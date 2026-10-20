"""VM inventory, CPU metrics and AutoHibernate for Azure and AWS.

Stopping a VM waits for the cloud to finish, re-reads the real power state and logs
the outcome (including failures), so the Action log shows what actually happened.
"""
from datetime import datetime, timedelta, timezone

import pandas as pd

import aws_cloud
import config
import db
import pricing

STOPPED_STATES = {"azure": "deallocated", "aws": "stopped"}


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _num(v):
    return None if v is None or pd.isna(v) else float(v)


def _cloud(row):
    return row.get("cloud") or "azure"


# ---------------------------------------------------------------- Azure
def _azure_clients():
    from azure.mgmt.compute import ComputeManagementClient
    from azure.mgmt.monitor import MonitorManagementClient

    cred = config.get_credential()
    return (ComputeManagementClient(cred, config.SUBSCRIPTION_ID),
            MonitorManagementClient(cred, config.SUBSCRIPTION_ID))


def _azure_avg_cpu(monitor, resource_id, hours):
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=hours)
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    res = monitor.metrics.list(
        resource_id, timespan=f"{start.strftime(fmt)}/{end.strftime(fmt)}",
        interval="PT1H", metricnames="Percentage CPU", aggregation="Average")
    vals = [d.average for m in res.value for ts in m.timeseries
            for d in ts.data if d.average is not None]
    return sum(vals) / len(vals) if vals else None


def _azure_power_state(compute, rg, name):
    iv = compute.virtual_machines.instance_view(rg, name)
    return next((s.code.split("/", 1)[1] for s in iv.statuses
                 if s.code.startswith("PowerState/")), "unknown")


def scan_azure(hours=24):
    compute, monitor = _azure_clients()
    ts = _now()
    rows = []
    for vm in compute.virtual_machines.list_all():
        rg = vm.id.split("/")[4]
        power = _azure_power_state(compute, rg, vm.name)
        cpu = _azure_avg_cpu(monitor, vm.id, hours) if power == "running" else None
        rows.append(dict(ts=ts, cloud="azure", instance_id=vm.id, vm=vm.name, resource_group=rg,
                         size=vm.hardware_profile.vm_size, location=vm.location,
                         power_state=power, avg_cpu=cpu,
                         tag=(vm.tags or {}).get(config.HIBERNATE_TAG, "")))
    db.upsert("vm_metrics", rows)
    return rows


def _azure_stop(row):
    compute, _ = _azure_clients()
    poller = compute.virtual_machines.begin_deallocate(row["resource_group"], row["vm"])
    poller.wait(config.STOP_TIMEOUT_SECONDS)
    return _azure_power_state(compute, row["resource_group"], row["vm"])


def _azure_start(row):
    compute, _ = _azure_clients()
    poller = compute.virtual_machines.begin_start(row["resource_group"], row["vm"])
    poller.wait(config.STOP_TIMEOUT_SECONDS)
    return _azure_power_state(compute, row["resource_group"], row["vm"])


# ---------------------------------------------------------------- shared
def scan(cloud, hours=24):
    return scan_azure(hours) if cloud == "azure" else aws_cloud.scan(hours)


def price_per_hour(row):
    try:
        if _cloud(row) == "aws":
            return aws_cloud.instance_hourly_price(row["size"], row["location"])
        return pricing.vm_hourly_price(row["size"], row["location"])
    except Exception:
        return None


def _log(row, action, dry_run, result, saving=None):
    entry = dict(ts=_now(), cloud=_cloud(row), vm=row["vm"], action=action,
                 dry_run=int(dry_run), avg_cpu=_num(row.get("avg_cpu")),
                 est_saving_per_day=saving, result=result)
    db.upsert("actions", [entry])
    return entry


def stop_vm(row, dry_run=True):
    """Stop (Azure: deallocate) one VM and log what happened. Never raises."""
    hourly = price_per_hour(row)
    saving = round(hourly * 24, 4) if hourly else None
    if dry_run:
        return _log(row, "WOULD_STOP", dry_run, "dry run - nothing was changed", saving)
    cloud = _cloud(row)
    try:
        state = _azure_stop(row) if cloud == "azure" else aws_cloud.stop(row)
    except Exception as e:
        return _log(row, "FAILED", dry_run, str(e)[:300], saving)
    if state == STOPPED_STATES[cloud]:
        return _log(row, "STOPPED", dry_run, f"verified: {state}", saving)
    return _log(row, "STOP_REQUESTED", dry_run,
                f"requested, but state is '{state}' - rescan in a minute", saving)


def start_vm(row):
    """Start a stopped VM and log it. Never raises."""
    cloud = _cloud(row)
    try:
        state = _azure_start(row) if cloud == "azure" else aws_cloud.start(row)
    except Exception as e:
        return _log(row, "FAILED", False, str(e)[:300])
    return _log(row, "STARTED" if state == "running" else "START_REQUESTED", False,
                f"state: {state}")


def autohibernate(rows, cpu_threshold, dry_run=True):
    """Stop running VMs tagged <tag>=true whose average CPU is below the threshold."""
    log = []
    for r in rows:
        if str(r.get("tag")).lower() != "true" or r["power_state"] != "running":
            continue
        cpu = _num(r.get("avg_cpu"))
        if cpu is None:
            log.append(_log(r, "SKIP", dry_run, "no CPU data yet"))
        elif cpu < cpu_threshold:
            log.append(stop_vm(r, dry_run))
    return log


def suggest(rows, cpu_threshold):
    """Running VMs below the CPU threshold, tagged or not, with estimated daily saving."""
    out = []
    for r in rows:
        cpu = _num(r.get("avg_cpu"))
        if r["power_state"] != "running" or cpu is None or cpu >= cpu_threshold:
            continue
        hourly = price_per_hour(r)
        out.append(dict(cloud=_cloud(r), vm=r["vm"], instance_id=r.get("instance_id"),
                        resource_group=r.get("resource_group"), size=r["size"],
                        location=r["location"], avg_cpu=round(cpu, 2), tag=r.get("tag", ""),
                        est_saving_per_day=round(hourly * 24, 4) if hourly else None))
    return out
