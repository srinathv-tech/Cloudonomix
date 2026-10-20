"""Real billing data from Azure Cost Management (ActualCost, daily)."""
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import pandas as pd
from azure.core.exceptions import HttpResponseError
from azure.mgmt.costmanagement import CostManagementClient
from azure.mgmt.costmanagement.models import (
    QueryAggregation, QueryDataset, QueryDefinition, QueryGrouping, QueryTimePeriod,
)

import config
import db


def _find(row, *needles):
    for k, v in row.items():
        if any(n in k for n in needles):
            return v
    return None


def _to_date(v):
    s = str(v)
    return datetime.strptime(s, "%Y%m%d").date().isoformat() if s.isdigit() else s[:10]


def normalize(result):
    """Turn a Cost Management QueryResult into a list of lower-case-keyed dicts."""
    cols = [c.name.lower() for c in result.columns]
    return [dict(zip(cols, r)) for r in result.rows]


def _query(groupings, days):
    client = CostManagementClient(config.get_credential())
    scope = f"/subscriptions/{config.SUBSCRIPTION_ID}"
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    definition = QueryDefinition(
        type="ActualCost",
        timeframe="Custom",
        time_period=QueryTimePeriod(from_property=start, to=end),
        dataset=QueryDataset(
            granularity="Daily",
            aggregation={"totalCost": QueryAggregation(name="Cost", function="Sum")},
            grouping=[QueryGrouping(type=t, name=n) for t, n in groupings],
        ),
    )
    for attempt in range(5):
        try:
            return normalize(client.query.usage(scope, definition))
        except HttpResponseError as e:
            if e.status_code == 429 and attempt < 4:
                wait = 2 ** attempt * 5
                try:
                    wait = int(e.response.headers.get(
                        "x-ms-ratelimit-microsoft.costmanagement-entity-retry-after", wait))
                except Exception:
                    pass
                time.sleep(min(wait, 60))
            else:
                raise


def sync(days=30):
    """Pull daily cost by service+resource group and by resource; store in SQLite."""
    n = 0
    agg = defaultdict(float)
    cur = "USD"
    for r in _query([("Dimension", "ServiceName"), ("Dimension", "ResourceGroupName")], days):
        cost = _find(r, "cost")
        if cost is None:
            continue
        cur = _find(r, "currency") or cur
        key = (_to_date(_find(r, "usagedate")), _find(r, "servicename") or "(unknown)",
               _find(r, "resourcegroup") or "(none)")
        agg[key] += float(cost)
    n += db.upsert("costs", [
        dict(date=d, cloud="azure", service=s, resource_group=g, cost=c, currency=cur)
        for (d, s, g), c in agg.items()])

    agg = defaultdict(float)
    for r in _query([("Dimension", "ResourceId")], days):
        cost = _find(r, "cost")
        rid = _find(r, "resourceid")
        if cost is None or not rid:
            continue
        agg[(_to_date(_find(r, "usagedate")), str(rid).lower())] += float(cost)
    n += db.upsert("resource_costs", [
        dict(date=d, cloud="azure", resource_id=rid, cost=c, currency=cur)
        for (d, rid), c in agg.items()])
    return n


def fetch_tag_costs(tag_key, days=30):
    """Cost allocation by an Azure tag key (e.g. team, project). Not stored."""
    agg = defaultdict(float)
    for r in _query([("TagKey", tag_key)], days):
        cost = _find(r, "cost")
        if cost is None:
            continue
        agg[_find(r, "tagvalue") or "(untagged)"] += float(cost)
    return (pd.DataFrame(sorted(agg.items(), key=lambda x: -x[1]), columns=["tag_value", "cost"]))
