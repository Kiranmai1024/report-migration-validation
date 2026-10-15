"""
Compare legacy vs upgraded report output row by row and classify every difference.

Statuses
  MATCH                 identical value
  WITHIN_TOLERANCE      |diff| <= ABS_TOL or relative diff <= REL_TOL (rounding / float drift)
  KEY_FORMAT            values reconcile but the key differs in case/whitespace on the new platform
  VALUE_MISMATCH        values differ beyond tolerance (calculation / aggregation logic)
  NULL_HANDLING         legacy NULL became 0 on the new platform
  MISSING_IN_UPGRADED   key exists in legacy, not in upgraded (filter / join / date cut-off)
  EXTRA_IN_UPGRADED     key exists only in upgraded
  DUPLICATE_IN_UPGRADED same key repeated in upgraded (join fan-out)

Pass = MATCH + WITHIN_TOLERANCE + KEY_FORMAT.
Pass rate = pass rows / all classified rows (union of keys, duplicates included).

Run:  python scripts/validate.py [--rel-tol 0.001] [--abs-tol 0.01]
Out:  data/validation_results.csv, data/report_summary.csv, data/defect_log.csv
"""
import argparse
from pathlib import Path

import numpy as np
import pandas as pd

ap = argparse.ArgumentParser()
ap.add_argument("--rel-tol", type=float, default=0.001, help="relative tolerance (0.001 = 0.1%%)")
ap.add_argument("--abs-tol", type=float, default=0.01, help="absolute tolerance (paise)")
args = ap.parse_args()

root = Path(__file__).resolve().parent.parent
data = root / "data"
catalog = pd.read_csv(data / "report_catalog.csv")
legacy = pd.read_csv(data / "legacy_output.csv", keep_default_na=True)
upg = pd.read_csv(data / "upgraded_output.csv", keep_default_na=True)

for df in (legacy, upg):
    for c in ("region", "product"):
        df[c + "_raw"] = df[c].astype(str)
        df[c] = df[c].astype(str).str.strip().str.lower()
    df["key"] = df.period + "|" + df.region + "|" + df["product"]
    df["raw_key"] = df.period + "|" + df.region_raw + "|" + df.product_raw

assert not legacy.duplicated(["report_id", "key"]).any(), "legacy keys must be unique"

# first occurrence is compared; repeats are duplicates
upg["occ"] = upg.groupby(["report_id", "key"]).cumcount()
dups = upg[upg.occ > 0].copy()
first = upg[upg.occ == 0]

m = legacy.merge(first, on=["report_id", "key"], how="outer", suffixes=("_l", "_u"), indicator="merge_flag")

rows = []
for r in m.itertuples(index=False):
    lv, uv = r.value_l, r.value_u
    key = r.key
    if r.merge_flag == "left_only":
        status, diff, rel = "MISSING_IN_UPGRADED", np.nan, np.nan
    elif r.merge_flag == "right_only":
        status, diff, rel = "EXTRA_IN_UPGRADED", np.nan, np.nan
    else:
        if pd.isna(lv):
            status = "NULL_HANDLING" if uv == 0 else "VALUE_MISMATCH"
            diff, rel = (np.nan, np.nan)
        else:
            diff = abs(lv - uv)
            rel = 0.0 if diff == 0 else (diff / abs(lv) if lv != 0 else 1.0)
            if diff == 0:
                status = "MATCH"
            elif diff <= args.abs_tol + 1e-9 or rel <= args.rel_tol:
                status = "WITHIN_TOLERANCE"
            else:
                status = "VALUE_MISMATCH"
            if status in ("MATCH", "WITHIN_TOLERANCE") and r.raw_key_l != r.raw_key_u:
                status = "KEY_FORMAT"
    rows.append((int(r.report_id), key, lv, uv, diff, rel, status))

