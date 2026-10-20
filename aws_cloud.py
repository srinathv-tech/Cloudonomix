"""AWS connector (boto3): Cost Explorer billing, EC2 inventory + CloudWatch CPU,
stop/start with verification, and EC2 on-demand prices from the Price List API.

boto3 is imported lazily so the rest of the app still runs when AWS is not set up.
"""
import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from functools import lru_cache

import config
import db

# Pricing API "location" names (the API wants these, not region codes).
REGION_NAMES = {
    "us-east-1": "US East (N. Virginia)", "us-east-2": "US East (Ohio)",
    "us-west-1": "US West (N. California)", "us-west-2": "US West (Oregon)",
    "ap-south-1": "Asia Pacific (Mumbai)", "ap-south-2": "Asia Pacific (Hyderabad)",
    "ap-east-1": "Asia Pacific (Hong Kong)", "ap-southeast-1": "Asia Pacific (Singapore)",
    "ap-southeast-2": "Asia Pacific (Sydney)", "ap-southeast-5": "Asia Pacific (Malaysia)",
    "ap-northeast-1": "Asia Pacific (Tokyo)", "ap-northeast-2": "Asia Pacific (Seoul)",
    "eu-west-1": "EU (Ireland)", "eu-west-2": "EU (London)", "eu-west-3": "EU (Paris)",
    "eu-central-1": "EU (Frankfurt)", "eu-north-1": "EU (Stockholm)",
    "ca-central-1": "Canada (Central)", "sa-east-1": "South America (Sao Paulo)",
    "me-central-1": "Middle East (UAE)", "af-south-1": "Africa (Cape Town)",
}

# Closest AWS region for an Azure region (used to pre-fill the price comparison).
AZURE_TO_AWS_REGION = {
    # Only regions that are enabled in every AWS account by default. Hong Kong, Malaysia, Hyderabad,
    # UAE and Cape Town are opt-in, so Azure regions near them map to the closest default region.
    "centralindia": "ap-south-1", "southindia": "ap-south-1", "westindia": "ap-south-1",
    "eastasia": "ap-southeast-1", "southeastasia": "ap-southeast-1", "malaysiawest": "ap-southeast-1",
    "japaneast": "ap-northeast-1", "koreacentral": "ap-northeast-2",
    "australiaeast": "ap-southeast-2", "eastus": "us-east-1", "eastus2": "us-east-2",
    "westus": "us-west-1", "westus2": "us-west-2", "northeurope": "eu-west-1",
    "westeurope": "eu-central-1", "uksouth": "eu-west-2", "francecentral": "eu-west-3",
    "germanywestcentral": "eu-central-1", "swedencentral": "eu-north-1",
    "canadacentral": "ca-central-1", "brazilsouth": "sa-east-1", "uaenorth": "ap-south-1",
    "southafricanorth": "eu-west-1",
}


def _client(service, region=None):
    import boto3  # lazy: only needed once AWS is used

    return boto3.client(service, region_name=region or config.AWS_REGIONS[0])


def identity():
    """(account id, ARN) of the credentials in use. Raises if the keys are wrong."""
    r = _client("sts").get_caller_identity()
    return r["Account"], r["Arn"]


# ---------------------------------------------------------------- billing
def sync_billing(days=30):
    """Daily UnblendedCost by service from Cost Explorer -> `costs` table."""
    ce = _client("ce", "us-east-1")  # Cost Explorer is a global endpoint in us-east-1
    end = datetime.now(timezone.utc).date() + timedelta(days=1)  # End is exclusive
    start = end - timedelta(days=days)
    agg, currency, token = defaultdict(float), "USD", None
    while True:
        kw = dict(TimePeriod={"Start": start.isoformat(), "End": end.isoformat()},
                  Granularity="DAILY", Metrics=["UnblendedCost"],
                  GroupBy=[{"Type": "DIMENSION", "Key": "SERVICE"}])
        if token:
            kw["NextPageToken"] = token
        r = ce.get_cost_and_usage(**kw)
        for res in r["ResultsByTime"]:
            day = res["TimePeriod"]["Start"]
            for g in res["Groups"]:
                m = g["Metrics"]["UnblendedCost"]
                currency = m.get("Unit", currency)
                agg[(day, g["Keys"][0])] += float(m["Amount"])
        token = r.get("NextPageToken")
        if not token:
            break
    rows = [dict(date=d, cloud="aws", service=s, resource_group="(account)", cost=c,
                 currency=currency) for (d, s), c in agg.items() if c != 0]
    return db.upsert("costs", rows)


