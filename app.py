import pandas as pd
import streamlit as st

import anomaly, aws_cloud, billing, config, db, pricing, vms

st.set_page_config(page_title="Cloudonomix", layout="wide")
db.init()

ORANGE, BROWN, CREAM = "#E8731A", "#3A2416", "#FBF6EE"
PALETTE = [ORANGE, BROWN, "#C9A27E", "#B4540A", "#8A6A55", "#F2A65A", "#6B4A36", "#D9B99B"]

st.markdown(f"""
<style>
[data-testid="stSidebar"] {{ background: {BROWN}; }}
[data-testid="stSidebar"] * {{ color: {CREAM} !important; }}
[data-testid="stSidebar"] hr {{ border-color: rgba(251,246,238,.25); }}
.block-container {{ padding-top: 4.5rem; }}
.cl-title {{ text-align: center; font-size: 2.6rem; font-weight: 800; letter-spacing: .2em;
             color: {BROWN}; margin: 0; line-height: 1.1; }}
.cl-sub {{ text-align: center; color: #8A6A55; margin: .2rem 0 1rem 0; }}
.cl-brand {{ font-size: 1.5rem; font-weight: 800; letter-spacing: .16em; }}
h1, h2, h3 {{ color: {BROWN}; }}
[data-testid="stMetric"] {{ background: #FFFFFF; border: 1px solid #E9D8C4;
                            border-radius: 12px; padding: 12px 16px; }}
div.stButton > button {{ border: 1.5px solid {ORANGE}; color: #B4540A; background: #FFFFFF;
                         border-radius: 8px; font-weight: 600; }}
div.stButton > button:hover {{ background: {ORANGE}; color: #FFFFFF; border-color: {ORANGE}; }}
div.stButton > button:disabled {{ border-color: #D9C9B8; color: #B8A794; background: #F6EEE3; }}
</style>
""", unsafe_allow_html=True)

if not config.SUBSCRIPTION_ID and not config.aws_configured():
    st.error("Set AZURE_SUBSCRIPTION_ID and/or the AWS keys in .env (see .env.example).")
    st.stop()

PAGES = ["Overview", "VMs & AutoHibernate", "Cross-Cloud Price Comparator",
         "Anomaly Alerts", "Cost Split Analyzer", "Auto Optimization Assistant"]

# Settings are kept here so every page can use them.
cfg = st.session_state.setdefault("cfg", dict(
    days=30, window=7, z=3.5, idle=float(config.IDLE_CPU_THRESHOLD), hours=config.IDLE_WINDOW_HOURS))


def run(label, fn):
    with st.spinner(label + "..."):
        try:
            st.success(fn())
        except Exception as e:
            st.error(f"{label} failed: {e}")


def flash(text):
    st.session_state["flash"] = text
    st.rerun()


def refresh(clouds):
    """Rescan after a real stop/start so the table shows the new power state."""
    for c in set(clouds):
        try:
            vms.scan(c, cfg["hours"])
        except Exception:
            pass


# ------------------------------------------------------------------ sidebar: navigation only
with st.sidebar:
    st.markdown('<div class="cl-brand">CLOUDONOMIX</div>', unsafe_allow_html=True)
    st.write("")
    page = st.radio("Navigate", PAGES, label_visibility="collapsed")
    st.divider()
    st.caption("Azure: " + ("configured" if config.SUBSCRIPTION_ID else "not configured"))
    st.caption("AWS: " + ("configured" if config.aws_configured() else "not configured"))
    st.caption("GCP: not connected yet")

# ------------------------------------------------------------------ header + settings
st.markdown('<p class="cl-title">CLOUDONOMIX</p>'
            '<p class="cl-sub">Multi-cloud cost optimization</p>', unsafe_allow_html=True)
_, settings_col = st.columns([6, 1])
with settings_col:
    with st.popover("Settings"):
        cfg["days"] = st.slider("Billing history (days)", 7, 90, cfg["days"], key="w_days")
        cfg["window"] = st.slider("Anomaly baseline window (days)", 3, 14, cfg["window"], key="w_window")
        cfg["z"] = st.slider("Anomaly sensitivity (z-score)", 2.0, 6.0, cfg["z"], 0.5, key="w_z")
        cfg["idle"] = st.slider("Idle CPU threshold (%)", 1.0, 20.0, cfg["idle"], 0.5, key="w_idle")
        cfg["hours"] = st.slider("CPU lookback (hours)", 1, 72, cfg["hours"], key="w_hours")
