# Cloudonomix

A multi-cloud cost optimization dashboard. It connects to your **Azure** and **AWS** accounts, shows your
real billing and virtual machines in one place, finds cost spikes, compares prices across clouds, and can
**automatically stop (hibernate) idle VMs** to save money.

Built with Python and Streamlit. All data stays on your machine in a local SQLite file.

## Features

| Page | What it does |
|---|---|
| **Overview** | Connect to each cloud, scan VMs, sync billing, see a fleet snapshot (VMs, running, stopped, idle) and daily cost charts per cloud |
| **VMs & AutoHibernate** | List VMs from both clouds with CPU usage, get idle-VM suggestions, stop or start VMs, run AutoHibernate, view the action log |
| **Cross-Cloud Price Comparator** | Compare an Azure VM size with matching AWS instances (same vCPU, similar RAM) using live list prices, and sweep one AWS instance across all regions |
| **Anomaly Alerts** | Flags days where a service's cost jumps far above its recent baseline |
| **Cost Split Analyzer** | Cost by resource group (Azure) and by Azure tag |
| **Auto Optimization Assistant** | Rule-based recommendations: idle VMs to stop, VMs to tag, recent cost spikes, with estimated savings |

GCP appears on the Overview page as a placeholder and is not implemented yet.

## How it works

```
Azure APIs  ─┐                                   ┌─ Overview / charts
             ├─►  connectors  ─►  SQLite DB  ─►  ├─ Anomaly detection
AWS APIs    ─┘  (billing, VMs,   (cloudonomix.db)├─ Price comparison
                 CPU metrics)                    └─ AutoHibernate (stop/start via the cloud APIs)
```

- **Billing:** Azure Cost Management API and AWS Cost Explorer, daily cost by service.
- **VMs and CPU:** Azure Compute + Azure Monitor, and AWS EC2 + CloudWatch. CPU is averaged over a lookback window.
- **Prices:** Azure Retail Prices API (public) and the AWS Price List API.
- **Data is a saved snapshot.** Click the Scan / Sync buttons on Overview to refresh it.

## Requirements