res = pd.DataFrame(rows, columns=["report_id", "key", "legacy_value", "upgraded_value", "abs_diff", "rel_diff", "status"])
d = dups[["report_id", "key", "value"]].rename(columns={"value": "upgraded_value"})
d["legacy_value"] = np.nan
d["abs_diff"] = np.nan
d["rel_diff"] = np.nan
d["status"] = "DUPLICATE_IN_UPGRADED"
res = pd.concat([res, d[res.columns]], ignore_index=True).sort_values(["report_id", "key", "status"]).reset_index(drop=True)

# ------------------------------------------------------------ reconciliation
for rid, g in res.groupby("report_id"):
    n_legacy = (legacy.report_id == rid).sum()
    in_legacy = g.status.isin(["MATCH", "WITHIN_TOLERANCE", "KEY_FORMAT", "VALUE_MISMATCH", "NULL_HANDLING", "MISSING_IN_UPGRADED"]).sum()
    assert n_legacy == in_legacy, f"report {rid}: legacy rows {n_legacy} != classified {in_legacy}"
    n_up = (upg.report_id == rid).sum()
    in_up = g.status.isin(["MATCH", "WITHIN_TOLERANCE", "KEY_FORMAT", "VALUE_MISMATCH", "NULL_HANDLING", "EXTRA_IN_UPGRADED", "DUPLICATE_IN_UPGRADED"]).sum()
    assert n_up == in_up, f"report {rid}: upgraded rows {n_up} != classified {in_up}"
print("reconciliation OK: every legacy and upgraded row is classified exactly once")

# ------------------------------------------------------------------ summaries
PASS = ["MATCH", "WITHIN_TOLERANCE", "KEY_FORMAT"]
res["is_pass"] = res.status.isin(PASS)
summ = res.groupby("report_id").agg(rows=("status", "size"), pass_rows=("is_pass", "sum")).reset_index()
summ["pass_rate_pct"] = (summ.pass_rows / summ.rows * 100).round(2)
for s in ["VALUE_MISMATCH", "NULL_HANDLING", "MISSING_IN_UPGRADED", "EXTRA_IN_UPGRADED", "DUPLICATE_IN_UPGRADED", "KEY_FORMAT"]:
    summ[s.lower()] = summ.report_id.map(res[res.status == s].groupby("report_id").size()).fillna(0).astype(int)
summ = catalog.merge(summ, on="report_id")
summ["ready_for_signoff"] = np.where(summ.pass_rate_pct >= summ.pass_target_pct, "Ready", "Needs fix")
summ.to_csv(data / "report_summary.csv", index=False)

log = (
    res[~res.is_pass]
    .groupby(["report_id", "status"])
    .agg(rows_affected=("status", "size"), example_key=("key", "first"),
         example_legacy=("legacy_value", "first"), example_upgraded=("upgraded_value", "first"))
    .reset_index()
)
cause = {
    "VALUE_MISMATCH": "Calculation or aggregation logic differs (check measure definition, grouping level)",
    "NULL_HANDLING": "NULL coalesced to 0 on the new platform (align NULL semantics / COALESCE rules)",
    "MISSING_IN_UPGRADED": "Rows dropped by a filter, inner join, or date cut-off on the new platform",
    "EXTRA_IN_UPGRADED": "Rows appear only on the new platform (date/time-zone shift or new join path)",
    "DUPLICATE_IN_UPGRADED": "Join fan-out: non-unique join key repeats rows",
}
log["likely_root_cause"] = log.status.map(cause)
log = catalog[["report_id", "report_name", "criticality"]].merge(log, on="report_id").sort_values(["rows_affected"], ascending=False)
log.to_csv(data / "defect_log.csv", index=False)
res.drop(columns="is_pass").to_csv(data / "validation_results.csv", index=False)

tot = len(res)
print(f"rows classified: {tot:,} | pass rate: {res.is_pass.mean():.2%} "
      f"(abs_tol={args.abs_tol}, rel_tol={args.rel_tol:.2%})")
print(summ.ready_for_signoff.value_counts().to_string())
print("\nstatus mix:\n" + res.status.value_counts().to_string())
print("\nNeeds fix:\n" + summ[summ.ready_for_signoff == "Needs fix"][["report_name", "criticality", "pass_rate_pct", "pass_target_pct"]].to_string(index=False))
