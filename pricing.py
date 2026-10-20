"""Live price comparison.

Azure: public Retail Prices API (no auth). AWS: Price List API via boto3 (needs the
pricing:GetProducts permission). Azure vCPU/RAM come from the Compute SKUs API, and
AWS instance types are matched by vCPU count and similar RAM.
"""
import os
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache

import pandas as pd
import requests

import aws_cloud

API = "https://prices.azure.com/api/retail/prices"


def _fetch(flt, currency="USD", max_pages=8):
    items, url, params = [], API, {"$filter": flt, "currencyCode": currency}
    for _ in range(max_pages):
        r = requests.get(url, params=params, timeout=30)
        r.raise_for_status()
        data = r.json()
        items += data.get("Items", [])
        url, params = data.get("NextPageLink"), None
        if not url:
            break
    return items


def _linux_ondemand(i):
    name = (i.get("productName", "") + i.get("skuName", "")).lower()
    return "windows" not in name and "spot" not in name and "low priority" not in name


@lru_cache(maxsize=256)
def vm_hourly_price(size, region, currency="USD"):
    flt = (f"serviceName eq 'Virtual Machines' and armRegionName eq '{region}' "
           f"and armSkuName eq '{size}' and priceType eq 'Consumption'")
    prices = [i["retailPrice"] for i in _fetch(flt, currency, 2)
              if _linux_ondemand(i) and i["retailPrice"] > 0]
    return min(prices) if prices else None


def compare_regions(size, currency="USD"):
    flt = (f"serviceName eq 'Virtual Machines' and armSkuName eq '{size}' "
           f"and priceType eq 'Consumption'")
    rows = {}
    for i in _fetch(flt, currency):
        if _linux_ondemand(i) and i["retailPrice"] > 0:
            r = i["armRegionName"]
            rows[r] = min(rows.get(r, 1e9), i["retailPrice"])
    df = pd.DataFrame(sorted(rows.items(), key=lambda x: x[1]), columns=["region", "price_per_hour"])
    df["price_per_month_730h"] = (df["price_per_hour"] * 730).round(2)
    return df


@lru_cache(maxsize=64)
def azure_size_specs(size, region):
    """{'vcpu': int, 'ram_gb': float} for an Azure VM size, or None if not offered there."""
    import config
    from azure.mgmt.compute import ComputeManagementClient

    compute = ComputeManagementClient(config.get_credential(), config.SUBSCRIPTION_ID)
    for sku in compute.resource_skus.list(filter=f"location eq '{region}'"):
        if sku.resource_type == "virtualMachines" and sku.name.lower() == size.lower():
            caps = {c.name: c.value for c in (sku.capabilities or [])}
            return dict(vcpu=int(float(caps["vCPUs"])), ram_gb=float(caps["MemoryGB"]))
    return None


def cross_cloud(size, azure_region, aws_region=None):
    """Compare one Azure size with matching AWS instances. Returns (DataFrame, warnings)."""
    warnings = []
    specs = azure_size_specs(size, azure_region)
    if not specs:
        raise ValueError(f"{size} was not found in {azure_region}. Check the size and region names.")
    burstable = size.lower().startswith("standard_b")
    azure_price = vm_hourly_price(size, azure_region)
    rows = [dict(cloud="Azure", instance=size, region=azure_region, vcpu=specs["vcpu"],
                 ram_gb=specs["ram_gb"], price_per_hour=azure_price)]
    if azure_price is None:
        warnings.append("No Azure retail price found for that size and region.")

    aws_region = aws_region or aws_cloud.AZURE_TO_AWS_REGION.get(azure_region)
    if not aws_region:
        warnings.append("No AWS region mapped for that Azure region. Pick one manually.")
    else:
        try:
            cands = aws_cloud.candidates(specs["vcpu"], specs["ram_gb"], burstable, aws_region)
            if not cands:
                warnings.append(f"No AWS instance types matched {specs['vcpu']} vCPU / "
                                f"~{specs['ram_gb']} GB in {aws_region}.")
            for c in cands:
                rows.append(dict(cloud="AWS", instance=c["instance"], region=aws_region,
                                 vcpu=c["vcpu"], ram_gb=c["ram_gb"],
                                 price_per_hour=aws_cloud.instance_hourly_price(c["instance"], aws_region)))
        except Exception as e:
            msg = str(e)
            if "AuthFailure" in msg or "not able to validate" in msg:
                warnings.append(
                    f"AWS rejected the request in region {aws_region}. That region is probably not enabled "
                    "in your AWS account (Malaysia, Hong Kong, UAE and some others are off by default). "
                    "Type an enabled region such as ap-south-1 in the AWS region box, or enable it under "
                    "AWS Account > AWS Regions. If ap-south-1 also fails, check your access keys in .env.")
            else:
                warnings.append(f"AWS lookup failed: {e}")

    df = pd.DataFrame(rows)
    df["price_per_month_730h"] = (df["price_per_hour"] * 730).round(2)
    if azure_price:
        df["vs_azure_%"] = ((df["price_per_hour"] / azure_price - 1) * 100).round(1)
    return df.sort_values("price_per_hour", na_position="last").reset_index(drop=True), warnings


def aws_region_sweep(instance_type):
    """Price of one AWS instance type in every region the Price List knows. Cheapest first."""
    def one(region):
        try:
            return region, aws_cloud.instance_hourly_price(instance_type, region)
        except Exception:
            return region, None

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(one, aws_cloud.REGION_NAMES))
    rows = [dict(region=r, location=aws_cloud.REGION_NAMES[r], price_per_hour=p)
            for r, p in results if p]
    df = pd.DataFrame(rows, columns=["region", "location", "price_per_hour"])
    if df.empty:
        return df
    df["price_per_month_730h"] = (df["price_per_hour"] * 730).round(2)
    cheapest = df["price_per_hour"].min()
    df["vs_cheapest_%"] = ((df["price_per_hour"] / cheapest - 1) * 100).round(1)
    return df.sort_values("price_per_hour").reset_index(drop=True)


def load_reference(path="reference_prices.csv"):
    """Optional: your own cross-cloud table (cloud,instance,vcpu,ram_gb,price_per_hour)."""
    if not os.path.exists(path):
        return None
    df = pd.read_csv(path)
    return df if not df.empty else None
