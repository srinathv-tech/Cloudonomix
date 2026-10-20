"""Offline test suite: fake Azure / AWS / Streamlit clients, no network and no real cloud calls.
Run from the repo root:  python tests/test_offline.py"""
import json, os, sqlite3, sys, tempfile, types
from types import SimpleNamespace as N

from pathlib import Path
PROJECT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, PROJECT)
tmp = tempfile.mkdtemp()
os.environ["CLOUDONOMIX_DB"] = os.path.join(tmp, "t.db")
os.environ["AWS_ACCESS_KEY_ID"] = "x"
os.environ["AWS_SECRET_ACCESS_KEY"] = "y"
os.environ["AZURE_SUBSCRIPTION_ID"] = "sub"
os.environ["STOP_TIMEOUT_SECONDS"] = "10"
os.chdir(tmp)

# ------------------------------------------------------------------ old-schema DB migration
old = sqlite3.connect(os.environ["CLOUDONOMIX_DB"])
old.executescript("""
CREATE TABLE costs(date TEXT, service TEXT, resource_group TEXT, cost REAL, currency TEXT,
  PRIMARY KEY(date, service, resource_group));
CREATE TABLE resource_costs(date TEXT, resource_id TEXT, cost REAL, currency TEXT, PRIMARY KEY(date, resource_id));
CREATE TABLE vm_metrics(ts TEXT, vm TEXT, resource_group TEXT, size TEXT, location TEXT,
  power_state TEXT, avg_cpu REAL, tag TEXT, PRIMARY KEY(ts, vm));
CREATE TABLE actions(ts TEXT, vm TEXT, action TEXT, dry_run INTEGER, avg_cpu REAL, est_saving_per_day REAL, note TEXT);
INSERT INTO costs VALUES('2026-10-01','Virtual Machines','rg1',1.5,'INR');
INSERT INTO resource_costs VALUES('2026-10-01','/subscriptions/s/x/virtualmachines/a',1.5,'INR');
INSERT INTO vm_metrics VALUES('t1','Auditor','CLOUDSEC','Standard_B2ats_v2','malaysiawest','running',0.24,'');
INSERT INTO actions VALUES('t1','Auditor','WOULD_DEALLOCATE',1,0.24,0.1,'dry run');
""")
old.commit(); old.close()

import db
db.init()
c = db.read("SELECT * FROM costs"); assert list(c.cloud) == ["azure"] and c.cost[0] == 1.5, c
v = db.read("SELECT * FROM vm_metrics"); assert v.instance_id[0] == "Auditor" and v.cloud[0] == "azure", v
a = db.read("SELECT * FROM actions"); assert a.result[0] == "dry run" and a.cloud[0] == "azure", a
assert not [r for r in sqlite3.connect(os.environ["CLOUDONOMIX_DB"]).execute(
    "select name from sqlite_master where name like '%_old'")]
db.init()  # idempotent
print("PASS db migration (old Azure-only DB keeps its rows)")

# ------------------------------------------------------------------ fake boto3
AWS = {"states": {"i-111": "running", "i-222": "stopped"}, "stop_calls": [], "fail_stop": False,
       "stuck": False}

class WaiterError(Exception):
    pass

class Waiter:
    def __init__(self, name): self.name = name
    def wait(self, InstanceIds, WaiterConfig=None):
        if AWS["stuck"]:
            raise WaiterError("timeout")

class Pager:
    def __init__(self, pages): self.pages = pages
    def paginate(self, **kw): return iter(self.pages(kw))

