"""
Embed validation data into dashboard/template.html -> dashboard/index.html
(single self-contained file; works offline and on GitHub Pages).

Run:  python scripts/validate.py && python scripts/build_dashboard.py
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd

root = Path(__file__).resolve().parent.parent
cat = pd.read_csv(root / "data" / "report_catalog.csv")
res = pd.read_csv(root / "data" / "validation_results.csv")

idx = {rid: i for i, rid in enumerate(cat.report_id)}

# row codes: 0 value pair, 1 missing, 2 extra, 3 duplicate, 4 null handling, 5 key-format pair
code_of = {
    "MATCH": 0, "WITHIN_TOLERANCE": 0, "VALUE_MISMATCH": 0,
    "KEY_FORMAT": 5, "MISSING_IN_UPGRADED": 1, "EXTRA_IN_UPGRADED": 2,
    "DUPLICATE_IN_UPGRADED": 3, "NULL_HANDLING": 4,
}


def num(x, nd=4):
    return None if pd.isna(x) else round(float(x), nd)


rows = []
for r in res.itertuples(index=False):
    code = code_of[r.status]
    # VALUE_MISMATCH rows where legacy was NULL carry no diff: treat as a pair that always fails
    abs_d = num(r.abs_diff)
    rel_d = num(r.rel_diff, 6)
    if r.status == "VALUE_MISMATCH" and abs_d is None:
        abs_d, rel_d = 9999.0, 9999.0
    rows.append([idx[r.report_id], code, num(r.legacy_value), num(r.upgraded_value), abs_d, rel_d, r.key.replace("|all", "").replace("|", " | ")])

data = {
    "reports": [[x.report_name, x.domain, x.criticality, float(x.pass_target_pct)] for x in cat.itertuples()],
    "domains": sorted(cat.domain.unique().tolist()),
    "rows": rows,
}
tpl = (root / "dashboard" / "template.html").read_text(encoding="utf-8")
html = tpl.replace("/*DATA*/", json.dumps(data, separators=(",", ":")))
(root / "dashboard" / "index.html").write_text(html, encoding="utf-8")
print(f"wrote dashboard/index.html ({len(html) / 1024:.0f} KB, {len(rows):,} rows)")