days, window, z, idle, hours = (cfg[k] for k in ("days", "window", "z", "idle", "hours"))

costs = db.read("SELECT * FROM costs")
vm_df = db.read("SELECT * FROM vm_metrics v WHERE ts=(SELECT MAX(ts) FROM vm_metrics WHERE cloud=v.cloud)")
if not vm_df.empty:
    rc = db.read("SELECT resource_id, SUM(cost) AS period_cost FROM resource_costs "
                 "WHERE cloud='azure' GROUP BY resource_id")
    vm_df["rid"] = vm_df["instance_id"].str.lower()
    vm_df = vm_df.merge(rc, left_on="rid", right_on="resource_id", how="left").drop(
        columns=["rid", "resource_id"])


def named_costs():
    """Costs with the cloud name in the service label, so both clouds can share one chart."""
    return costs.assign(service=costs["cloud"].str.upper() + ": " + costs["service"])


# ================================================================== Overview
if page == "Overview":
    with st.container(border=True):
        st.subheader("Data sources")
        c_az, c_aws, c_gcp, c_chk = st.columns([1, 1, 1, 1.2])
        with c_az:
            st.markdown("**Azure**")
            if config.SUBSCRIPTION_ID:
                if st.button("Scan VMs", key="scan_azure"):
                    run("Azure VM scan", lambda: f"Scanned {len(vms.scan('azure', hours))} VMs")
                if st.button("Sync billing", key="bill_azure"):
                    run("Azure billing sync", lambda: f"Stored {billing.sync(days)} rows")
            else:
                st.caption("Set AZURE_SUBSCRIPTION_ID in .env")
        with c_aws:
            st.markdown("**AWS**")
            if config.aws_configured():
                if st.button("Scan VMs", key="scan_aws"):
                    run("AWS EC2 scan", lambda: f"Scanned {len(vms.scan('aws', hours))} instances")
                if st.button("Sync billing", key="bill_aws"):
                    run("AWS billing sync", lambda: f"Stored {aws_cloud.sync_billing(days)} rows")
            else:
                st.caption("Add AWS keys to .env")
        with c_gcp:
            st.markdown("**GCP**")
            st.button("Scan VMs", key="scan_gcp", disabled=True)
            st.button("Sync billing", key="bill_gcp", disabled=True)
            st.caption("Not connected yet")
        with c_chk:
            st.markdown("**Connections**")
            if st.button("Check connections", key="check_conn"):
                if config.SUBSCRIPTION_ID:
                    try:
                        compute, _ = vms._azure_clients()
                        next(iter(compute.virtual_machines.list_all()), None)
                        st.success("Azure: connected")
                    except Exception as e:
                        st.error(f"Azure: {e}")
                if config.aws_configured():
                    try:
                        acct, arn = aws_cloud.identity()
                        st.success(f"AWS: connected (account {acct})")
                    except Exception as e:
                        st.error(f"AWS: {e}")

    with st.container(border=True):
        st.subheader("Fleet snapshot")
        if vm_df.empty:
            st.info("No VMs scanned yet. Use the Scan VMs buttons above.")
        else:
            running = vm_df[vm_df["power_state"] == "running"]
            idle_now = running[running["avg_cpu"].notna() & (running["avg_cpu"] < idle)]
            f1, f2, f3, f4 = st.columns(4)
            f1.metric("VMs", len(vm_df))
            f2.metric("Running", len(running))
            f3.metric("Stopped", len(vm_df) - len(running))
            f4.metric(f"Idle (<{idle:g}% CPU)", len(idle_now))
            st.caption(f"Last scan: {vm_df['ts'].max()} UTC. This is a saved snapshot, "
                       "so click Scan VMs after you start or stop anything.")

    st.subheader("Cost")
    if costs.empty:
        st.info("No billing data yet. Use the Sync billing buttons above. New accounts can take about 24h to show costs.")
    else:
        sel = st.selectbox("Cloud", ["All"] + sorted(costs["cloud"].unique()), key="cost_filter")
        view = costs if sel == "All" else costs[costs["cloud"] == sel]
        for cloud, g in view.groupby("cloud"):
            cur = g["currency"].iloc[0]
            daily = g.groupby("date")["cost"].sum().sort_index()
            st.markdown(f"**{cloud.upper()}** ({cur})")
            c1, c2, c3 = st.columns(3)
            c1.metric("Total", f"{daily.sum():,.2f}")
            c2.metric("Daily average", f"{daily.mean():,.2f}")
            c3.metric("Latest day", f"{daily.iloc[-1]:,.2f}")
        def colors(n):
            return [PALETTE[i % len(PALETTE)] for i in range(n)]

        if view["currency"].nunique() > 1:
            # Different currencies cannot share one axis, so each cloud gets its own chart.
            for cloud, g in view.groupby("cloud"):
                st.markdown(f"**{cloud.upper()}**: daily cost by service ({g['currency'].iloc[0]})")
                wide = g.pivot_table(index="date", columns="service", values="cost", aggfunc="sum").fillna(0)
                st.line_chart(wide, color=colors(wide.shape[1]))
            st.caption("Azure and AWS bill in different currencies, so they get separate charts and their "
                       "totals are never added together.")
        else:
            plot = view.assign(series=view["cloud"].str.upper() + ": " + view["service"])
            wide = plot.pivot_table(index="date", columns="series", values="cost", aggfunc="sum").fillna(0)
            st.line_chart(wide, color=colors(wide.shape[1]))