class Client:
    def __init__(self, service, region): self.service, self.region = service, region
    # sts
    def get_caller_identity(self): return {"Account": "123456789012", "Arn": "arn:aws:iam::1:user/t"}
    # ce
    def get_cost_and_usage(self, **kw):
        assert kw["Granularity"] == "DAILY" and kw["GroupBy"][0]["Key"] == "SERVICE"
        if "NextPageToken" not in kw:
            return {"NextPageToken": "p2", "ResultsByTime": [{"TimePeriod": {"Start": "2026-10-17"}, "Groups": [
                {"Keys": ["Amazon EC2"], "Metrics": {"UnblendedCost": {"Amount": "1.25", "Unit": "USD"}}},
                {"Keys": ["AWS Support"], "Metrics": {"UnblendedCost": {"Amount": "0", "Unit": "USD"}}}]}]}
        return {"ResultsByTime": [{"TimePeriod": {"Start": "2026-10-18"}, "Groups": [
            {"Keys": ["Amazon EC2"], "Metrics": {"UnblendedCost": {"Amount": "1.50", "Unit": "USD"}}}]}]}
    # ec2
    def get_paginator(self, name):
        if name == "describe_instances":
            def pages(kw):
                yield {"Reservations": [{"Instances": [
                    {"InstanceId": "i-111", "InstanceType": "t3.micro", "State": {"Name": AWS["states"]["i-111"]},
                     "Tags": [{"Key": "Name", "Value": "web"}, {"Key": "autohibernate", "Value": "true"}]},
                    {"InstanceId": "i-222", "InstanceType": "t3.small", "State": {"Name": AWS["states"]["i-222"]}, "Tags": []},
                    {"InstanceId": "i-333", "InstanceType": "t3.small", "State": {"Name": "terminated"}}]}]}
            return Pager(pages)
        def pages(kw):
            names = {f["Name"]: f["Values"] for f in kw["Filters"]}
            assert names["vcpu-info.default-vcpus"] == ["2"] and names["processor-info.supported-architecture"] == ["x86_64"]
            T = lambda n, mib, burst, bm=False: {"InstanceType": n, "MemoryInfo": {"SizeInMiB": mib},
                                                  "BurstablePerformanceSupported": burst, "BareMetal": bm}
            yield {"InstanceTypes": [T("t3.micro", 1024, True), T("t3a.micro", 1024, True), T("t3.small", 2048, True),
                                     T("t3.large", 8192, True), T("m5.large", 8192, False), T("c5.large", 4096, False),
                                     T("t3.bm", 1024, True, True)]}
        return Pager(pages)
    def describe_instances(self, InstanceIds):
        return {"Reservations": [{"Instances": [{"State": {"Name": AWS["states"][InstanceIds[0]]}}]}]}
    def stop_instances(self, InstanceIds):
        if AWS["fail_stop"]:
            raise RuntimeError("UnauthorizedOperation: not allowed to stop")
        AWS["stop_calls"].append(InstanceIds[0])
        AWS["states"][InstanceIds[0]] = "running" if AWS["stuck"] else "stopped"
    def start_instances(self, InstanceIds): AWS["states"][InstanceIds[0]] = "running"
    def get_waiter(self, name): return Waiter(name)
    # cloudwatch
    def get_metric_statistics(self, **kw):
        assert kw["Namespace"] == "AWS/EC2" and kw["Period"] == 3600
        return {"Datapoints": [{"Average": 1.0}, {"Average": 3.0}]}
    # pricing
    def get_products(self, ServiceCode, Filters, MaxResults):
        f = {x["Field"]: x["Value"] for x in Filters}
        assert ServiceCode == "AmazonEC2" and f["operatingSystem"] == "Linux" and f["tenancy"] == "Shared"
        price = {"t3.micro": "0.0116", "t3a.micro": "0.0104", "t3.small": "0.0232"}.get(f["instanceType"])
        if f["location"] != "Asia Pacific (Mumbai)" or price is None:
            return {"PriceList": []}
        item = {"terms": {"OnDemand": {"k": {"priceDimensions": {"d": {"unit": "Hrs", "pricePerUnit": {"USD": price}}}}}}}
        return {"PriceList": [json.dumps(item)]}

boto3 = types.ModuleType("boto3"); boto3.client = lambda s, region_name=None: Client(s, region_name)
botocore = types.ModuleType("botocore"); exc = types.ModuleType("botocore.exceptions"); exc.WaiterError = WaiterError
sys.modules.update({"boto3": boto3, "botocore": botocore, "botocore.exceptions": exc})

import aws_cloud, config

assert aws_cloud.identity()[0] == "123456789012"
n = aws_cloud.sync_billing(5)
rows = db.read("SELECT * FROM costs WHERE cloud='aws' ORDER BY date")
assert n == 2 and list(rows.cost) == [1.25, 1.5] and set(rows.currency) == {"USD"}, rows  # page 2 read, zero row skipped
print("PASS aws billing (pagination, zero rows skipped)")

scanned = aws_cloud.scan(24)
by = {r["instance_id"]: r for r in scanned}
assert set(by) == {"i-111", "i-222"}  # terminated excluded
assert by["i-111"]["vm"] == "web" and by["i-111"]["avg_cpu"] == 2.0 and by["i-111"]["tag"] == "true"
assert by["i-222"]["avg_cpu"] is None and by["i-222"]["vm"] == "i-222"
print("PASS aws EC2 scan (name tag, CPU average, terminated skipped, stopped has no CPU)")

