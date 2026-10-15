"""
Generate SYNTHETIC output of 24 banking reports from a "legacy" BI platform and
from an "upgraded" platform, with realistic migration defects injected into the
upgraded output (null handling, missing rows, join fan-out duplicates, a
time-zone/date cut-off shift, an aggregation difference, key formatting, and
rounding noise).

No real report, bank or employer data is used. Fixed seed => reproducible.

Run:  python scripts/generate_data.py
Out:  data/report_catalog.csv, data/legacy_output.csv, data/upgraded_output.csv
"""
from pathlib import Path

import numpy as np
import pandas as pd

rng = np.random.default_rng(77)
out = Path(__file__).resolve().parent.parent / "data"
out.mkdir(exist_ok=True)

PERIODS = [str(p) for p in pd.period_range("2024-10", "2026-09", freq="M")]
REGIONS = ["South", "West", "North", "East"]
PRODUCTS = ["Home", "Auto", "Personal", "Business", "Education"]

# id, name, domain, criticality, grain (A=period x region x product, B=period x region, C=period x product),
# measure type (amount / count / rate), defect profile
REPORTS = [
    (1, "Loan Disbursement Summary", "Lending", "Critical", "A", "amount", {}),
    (2, "Loan Outstanding by Branch", "Lending", "Critical", "B", "amount", {"null": 0.05}),
    (3, "NPA Movement", "Lending", "Critical", "A", "amount", {"date_shift": True}),
    (4, "Collections Efficiency", "Lending", "High", "B", "rate", {"dup": 0.04}),
    (5, "Loan Application Funnel", "Lending", "Medium", "C", "count", {}),
    (6, "Deposit Balance Summary", "Deposits", "Critical", "A", "amount", {"drift": 0.12}),
    (7, "CASA Ratio Trend", "Deposits", "High", "B", "rate", {}),
    (8, "Term Deposit Maturity", "Deposits", "Medium", "C", "amount", {}),
    (9, "Savings Account Openings", "Deposits", "Low", "C", "count", {}),
    (10, "Card Spend by Category", "Cards", "High", "A", "amount", {"key_format": 0.08}),
    (11, "Card Activation Rate", "Cards", "Medium", "B", "rate", {}),
    (12, "Card Delinquency Roll Rates", "Cards", "High", "A", "rate", {"agg_product": "Education"}),
    (13, "Reward Points Liability", "Cards", "Medium", "C", "amount", {}),
    (14, "Credit Exposure by Segment", "Risk", "Critical", "A", "amount", {}),
    (15, "Provision Coverage", "Risk", "Critical", "B", "rate", {"missing": 0.03}),
    (16, "Concentration Risk", "Risk", "High", "C", "amount", {"dup": 0.06}),
    (17, "Early Warning Indicators", "Risk", "High", "A", "count", {}),
    (18, "Net Interest Margin", "Finance", "Critical", "B", "rate", {}),
    (19, "Fee Income Summary", "Finance", "High", "A", "amount", {"null": 0.03, "dup": 0.02}),
    (20, "Cost to Income Ratio", "Finance", "High", "B", "rate", {}),
    (21, "Branch Profitability", "Finance", "Medium", "A", "amount", {"missing": 0.07}),
    (22, "Complaint Resolution SLA", "Operations", "Medium", "C", "rate", {"key_format": 0.05}),
    (23, "Branch Footfall", "Operations", "Low", "A", "count", {}),
    (24, "Call Centre Volume", "Operations", "Low", "C", "count", {"null": 0.09}),
]

catalog = pd.DataFrame(
    [(r[0], r[1], r[2], r[3], {"A": "period x region x product", "B": "period x region", "C": "period x product"}[r[4]], r[5])
     for r in REPORTS],
    columns=["report_id", "report_name", "domain", "criticality", "grain", "measure_type"],
)
catalog["pass_target_pct"] = catalog.criticality.map({"Critical": 99.9, "High": 99.5, "Medium": 99.5, "Low": 99.0})


def gen_value(mtype, n, trend):
    if mtype == "amount":  # INR crore
        return np.round(rng.lognormal(np.log(45), 0.7, n) * trend, 2)
    if mtype == "count":
        return np.round(rng.lognormal(np.log(220), 0.6, n) * trend).astype(float)
    return np.round(rng.uniform(4, 14, n) + (trend - 1) * 2, 4)  # rate, percent