# ---------------------------------------------------------------- EC2
def _avg_cpu(cw, instance_id, hours):
    end = datetime.now(timezone.utc)
    r = cw.get_metric_statistics(
        Namespace="AWS/EC2", MetricName="CPUUtilization",
        Dimensions=[{"Name": "InstanceId", "Value": instance_id}],
        StartTime=end - timedelta(hours=hours), EndTime=end, Period=3600,
        Statistics=["Average"])
    vals = [p["Average"] for p in r.get("Datapoints", [])]
    return sum(vals) / len(vals) if vals else None


def scan(hours=24):
    """All EC2 instances (not terminated) in AWS_REGIONS with power state and average CPU."""
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows = []
    for region in config.AWS_REGIONS:
        ec2, cw = _client("ec2", region), _client("cloudwatch", region)
        for page in ec2.get_paginator("describe_instances").paginate():
            for res in page["Reservations"]:
                for i in res["Instances"]:
                    state = i["State"]["Name"]
                    if state in ("terminated", "shutting-down"):
                        continue
                    tags = {t["Key"]: t["Value"] for t in i.get("Tags", [])}
                    iid = i["InstanceId"]
                    cpu = _avg_cpu(cw, iid, hours) if state == "running" else None
                    rows.append(dict(ts=ts, cloud="aws", instance_id=iid,
                                     vm=tags.get("Name") or iid, resource_group="(aws)",
                                     size=i["InstanceType"], location=region,
                                     power_state=state, avg_cpu=cpu,
                                     tag=tags.get(config.HIBERNATE_TAG, "")))
    db.upsert("vm_metrics", rows)
    return rows


def _state(ec2, instance_id):
    r = ec2.describe_instances(InstanceIds=[instance_id])
    return r["Reservations"][0]["Instances"][0]["State"]["Name"]


def _wait(ec2, waiter_name, instance_id):
    from botocore.exceptions import WaiterError

    attempts = max(1, config.STOP_TIMEOUT_SECONDS // 5)
    try:
        ec2.get_waiter(waiter_name).wait(
            InstanceIds=[instance_id], WaiterConfig={"Delay": 5, "MaxAttempts": attempts})
    except WaiterError:
        pass  # report whatever state it is in below


def stop(row):
    """Stop one instance, wait, and return the state AWS reports afterwards."""
    ec2 = _client("ec2", row["location"])
    ec2.stop_instances(InstanceIds=[row["instance_id"]])
    _wait(ec2, "instance_stopped", row["instance_id"])
    return _state(ec2, row["instance_id"])


def start(row):
    ec2 = _client("ec2", row["location"])
    ec2.start_instances(InstanceIds=[row["instance_id"]])
    _wait(ec2, "instance_running", row["instance_id"])
    return _state(ec2, row["instance_id"])


# ---------------------------------------------------------------- pricing
@lru_cache(maxsize=256)
def instance_hourly_price(instance_type, region):
    """On-demand Linux price per hour in USD, or None if the Price List has no match."""
    location = REGION_NAMES.get(region)
    if not location:
        return None
    filters = [("instanceType", instance_type), ("location", location),
               ("operatingSystem", "Linux"), ("tenancy", "Shared"),
               ("preInstalledSw", "NA"), ("capacitystatus", "Used")]
    r = _client("pricing", "us-east-1").get_products(
        ServiceCode="AmazonEC2", MaxResults=20,
        Filters=[{"Type": "TERM_MATCH", "Field": f, "Value": v} for f, v in filters])
    best = None
    for item in r.get("PriceList", []):
        for term in json.loads(item)["terms"].get("OnDemand", {}).values():
            for dim in term["priceDimensions"].values():
                if dim.get("unit") != "Hrs":
                    continue
                v = float(dim["pricePerUnit"].get("USD", 0))
                if v > 0 and (best is None or v < best):
                    best = v
    return best


def candidates(vcpu, ram_gb, burstable, region, limit=8):
    """x86 current-generation instance types with the same vCPU count and similar RAM."""
    ec2 = _client("ec2", region)
    pages = ec2.get_paginator("describe_instance_types").paginate(Filters=[
        {"Name": "vcpu-info.default-vcpus", "Values": [str(int(vcpu))]},
        {"Name": "current-generation", "Values": ["true"]},
        {"Name": "processor-info.supported-architecture", "Values": ["x86_64"]}])
    out = []
    for page in pages:
        for t in page["InstanceTypes"]:
            mem = t["MemoryInfo"]["SizeInMiB"] / 1024
            if t.get("BareMetal") or not (ram_gb * 0.75 <= mem <= ram_gb * 2.1):
                continue
            if bool(t.get("BurstablePerformanceSupported")) != bool(burstable):
                continue
            out.append(dict(instance=t["InstanceType"], vcpu=int(vcpu), ram_gb=round(mem, 1)))
    out.sort(key=lambda x: (abs(x["ram_gb"] - ram_gb), x["instance"]))
    return out[:limit]