assert aws_cloud.instance_hourly_price("t3.micro", "ap-south-1") == 0.0116
assert aws_cloud.instance_hourly_price("t3.micro", "xx-fake-1") is None
c = aws_cloud.candidates(2, 1.0, True, "ap-south-1")
assert [x["instance"] for x in c] == ["t3.micro", "t3a.micro", "t3.small"], c  # no m5/c5, no large, no bare metal
print("PASS aws pricing + candidate matching:", [x["instance"] for x in c])

# ------------------------------------------------------------------ vms: stop paths (AWS)
import vms
row = by["i-111"]
dry = vms.stop_vm(row, True)
assert dry["action"] == "WOULD_STOP" and AWS["stop_calls"] == [] and dry["est_saving_per_day"] == round(0.0116 * 24, 4)
live = vms.stop_vm(row, False)
assert live["action"] == "STOPPED" and live["result"] == "verified: stopped" and AWS["stop_calls"] == ["i-111"], live
AWS["states"]["i-111"] = "running"; AWS["fail_stop"] = True
fail = vms.stop_vm(row, False)
assert fail["action"] == "FAILED" and "UnauthorizedOperation" in fail["result"], fail
AWS["fail_stop"] = False; AWS["stuck"] = True
stuck = vms.stop_vm(row, False)
assert stuck["action"] == "STOP_REQUESTED" and "running" in stuck["result"], stuck
AWS["stuck"] = False
st_ = vms.start_vm(by["i-222"])
assert st_["action"] == "STARTED", st_
print("PASS aws stop: dry run changes nothing / live verified / failure logged / unverified flagged / start")

# ------------------------------------------------------------------ vms: stop paths (Azure)
AZ = {"state": "running", "fail": False, "stuck": False, "calls": []}

class Poller:
    def wait(self, timeout=None): pass

class VMOps:
    def begin_deallocate(self, rg, name):
        if AZ["fail"]:
            raise RuntimeError("AuthorizationFailed: client lacks permission")
        AZ["calls"].append((rg, name))
        if not AZ["stuck"]:
            AZ["state"] = "deallocated"
        return Poller()
    def begin_start(self, rg, name): AZ["state"] = "running"; return Poller()
    def instance_view(self, rg, name):
        return N(statuses=[N(code="ProvisioningState/succeeded"), N(code=f"PowerState/{AZ['state']}")])

vms._azure_clients = lambda: (N(virtual_machines=VMOps()), None)
az_row = dict(cloud="azure", vm="Auditor", resource_group="CLOUDSEC", size="Standard_B2ats_v2",
              location="malaysiawest", avg_cpu=0.24, instance_id="/subscriptions/s/.../Auditor")
vms.price_per_hour = lambda r: 0.01
assert vms.stop_vm(az_row, True)["action"] == "WOULD_STOP" and AZ["calls"] == []
r = vms.stop_vm(az_row, False)
assert r["action"] == "STOPPED" and r["result"] == "verified: deallocated" and AZ["calls"] == [("CLOUDSEC", "Auditor")], r
AZ["state"] = "running"; AZ["fail"] = True
r = vms.stop_vm(az_row, False); assert r["action"] == "FAILED" and "AuthorizationFailed" in r["result"], r
AZ["fail"] = False; AZ["stuck"] = True; AZ["state"] = "running"
r = vms.stop_vm(az_row, False); assert r["action"] == "STOP_REQUESTED", r
print("PASS azure stop: dry run / verified deallocated / permission error logged / unverified flagged")

# ------------------------------------------------------------------ autohibernate + suggestions
AZ.update(stuck=False, state="running", calls=[])
recs = [
    dict(az_row, power_state="running", tag="true"),                                   # idle + tagged -> stop
    dict(az_row, vm="SIEM", instance_id="s", power_state="running", tag="", avg_cpu=0.2),  # idle, untagged -> never auto
    dict(az_row, vm="busy", instance_id="b", power_state="running", tag="true", avg_cpu=60.0),
    dict(az_row, vm="nodata", instance_id="n", power_state="running", tag="true", avg_cpu=float("nan")),
    dict(az_row, vm="off", instance_id="o", power_state="deallocated", tag="true", avg_cpu=None),
]
log = vms.autohibernate(recs, 5.0, dry_run=False)
assert [(e["vm"], e["action"]) for e in log] == [("Auditor", "STOPPED"), ("nodata", "SKIP")], log
assert AZ["calls"] == [("CLOUDSEC", "Auditor")]            # untagged SIEM and busy VM untouched
sg = vms.suggest(recs, 5.0)
assert sorted(x["vm"] for x in sg) == ["Auditor", "SIEM"], sg   # suggestions include untagged, exclude NaN/busy/off
print("PASS autohibernate: only idle+tagged stopped, NaN CPU skipped, suggestions include untagged")
acts = db.read("SELECT action FROM actions WHERE cloud='azure' ORDER BY ts")
assert "STOPPED" in set(acts.action)