legacy_parts, upgraded_parts = [], []
for rid, name, domain, crit, grain, mtype, prof in REPORTS:
    dims = []
    for p in PERIODS:
        if grain == "A":
            dims += [(p, r, pr) for r in REGIONS for pr in PRODUCTS]
        elif grain == "B":
            dims += [(p, r, "ALL") for r in REGIONS]
        else:
            dims += [(p, "ALL", pr) for pr in PRODUCTS]
    df = pd.DataFrame(dims, columns=["period", "region", "product"])
    trend = 1 + 0.015 * df.period.map({p: i for i, p in enumerate(PERIODS)}).values
    df["value"] = gen_value(mtype, len(df), trend)
    df.insert(0, "report_id", rid)
    df["measure_name"] = {"amount": "amount_cr", "count": "count", "rate": "rate_pct"}[mtype]

    legacy = df.copy()
    if "null" in prof:  # legacy has genuine NULLs
        idx = rng.choice(len(legacy), size=int(len(legacy) * prof["null"]), replace=False)
        legacy.loc[legacy.index[idx], "value"] = np.nan

    up = legacy.copy()
    v = up["value"].copy()

    # float-accumulation drift of 0.02-0.06% on larger amounts: passes at the default
    # 0.1% tolerance but fails if the tolerance is tightened (see the dashboard slider)
    p_drift = prof.get("drift", 0.02)
    d_mask = np.zeros(len(up), dtype=bool)
    if mtype == "amount":
        d_mask = (rng.random(len(up)) < p_drift) & (v.values >= 20)

    # baseline rounding noise (paise / 4th decimal) -> always inside tolerance
    noise_mask = (rng.random(len(up)) < 0.12) & ~d_mask
    step = {"amount": 0.01, "count": 0.0, "rate": 0.0001}[mtype]
    v = v + np.where(noise_mask, rng.choice([-step, step], len(up)), 0)
    if mtype == "amount":
        factor = 1 + rng.uniform(0.0002, 0.0006, len(up)) * rng.choice([-1, 1], len(up))
        v = np.round(np.where(d_mask, v * factor, v), 2)

    # null handling: upgraded platform coalesces NULL to 0
    v = np.where(np.isnan(legacy["value"].values), 0.0, v)

    # aggregation logic difference for one product (e.g. AVG instead of SUM-weighted)
    if "agg_product" in prof:
        m = (up["product"] == prof["agg_product"]).values & (rng.random(len(up)) < 0.8)
        v = np.where(m, np.round(v * rng.uniform(0.35, 0.65, len(up)), 4), v)
    up["value"] = v

    # key formatting: upgraded output has lower-case / trailing-space dimension values
    if "key_format" in prof:
        m = rng.random(len(up)) < prof["key_format"]
        up.loc[m, "product"] = up.loc[m, "product"].str.lower() + " "
        up.loc[m & (up["region"] != "ALL"), "region"] = up.loc[m & (up["region"] != "ALL"), "region"].str.upper()

    # missing rows: a filter on the new platform drops rows
    if "missing" in prof:
        drop = rng.choice(len(up), size=int(len(up) * prof["missing"]), replace=False)
        up = up.drop(up.index[drop])

    # time-zone / cut-off shift: last period lands in the next month on the new platform
    if prof.get("date_shift"):
        up.loc[up.period == PERIODS[-1], "period"] = "2026-10"

    # join fan-out: duplicated rows
    if "dup" in prof:
        dup = up.sample(frac=prof["dup"], random_state=int(rid))
        up = pd.concat([up, dup], ignore_index=True)

    legacy_parts.append(legacy)
    upgraded_parts.append(up)

legacy_all = pd.concat(legacy_parts, ignore_index=True)
upgraded_all = pd.concat(upgraded_parts, ignore_index=True).sample(frac=1.0, random_state=5).reset_index(drop=True)

catalog.to_csv(out / "report_catalog.csv", index=False)
legacy_all.to_csv(out / "legacy_output.csv", index=False)
upgraded_all.to_csv(out / "upgraded_output.csv", index=False)
print(f"reports={len(catalog)} legacy_rows={len(legacy_all):,} upgraded_rows={len(upgraded_all):,}")
