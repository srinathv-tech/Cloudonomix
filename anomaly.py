"""Rolling robust (median/MAD) spike detection on daily cost per service."""
import pandas as pd

COLS = ["date", "service", "cost", "baseline", "z_score"]


def detect(costs, window=7, z_thresh=3.5, min_delta=0.05):
    if costs.empty:
        return pd.DataFrame(columns=COLS)
    df = costs.copy()
    df["date"] = pd.to_datetime(df["date"])
    found = []
    for service, g in df.groupby("service"):
        s = g.groupby("date")["cost"].sum().sort_index()
        s = s.reindex(pd.date_range(s.index.min(), s.index.max()), fill_value=0.0)
        for i in range(window, len(s)):
            hist = s.iloc[i - window:i]
            med = hist.median()
            mad = (hist - med).abs().median()
            scale = mad / 0.6745 if mad > 0 else max(0.1 * med, 0.01)
            val = s.iloc[i]
            z = (val - med) / scale
            if z > z_thresh and (val - med) > min_delta:
                found.append(dict(date=s.index[i].date().isoformat(), service=service,
                                  cost=round(val, 4), baseline=round(med, 4), z_score=round(z, 1)))
    out = pd.DataFrame(found, columns=COLS)
    return out.sort_values("date", ascending=False).reset_index(drop=True)