# ------------------------------------------------------------------ cross-cloud comparison
import pricing
sku = lambda name, vcpu, mem: N(resource_type="virtualMachines", name=name,
                                capabilities=[N(name="vCPUs", value=str(vcpu)), N(name="MemoryGB", value=str(mem))])
class SkuOps:
    def list(self, filter): assert filter == "location eq 'centralindia'"; return [sku("Standard_D2s_v5", 2, 8), sku("Standard_B2ats_v2", 2, 1)]
class FakeCompute:
    def __init__(self, cred, sub): self.resource_skus = SkuOps()
for m in ("azure", "azure.mgmt", "azure.mgmt.compute"):
    sys.modules.setdefault(m, types.ModuleType(m))
sys.modules["azure.mgmt.compute"].ComputeManagementClient = FakeCompute
config.get_credential = lambda: None
pricing.vm_hourly_price = lambda size, region, currency="USD": 0.0092
df, warns = pricing.cross_cloud("Standard_B2ats_v2", "centralindia")
print(df.to_string())
assert not warns, warns
assert list(df.instance) == ["Standard_B2ats_v2", "t3a.micro", "t3.micro", "t3.small"], list(df.instance)
az = df[df.cloud == "Azure"].iloc[0]
assert az["vcpu"] == 2 and az["ram_gb"] == 1.0
assert df.iloc[1]["vs_azure_%"] == round((0.0104 / 0.0092 - 1) * 100, 1)
df2, w2 = pricing.cross_cloud("Standard_B2ats_v2", "centralindia", "xx-fake-1")   # AWS region without prices
import pandas as pd
assert all(pd.isna(p) for p in df2[df2.cloud == "AWS"].price_per_hour)
try:
    pricing.cross_cloud("Standard_NOPE", "centralindia"); raise SystemExit("should have raised")
except ValueError as e:
    assert "not found" in str(e)
boto3.client = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no AWS credentials"))
df3, w3 = pricing.cross_cloud("Standard_B2ats_v2", "centralindia")
assert len(df3) == 1 and any("AWS lookup failed" in w for w in w3), w3     # Azure still shown when AWS is not set up
boto3.client = lambda s, region_name=None: Client(s, region_name)
print("PASS cross-cloud comparison: matching, ranking, % vs Azure, missing size, AWS down degrades gracefully")

# ------------------------------------------------------------------ Streamlit smoke test of app.py
class Ctx:
    def __enter__(self): return self
    def __exit__(self, *a): return False

class Col(Ctx):
    def __init__(self, fake): self._fake = fake
    def __getattr__(self, name): return self._fake.__getattr__(name)

class FakeSt:
    def __init__(self, page, clicked, state, dry):
        self.session_state, self.page, self.clicked, self.dry = state, page, set(clicked), dry
        self.errors, self.successes, self.select = [], [], {}
    def __getattr__(self, name):
        def f(*a, **k):
            if name == "button": return (k.get("key") or a[0]) in self.clicked
            if name == "checkbox": return self.dry if "Dry run" in a[0] else True
            if name == "slider": return a[3]
            if name == "selectbox": return self.select.get(a[0], a[1][0])
            if name == "text_input": return a[1] if len(a) > 1 else ""
            if name == "radio": return self.page
            if name == "columns": return [Col(self) for _ in range(a[0] if isinstance(a[0], int) else len(a[0]))]
            if name in ("spinner", "expander", "container", "popover"): return Ctx()
            if name == "rerun": raise RuntimeError("RERUN")
            if name == "error": self.errors.append(a[0])
            if name == "success": self.successes.append(a[0])
            return None
        return f

for m in ("azure.core", "azure.core.exceptions", "azure.mgmt.costmanagement", "azure.mgmt.costmanagement.models"):
    sys.modules.setdefault(m, types.ModuleType(m))
sys.modules["azure.core.exceptions"].HttpResponseError = Exception
for n in ("QueryAggregation", "QueryDataset", "QueryDefinition", "QueryGrouping", "QueryTimePeriod"):
    setattr(sys.modules["azure.mgmt.costmanagement.models"], n, object)