- Python 3.10 or newer
- An Azure subscription and/or an AWS account
- For Azure login: the [Azure CLI](https://learn.microsoft.com/cli/azure/install-azure-cli) (or a service principal, see below)

## Quick start

```bash
# 1. get the code
git clone <your-repo-url>
cd cloudonomix

# 2. create and activate a virtual environment
python -m venv venv
venv\Scripts\activate            # Windows (Command Prompt)
# source venv/bin/activate       # macOS / Linux

# 3. install dependencies
pip install -r requirements.txt

# 4. create your settings file
copy .env.example .env           # Windows
# cp .env.example .env           # macOS / Linux
```

Then edit `.env` (see the Azure and AWS sections below), log in to Azure, and start the app:

```bash
az login
streamlit run app.py
```

The dashboard opens at <http://localhost:8501>. The first start can take up to a minute.
If `streamlit` is not recognized, run `python -m streamlit run app.py`.

On the **Overview** page: click **Check connections**, then **Scan VMs** and **Sync billing** for each cloud.

## Azure setup

Put your subscription ID in `.env`: `AZURE_SUBSCRIPTION_ID=...` (Portal → Subscriptions).

Choose **one** way to sign in:

**Option A: Azure CLI login (simplest).** Leave the three service-principal values empty and run
`az login`. If it shows no subscription, run `az login --tenant <your-tenant-id-or-domain>`.

**Option B: service principal (like an AWS IAM user).**
1. Entra ID → App registrations → New registration. Copy the Application (client) ID and Directory (tenant) ID.
2. Certificates & secrets → New client secret. Copy the **Value** immediately.
3. On your subscription → Access control (IAM), assign these roles to the app (one at a time in the
   portal, or with `az role assignment create --assignee <client-id> --role "<role>" --scope /subscriptions/<id>`):
   - **Reader** (list VMs, read metrics)
   - **Cost Management Reader** (read billing)
   - **Virtual Machine Contributor** (stop and start VMs)
4. Fill `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET` in `.env`.

If all three service-principal values are set, they are used. Otherwise the CLI login is used.

## AWS setup (optional)

1. IAM → Policies → Create policy → JSON tab → paste the contents of `aws_iam_policy.json`.
   It allows read-only access to billing, EC2, CloudWatch and prices, and allows **stop/start only on
   instances tagged `autohibernate=true`**.
2. IAM → Users → Create user (no console access) → attach that policy → Security credentials →
   Create access key.
3. Add to `.env`:
   ```
   AWS_ACCESS_KEY_ID=...
   AWS_SECRET_ACCESS_KEY=...
   AWS_REGIONS=ap-south-1,us-east-1
   ```
   List every region where you have EC2 instances, separated by commas, no spaces.
   Only list regions that are enabled in your account.
4. Billing console → Cost Explorer → Launch (it can take up to 24 hours to start returning data).

## AutoHibernate

AutoHibernate stops VMs that are not doing any work. A VM is stopped only if **all** of these are true:

1. It has the tag `autohibernate` with the value `true` (exact spelling, lowercase).
2. It is running.
3. It has CPU data.
4. Its average CPU over the lookback window is below the idle threshold (defaults: 24 hours, 5%).

How to use it:

1. Tag the VMs you are happy to stop. In Azure: VM → Tags → `autohibernate` = `true`. In AWS: instance → Tags.
2. Overview → **Scan VMs**, then open **VMs & AutoHibernate**.
3. Leave **Dry run** ticked first. **Run AutoHibernate** only logs `WOULD_STOP` with an estimated saving.
4. Untick Dry run (a red LIVE MODE banner appears), tick the confirmation box, and run again.
   Azure VMs are **deallocated** (compute billing stops) and AWS instances are **stopped**. The app waits,
   re-reads the real power state, and records the verified result (`STOPPED`) or the error (`FAILED`) in the Action log.
5. Use **Start selected VM** to bring a VM back. Nothing restarts VMs automatically.

"Stop selected VM" works on any idle VM, tagged or not. It is manual and asks for confirmation in live mode.
Stay on the page while a stop runs: it waits up to 5 minutes for the cloud to confirm.

## Configuration (`.env`)

| Variable | Default | Meaning |
|---|---|---|
| `AZURE_SUBSCRIPTION_ID` | | Azure subscription to read and manage |
| `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET` | empty | Service principal; leave empty to use `az login` |
| `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` | empty | AWS credentials; leave empty to skip AWS |
| `AWS_REGIONS` | `ap-south-1` | Comma-separated regions to scan for EC2 instances |
| `AUTOHIBERNATE_TAG` | `autohibernate` | Tag key that opts a VM in |
| `AUTOHIBERNATE_DRY_RUN` | `true` | Whether the dry-run box starts ticked |
| `IDLE_CPU_THRESHOLD` | `5` | Average CPU (%) below which a VM is idle |
| `IDLE_WINDOW_HOURS` | `24` | CPU lookback window |
| `STOP_TIMEOUT_SECONDS` | `300` | How long to wait for a stop to be confirmed |
| `CLOUDONOMIX_DB` | `cloudonomix.db` | SQLite file location |

Thresholds can also be changed in the **Settings** dropdown at the top right of the app.

## Known limitations

- Billing is delayed on both clouds (roughly 8 to 24 hours). VM state and CPU are current.
- AWS cost is per service at account level, not per instance. Azure has per-resource cost for VMs.
- Azure and AWS can bill in different currencies. The app never adds them together and shows separate charts.
- Only VMs are listed. Networks, disks and other resources appear in billing by service but not as inventory.
- Memory usage is not available by default on either cloud, so the idle rule uses CPU only.
- The price comparison is a close match (same vCPU, similar RAM, x86, on-demand Linux, USD), not exact like-for-like.
- AWS Cost Explorer API calls cost about $0.01 each. The app makes a few per sync.
- Azure paths were verified against a real Azure subscription. The AWS paths are covered by the offline tests
  with fake clients; verify them against your own account.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Page stays blank on first start | Wait up to a minute for the first load, then refresh |
| `Failed to invoke the Azure CLI` | Run `az account show`. If it asks you to log in, run `az login` and restart the app |
| `az login` shows no subscription | `az login --tenant <tenant-id-or-domain>` |
| `streamlit` is not recognized | Activate the venv, or use `python -m streamlit run app.py` |
| Azure billing returns nothing | New or student subscriptions can take about 24 hours. Check Cost analysis in the portal first |
| AWS `DataUnavailableException` | Cost Explorer was just enabled. Wait up to 24 hours |
| AWS `AuthFailure` in the price comparison | The AWS region is not enabled in your account (Malaysia, Hong Kong, UAE, others are opt-in). Use an enabled region such as `ap-south-1` |
| AWS instances do not appear | Their region is not in `AWS_REGIONS`, or you have not clicked Scan VMs |
| Stop fails with `AuthorizationFailed` / `UnauthorizedOperation` | Azure: add the Virtual Machine Contributor role. AWS: the instance needs the `autohibernate=true` tag (policy condition) |
| Tag column is empty | Key must be exactly `autohibernate`, value `true`. Rescan after tagging |
| Fleet snapshot looks stale | It is a saved snapshot. Click Scan VMs again |

## Tests

An offline test suite with fake Azure, AWS and Streamlit clients (no network, no cloud calls):

```bash
python tests/test_offline.py
```

It covers the database migration, billing parsing, VM scans, stop/start (dry run, verified, failed, unverified),
AutoHibernate rules, the price comparison, and every page and button of the dashboard.

## Project structure

```
app.py               Streamlit UI: pages, navigation, theme CSS
config.py            Settings from .env, credential helpers
db.py                SQLite schema and helpers (migrates older databases)
billing.py           Azure Cost Management billing
aws_cloud.py         AWS billing, EC2 scan, stop/start, price list
vms.py               VM scans, idle suggestions, stop/start, AutoHibernate
pricing.py           Azure and AWS price lookups and comparison
anomaly.py           Cost spike detection
aws_iam_policy.json  Least-privilege AWS policy
.streamlit/          Theme (orange, warm white, dark brown)
tests/               Offline test suite
```

## Security

- Never commit `.env` or `cloudonomix.db`. Both are in `.gitignore`.
- If a key or secret is ever committed or shared, delete it in the cloud console and create a new one.
- Use a dedicated service principal and IAM user with only the permissions listed above.
- Keep Dry run on until you are sure which VMs carry the `autohibernate` tag.
- The app can stop real VMs. Only run it against accounts and VMs you are allowed to manage.

## Roadmap

GCP connector, automatic scheduled AutoHibernate, a multi-metric idle rule (CPU peak and network),
a protected-VM tag, per-instance AWS cost, and a resource inventory page.
