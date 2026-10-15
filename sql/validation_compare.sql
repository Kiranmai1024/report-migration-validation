-- =====================================================================
-- Legacy vs upgraded report output: row-level comparison in SQL
-- (SQL Server flavour). Mirrors scripts/validate.py exactly.
-- Data is SYNTHETIC.
-- =====================================================================

-- Staging tables loaded from data/legacy_output.csv and data/upgraded_output.csv
CREATE TABLE stg_legacy (
    report_id INT, period CHAR(7), region VARCHAR(20), product VARCHAR(20),
    value DECIMAL(18,4) NULL, measure_name VARCHAR(20)
);
CREATE TABLE stg_upgraded (
    report_id INT, period CHAR(7), region VARCHAR(20), product VARCHAR(20),
    value DECIMAL(18,4) NULL, measure_name VARCHAR(20)
);

-- Parameters
DECLARE @abs_tol DECIMAL(10,4) = 0.01;    -- paise rounding
DECLARE @rel_tol DECIMAL(10,6) = 0.001;   -- 0.1 %

-- 1. Normalise keys (trim + lower-case) and number repeats in the new output
WITH l AS (
    SELECT report_id, period,
           LOWER(LTRIM(RTRIM(region)))  AS region,
           LOWER(LTRIM(RTRIM(product))) AS product,
           region AS region_raw, product AS product_raw, value
    FROM stg_legacy
),
u AS (
    SELECT report_id, period,
           LOWER(LTRIM(RTRIM(region)))  AS region,
           LOWER(LTRIM(RTRIM(product))) AS product,
           region AS region_raw, product AS product_raw, value,
           ROW_NUMBER() OVER (PARTITION BY report_id, period,
                                           LOWER(LTRIM(RTRIM(region))), LOWER(LTRIM(RTRIM(product)))
                              ORDER BY (SELECT 1)) AS occ
    FROM stg_upgraded
),
-- 2. Compare first occurrences with a FULL OUTER JOIN
cmp AS (
    SELECT COALESCE(l.report_id, u.report_id) AS report_id,
           COALESCE(l.period,  u.period)      AS period,
           COALESCE(l.region,  u.region)      AS region,
           COALESCE(l.product, u.product)     AS product,
           l.value AS legacy_value, u.value AS upgraded_value,
           ABS(l.value - u.value)             AS abs_diff,
           CASE WHEN l.value = 0 THEN NULL ELSE ABS(l.value - u.value) / ABS(l.value) END AS rel_diff,
           CASE WHEN l.region_raw <> u.region_raw OR l.product_raw <> u.product_raw
                THEN 1 ELSE 0 END             AS key_format_flag,
           CASE WHEN l.report_id IS NULL THEN 'EXTRA_IN_UPGRADED'
                WHEN u.report_id IS NULL THEN 'MISSING_IN_UPGRADED' END AS presence
    FROM l
    FULL OUTER JOIN (SELECT * FROM u WHERE occ = 1) u
      ON  u.report_id = l.report_id AND u.period = l.period
      AND u.region = l.region      AND u.product = l.product
),
classified AS (
    SELECT *,
           CASE
             WHEN presence IS NOT NULL                                        THEN presence
             WHEN legacy_value IS NULL AND upgraded_value = 0                 THEN 'NULL_HANDLING'
             WHEN legacy_value IS NULL                                        THEN 'VALUE_MISMATCH'
             WHEN abs_diff = 0 AND key_format_flag = 0                        THEN 'MATCH'
             WHEN (abs_diff <= @abs_tol OR rel_diff <= @rel_tol) AND key_format_flag = 1 THEN 'KEY_FORMAT'
             WHEN abs_diff <= @abs_tol OR rel_diff <= @rel_tol                THEN 'WITHIN_TOLERANCE'
             ELSE 'VALUE_MISMATCH'
           END AS status
    FROM cmp
),
-- 3. Duplicates (join fan-out) in the new output
dups AS (
    SELECT report_id, period, region, product,
           CAST(NULL AS DECIMAL(18,4)) AS legacy_value, value AS upgraded_value,
           CAST(NULL AS DECIMAL(18,4)) AS abs_diff, CAST(NULL AS DECIMAL(18,6)) AS rel_diff,
           0 AS key_format_flag, CAST(NULL AS VARCHAR(30)) AS presence,
           'DUPLICATE_IN_UPGRADED' AS status
    FROM u WHERE occ > 1
),
all_rows AS (
    SELECT * FROM classified UNION ALL SELECT * FROM dups
)
-- 4. Sign-off summary per report
SELECT  r.report_id,
        COUNT(*)                                                                          AS rows_compared,
        SUM(CASE WHEN status IN ('MATCH','WITHIN_TOLERANCE','KEY_FORMAT') THEN 1 ELSE 0 END) AS pass_rows,
        100.0 * SUM(CASE WHEN status IN ('MATCH','WITHIN_TOLERANCE','KEY_FORMAT') THEN 1 ELSE 0 END)
              / COUNT(*)                                                                  AS pass_rate_pct
FROM    all_rows r
GROUP BY r.report_id
ORDER BY pass_rate_pct;

-- 5. Row-count reconciliation (must be zero for a trustworthy comparison):
--    every legacy row and every upgraded row must be classified exactly once.
SELECT 'legacy rows not classified' AS check_name,
       (SELECT COUNT(*) FROM stg_legacy)
     - (SELECT COUNT(*) FROM all_rows WHERE status NOT IN ('EXTRA_IN_UPGRADED','DUPLICATE_IN_UPGRADED')) AS difference
UNION ALL
SELECT 'upgraded rows not classified',
       (SELECT COUNT(*) FROM stg_upgraded)
     - (SELECT COUNT(*) FROM all_rows WHERE status NOT IN ('MISSING_IN_UPGRADED'));