sys.modules["azure.mgmt.costmanagement"].CostManagementClient = object
sys.modules["azure.mgmt.monitor"] = types.ModuleType("azure.mgmt.monitor"); sys.modules["azure.mgmt.monitor"].MonitorManagementClient = object

def run_app(page="Overview", clicked=(), state=None, dry=True):
    state = {} if state is None else state
    fake = FakeSt(page, clicked, state, dry)
    mod = types.ModuleType("streamlit"); mod.__getattr__ = fake.__getattr__
    mod.session_state = state; mod.sidebar = Ctx()
    sys.modules["streamlit"] = mod
    exec(compile(open(PROJECT + "/app.py").read(), "app.py", "exec"), {"__name__": "__main__"})
    return fake

vms.scan_azure = lambda hours=24: []
db.upsert("vm_metrics", [dict(ts="2026-10-19T10:00:00+00:00", cloud="azure", instance_id="/subscriptions/s/x/virtualmachines/Auditor",
    vm="Auditor", resource_group="CLOUDSEC", size="Standard_B2ats_v2", location="centralindia", power_state="running", avg_cpu=0.2, tag="true"),
    dict(ts="2026-10-19T10:00:00+00:00", cloud="azure", instance_id="/subscriptions/s/x/virtualmachines/off",
    vm="off", resource_group="CLOUDSEC", size="Standard_B2ats_v2", location="centralindia", power_state="deallocated", avg_cpu=None, tag="")])
db.upsert("vm_metrics", [dict(ts="2026-10-19T10:00:00+00:00", cloud="aws", instance_id="i-111", vm="web", resource_group="(aws)",
    size="t3.micro", location="ap-south-1", power_state="running", avg_cpu=1.0, tag="true")])

PAGES = ["Overview", "VMs & AutoHibernate", "Cross-Cloud Price Comparator",
         "Anomaly Alerts", "Cost Split Analyzer", "Auto Optimization Assistant"]
shared = {}
for pg in PAGES:                        # every page renders with data from both clouds
    run_app(pg, state=shared)
assert shared["cfg"]["days"] == 30 and shared["cfg"]["idle"] == 5.0
print("PASS all 6 pages render (settings shared across pages)")

for b in ["scan_azure", "bill_azure", "scan_aws", "bill_aws", "check_conn"]:
    run_app("Overview", [b], state={})
for pg, btns in {"Cost Split Analyzer": ["Fetch tag costs"],
                 "Cross-Cloud Price Comparator": ["Compare Azure vs AWS", "Compare Azure regions"]}.items():
    for b in btns:
        run_app(pg, [b], state={})
print("PASS Overview / Cost Split / Price Comparator buttons run without crashing")

# a disabled GCP button must never be treated as a real action
run_app("Overview", ["scan_gcp", "bill_gcp"], state={})

# cost filter + a chart with real cost rows
db.upsert("costs", [dict(date=f"2026-10-{d:02d}", cloud=c, service="Compute", resource_group="rg", cost=1.0 + d / 10, currency=cur)
                    for d in range(1, 12) for c, cur in (("azure", "INR"), ("aws", "USD"))])
run_app("Overview", state={})
fk = FakeSt("Overview", [], {}, True); fk.select["Cloud"] = "azure"
mod = types.ModuleType("streamlit"); mod.__getattr__ = fk.__getattr__; mod.session_state = fk.session_state; mod.sidebar = Ctx()
sys.modules["streamlit"] = mod
exec(compile(open(PROJECT + "/app.py").read(), "app.py", "exec"), {"__name__": "__main__"})
run_app("Anomaly Alerts", state={}); run_app("Auto Optimization Assistant", state={}); run_app("Cost Split Analyzer", state={})
print("PASS cost filter (All / single cloud) and pages with real cost rows")

# Auto Optimization Assistant content
import pandas as pd
recs_vm = vms.suggest(db.read("SELECT * FROM vm_metrics").to_dict("records"), 5.0)
assert {x["vm"] for x in recs_vm} >= {"Auditor", "web"}, recs_vm
print("PASS optimization assistant sees idle VMs from both clouds")

# VM page: dry run logs only, live run really stops and logs STOPPED
before = len(AZ["calls"])
st1 = {}
try:
    run_app("VMs & AutoHibernate", ["Stop selected VM"], st1, dry=True)
except RuntimeError as e:
    assert str(e) == "RERUN"
