# DAX measures and Power BI build guide

Load `data/validation_results.csv` (fact), `data/report_catalog.csv` (dimension) and, if you
want to explore the raw outputs, `data/legacy_output.csv` / `data/upgraded_output.csv`.

**Model:** `validation_results[report_id]` (many) -> `report_catalog[report_id]` (one), single direction.

## Measures

```dax
Rows Compared = COUNTROWS ( validation_results )

Pass Rows =
CALCULATE (
    COUNTROWS ( validation_results ),
    validation_results[status] IN { "MATCH", "WITHIN_TOLERANCE", "KEY_FORMAT" }
)

Failing Rows = [Rows Compared] - [Pass Rows]

Pass Rate % = DIVIDE ( [Pass Rows], [Rows Compared] )

Failure Rate % = 1 - [Pass Rate %]
```

## Sign-off logic (per report)

```dax
Target Pass Rate = MAX ( report_catalog[pass_target_pct] ) / 100

Ready for Sign-off =
IF ( [Pass Rate %] >= [Target Pass Rate], "Ready", "Needs fix" )

Reports Ready =
SUMX (
    VALUES ( report_catalog[report_id] ),
    IF ( [Pass Rate %] >= [Target Pass Rate], 1, 0 )
)

Critical Reports Blocked =
CALCULATE (
    COUNTROWS ( report_catalog ),
    FILTER (
        report_catalog,
        report_catalog[criticality] = "Critical"
            && [Pass Rate %] < [Target Pass Rate]
    )
)
```

## What-if tolerance (parameter)

Create a **What-if parameter** `Tolerance` (0 to 0.01, step 0.0001, default 0.001). Then re-classify
in a measure so the slider changes the result live:

```dax
Pass Rows (What-if) =
VAR AbsTol = 0.01
VAR Tol    = [Tolerance Value]
RETURN
SUMX (
    validation_results,
    SWITCH (
        TRUE (),
        validation_results[status] IN { "MATCH", "KEY_FORMAT" }, 1,
        validation_results[status] IN { "WITHIN_TOLERANCE", "VALUE_MISMATCH" }
            && ( validation_results[abs_diff] <= AbsTol || validation_results[rel_diff] <= Tol ), 1,
        0
    )
)
```

## Suggested report page

| Visual | Fields |
|---|---|
| Cards | Reports Ready, Pass Rate %, Rows Compared, Failing Rows, Critical Reports Blocked |
| Bar (sorted desc) | Axis: report_name; Values: Failure Rate %; add a constant line at the target |
| Bar | Axis: status (failing types only); Values: Rows Compared |
| Stacked column | Axis: relative-difference bucket; Legend: pass / fail |
| Table | report_name, criticality, status, Failing Rows, example key |
| Slicers | domain, criticality, Tolerance parameter |

Colour rule used throughout: **blue = passes / ready, orange = fails / needs fix**, always with a text label
(never colour alone).