# ================================================================== VMs & AutoHibernate
elif page == "VMs & AutoHibernate":
    st.header("VMs & AutoHibernate")
    # Not stored in the settings on purpose: it goes back to dry run whenever you leave this page.
    dry = st.checkbox("Dry run (do NOT really stop VMs)", value=config.DRY_RUN)
    if dry:
        st.info("DRY RUN: stop buttons only log what would happen. Untick the box above to really stop VMs.")
    else:
        st.error("LIVE MODE: stop buttons will really stop VMs.")
    st.caption(f"Idle threshold: {idle:g}% CPU over the last {hours} h (change it under Settings).")

    fl = st.session_state.pop("flash", None)
    if fl:
        (st.error if "FAILED" in fl else st.success)(fl)
    if vm_df.empty:
        st.info("Scan your VMs from the Overview page first.")
    else:
        st.dataframe(vm_df.drop(columns=["instance_id"]), use_container_width=True)
        records = vm_df.to_dict("records")

        st.subheader("Idle VM suggestions (all VMs, tagged or not)")
        sugg = vms.suggest(records, idle)
        if sugg:
            st.dataframe(pd.DataFrame(sugg).drop(columns=["instance_id"]), use_container_width=True)
            labels = [f"{x['cloud']}: {x['vm']}" for x in sugg]
            pick = st.selectbox("Pick a VM to stop", labels)
            row = sugg[labels.index(pick)]
            ok = dry or st.checkbox(f"Yes, really stop {pick}", key="confirm_pick")
            if st.button("Stop selected VM") and ok:
                with st.spinner(f"Stopping {pick} and waiting for confirmation. Stay on this page..."):
                    res = vms.stop_vm(row, dry)
                    if not dry:
                        refresh([row["cloud"]])
                flash(f"{res['action']}: {pick} - {res['result']}")
        else:
            st.info(f"No running VMs with CPU data below {idle:g}%.")

        st.subheader(f"AutoHibernate (only VMs tagged {config.HIBERNATE_TAG}=true)")
        tagged = [r for r in records if str(r.get("tag")).lower() == "true"]
        st.caption(f"{len(tagged)} tagged VM(s) right now. Untagged VMs are never touched here.")
        ok_auto = dry or st.checkbox("Yes, really stop matching idle tagged VMs", key="confirm_auto")
        if st.button("Run AutoHibernate") and ok_auto:
            with st.spinner("Running AutoHibernate. Stay on this page..."):
                log = vms.autohibernate(records, idle, dry)
                if not dry and log:
                    refresh([e["cloud"] for e in log])
            if log:
                flash("\n\n".join(f"{e['action']}: {e['cloud']}/{e['vm']} - {e['result']}" for e in log))
            else:
                st.info(f"No running tagged VMs are below {idle:g}% CPU.")

        st.subheader("Start a stopped VM")
        stopped = [r for r in records if r["power_state"] in ("deallocated", "stopped")]
        if stopped:
            labels = [f"{r['cloud']}: {r['vm']}" for r in stopped]
            pick = st.selectbox("Pick a VM to start", labels)
            ok_start = st.checkbox(f"Yes, start {pick} (it will cost money while running)", key="confirm_start")
            if st.button("Start selected VM") and ok_start:
                row = stopped[labels.index(pick)]
                with st.spinner(f"Starting {pick}. Stay on this page..."):
                    res = vms.start_vm(row)
                    refresh([row["cloud"]])
                flash(f"{res['action']}: {pick} - {res['result']}")
        else:
            st.caption("No stopped VMs.")

    st.subheader("Action log")
    st.dataframe(db.read("SELECT * FROM actions ORDER BY ts DESC"), use_container_width=True)