assert st1["flash"].startswith("WOULD_STOP") and len(AZ["calls"]) == before, st1
st2 = {}
try:
    run_app("VMs & AutoHibernate", ["Stop selected VM"], st2, dry=False)
except RuntimeError as e:
    assert str(e) == "RERUN"
assert st2["flash"].startswith("STOPPED") and len(AZ["calls"]) == before + 1, st2
for btn in ("Run AutoHibernate", "Start selected VM"):
    for dry in (True, False):
        try:
            run_app("VMs & AutoHibernate", [btn], {}, dry=dry)
        except RuntimeError as e:
            assert str(e) == "RERUN"
print("PASS VM page: dry run changes nothing, live run stops VM and shows STOPPED, action log shown inline")

# ------------------------------------------------------------------ AWS region sweep
import pandas as pd
sweep = pricing.aws_region_sweep("t3.micro")
assert list(sweep.region) == ["ap-south-1"] and sweep.price_per_hour[0] == 0.0116 and sweep["vs_cheapest_%"][0] == 0.0, sweep
orig = aws_cloud.instance_hourly_price
aws_cloud.instance_hourly_price = lambda i, r: {"ap-south-1": 0.0116, "us-east-1": 0.0104, "eu-west-1": 0.0114}.get(r)
sw = pricing.aws_region_sweep("t3.micro")
assert list(sw.region) == ["us-east-1", "eu-west-1", "ap-south-1"] and sw["vs_cheapest_%"].iloc[-1] == round((0.0116 / 0.0104 - 1) * 100, 1), sw
aws_cloud.instance_hourly_price = lambda i, r: (_ for _ in ()).throw(RuntimeError("boom"))
assert pricing.aws_region_sweep("t3.micro").empty          # an API failure never crashes the page
aws_cloud.instance_hourly_price = orig
print("PASS aws region sweep: sorted cheapest first, % vs cheapest, failures tolerated")

# comparator page with a stored comparison + the sweep button
cmp_state = {"cmp": (pd.DataFrame([dict(cloud="Azure", instance="Standard_B2ats_v2", region="centralindia", vcpu=2, ram_gb=1.0, price_per_hour=0.0092, price_per_month_730h=6.72, **{"vs_azure_%": 0.0}),
                                    dict(cloud="AWS", instance="t3.micro", region="ap-south-1", vcpu=2, ram_gb=1.0, price_per_hour=0.0116, price_per_month_730h=8.47, **{"vs_azure_%": 26.1})]), ["a warning"])}
run_app("Cross-Cloud Price Comparator", state=dict(cmp_state))
run_app("Cross-Cloud Price Comparator", ["Compare this instance across all AWS regions"], state=dict(cmp_state))
run_app("Cross-Cloud Price Comparator", ["Compare Azure vs AWS"], state={})
print("PASS comparator page: stored comparison renders, sweep button works, compare button works")

# cost chart: mixed currencies -> separate charts, same currency -> one combined chart
class Rec(FakeSt):
    def __init__(self, *a, **k):
        FakeSt.__init__(self, *a, **k)
        self.charts = []
    def __getattr__(self, name):
        f = FakeSt.__getattr__(self, name)
        def g(*a, **k):
            if name == "line_chart": self.charts.append(list(a[0].columns))
            return f(*a, **k)
        return g
def overview_charts(select="All"):
    fk = Rec("Overview", [], {}, True); fk.select["Cloud"] = select
    mod = types.ModuleType("streamlit"); mod.__getattr__ = fk.__getattr__; mod.session_state = fk.session_state; mod.sidebar = Ctx()
    sys.modules["streamlit"] = mod
    exec(compile(open(PROJECT + "/app.py").read(), "app.py", "exec"), {"__name__": "__main__"})
    return fk.charts
mixed = overview_charts("All")
assert len(mixed) == 2 and not any(str(c).startswith(("AZURE:", "AWS:")) for ch in mixed for c in ch), mixed   # one chart per cloud, plain service names
assert len(overview_charts("azure")) == 1 and len(overview_charts("aws")) == 1
import sqlite3 as _sq
_c = _sq.connect(os.environ["CLOUDONOMIX_DB"]); _c.execute("UPDATE costs SET currency='USD'"); _c.commit(); _c.close()
same = overview_charts("All")
assert len(same) == 1 and {"AWS: Compute", "AZURE: Compute"} <= set(same[0]), same   # one combined chart, prefixed names
print("PASS cost chart: different currencies get separate charts, same currency gets one combined chart, single-cloud filter works")