# ================================================================== Cross-Cloud Price Comparator
elif page == "Cross-Cloud Price Comparator":
    st.header("Cross-Cloud Price Comparator")
    st.subheader("Azure vs AWS (live prices)")
    azure_vms = vm_df[vm_df["cloud"] == "azure"] if not vm_df.empty else vm_df
    labels = [f"{r.vm} ({r.size}, {r.location})" for r in azure_vms.itertuples()] if not azure_vms.empty else []
    choice = st.selectbox("Start from one of your Azure VMs", ["(type manually)"] + labels)
    d_size, d_region = "Standard_B2ats_v2", "centralindia"
    if choice != "(type manually)":
        r = azure_vms.iloc[labels.index(choice)]
        d_size, d_region = r["size"], r["location"]
    c1, c2, c3 = st.columns(3)
    size = c1.text_input("Azure VM size", d_size, key=f"size_{choice}")
    az_region = c2.text_input("Azure region", d_region, key=f"region_{choice}")
    aws_region = c3.text_input("AWS region (blank = closest match)", "", key=f"awsr_{choice}")
    if st.button("Compare Azure vs AWS"):
        with st.spinner("Fetching live prices from Azure and AWS..."):
            try:
                st.session_state["cmp"] = pricing.cross_cloud(
                    size.strip(), az_region.strip().lower(), aws_region.strip() or None)
            except Exception as e:
                st.session_state.pop("cmp", None)
                st.error(f"Comparison failed: {e}")
    if st.session_state.get("cmp"):
        cmp_df, warns = st.session_state["cmp"]
        for w in warns:
            st.warning(w)
        st.dataframe(cmp_df, use_container_width=True)
        chart = cmp_df.assign(label=cmp_df["cloud"] + ": " + cmp_df["instance"]).set_index("label")
        st.bar_chart(chart["price_per_hour"], color=ORANGE)
        st.caption("AWS matches: same vCPU count, RAM within 0.75x to 2.1x, x86, on-demand Linux, USD. "
                   "Azure B-series is compared with AWS burstable types. This is a close match, "
                   "not an exact like-for-like.")

        aws_rows = cmp_df[(cmp_df["cloud"] == "AWS") & cmp_df["price_per_hour"].notna()]
        if not aws_rows.empty:
            st.subheader("AWS region sweep")
            inst = st.selectbox("AWS instance", list(aws_rows["instance"]), key="sweep_instance")
            if st.button("Compare this instance across all AWS regions"):
                with st.spinner("Fetching prices for every AWS region..."):
                    try:
                        sweep = pricing.aws_region_sweep(inst)
                        if sweep.empty:
                            st.info("No prices found for that instance.")
                        else:
                            st.dataframe(sweep, use_container_width=True)
                            st.bar_chart(sweep.head(15).set_index("region")["price_per_hour"], color=ORANGE)
                    except Exception as e:
                        st.error(f"Region sweep failed: {e}")

    with st.expander("Azure region price comparison for one size"):
        rsize = st.text_input("VM size", "Standard_B1s", key="rsize")
        if st.button("Compare Azure regions"):
            try:
                df = pricing.compare_regions(rsize)
                st.dataframe(df.head(20), use_container_width=True)
                st.bar_chart(df.head(15).set_index("region")["price_per_hour"], color=ORANGE)
            except Exception as e:
                st.error(f"Price lookup failed: {e}")
    ref = pricing.load_reference()
    if ref is not None:
        st.subheader("Other clouds (your reference_prices.csv)")
        st.dataframe(ref, use_container_width=True)

# ================================================================== Anomaly Alerts
elif page == "Anomaly Alerts":
    st.header("Anomaly Alerts")
    st.caption(f"Baseline window: {window} days, sensitivity: {z:g} (change these under Settings).")
    found = anomaly.detect(named_costs(), window, z)
    if found.empty:
        st.info(f"No spikes found. Needs more than {window} days of history; lower the baseline window if you have few days.")
    else:
        st.dataframe(found, use_container_width=True)

# ================================================================== Cost Split Analyzer
elif page == "Cost Split Analyzer":
    st.header("Cost Split Analyzer")
    if costs.empty:
        st.info("Sync billing data first (Overview page).")
    else:
        rg = costs.groupby(["cloud", "resource_group"])["cost"].sum().to_frame("cost").reset_index()
        rg["share_%"] = (rg["cost"] / rg.groupby("cloud")["cost"].transform("sum") * 100).round(1)
        rg = rg.sort_values(["cloud", "cost"], ascending=[True, False])
        st.subheader("By resource group (AWS has account-level cost only)")
        st.dataframe(rg, use_container_width=True)
        st.bar_chart(rg.assign(label=rg["cloud"] + ": " + rg["resource_group"]).set_index("label")["cost"],
                     color=ORANGE)
    st.subheader("By Azure tag")
    tag_key = st.text_input("Tag key (e.g. team, project)")
    if st.button("Fetch tag costs") and tag_key:
        try:
            st.dataframe(billing.fetch_tag_costs(tag_key, days), use_container_width=True)
        except Exception as e:
            st.error(f"Tag query failed: {e}")

# ================================================================== Auto Optimization Assistant
else:
    st.header("Auto Optimization Assistant")
    st.caption("Rule-based recommendations built from your latest scan and billing data. "
               "Apply them on the VMs & AutoHibernate page. Savings are estimates from list prices in USD.")
    recs = []
    records = vm_df.to_dict("records") if not vm_df.empty else []
    for s in vms.suggest(records, idle):
        target = f"{s['cloud']}: {s['vm']}"
        recs.append(dict(action="Stop idle VM", target=target,
                         why=f"average CPU {s['avg_cpu']}% is below {idle:g}% over {hours} h",
                         est_saving_per_day_usd=s["est_saving_per_day"]))
        if str(s.get("tag")).lower() != "true":
            recs.append(dict(action="Tag for AutoHibernate", target=target,
                             why=f"add {config.HIBERNATE_TAG}=true so it can be stopped automatically",
                             est_saving_per_day_usd=None))
    found = anomaly.detect(named_costs(), window, z)
    for a in found.head(5).itertuples():
        recs.append(dict(action="Investigate cost spike", target=a.service,
                         why=f"{a.date}: cost {a.cost} vs baseline {a.baseline}",
                         est_saving_per_day_usd=None))
    if not recs:
        st.success("Nothing to optimise right now. Scan your VMs and sync billing on the Overview page, "
                   "then check back.")
    else:
        df = pd.DataFrame(recs)
        per_day = float(df["est_saving_per_day_usd"].sum(skipna=True))
        m1, m2, m3 = st.columns(3)
        m1.metric("Recommendations", len(df))
        m2.metric("Est. saving per day (USD)", f"{per_day:,.2f}")
        m3.metric("Est. saving per month (USD)", f"{per_day * 30:,.2f}")
        st.dataframe(df, use_container_width=True)
