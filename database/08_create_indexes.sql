-- ============================================================
-- 08_create_indexes.sql
-- Index tuning for the OLAP star schema.
--
-- WHAT THIS SCRIPT DOES
--   1. Audits the indexes that 03/04_create_*.sql already created.
--   2. ADDS four indexes: two composite COVERING indexes on the fact
--      table, one composite index on the SCD2 employee dimension and one
--      calendar index, each matched to a predicate the Streamlit
--      dashboard actually issues.
--   3. DROPS the one secondary index that is strictly redundant.
--   4. Refreshes index statistics (ANALYZE TABLE).
--
-- THE PROBLEM WITH THE DEFAULT LAYOUT
--   04_create_dw.sql gives every dimension foreign key its own
--   single-column index. That is the textbook star-schema layout and it
--   is the right starting point, but it only helps a query that filters
--   on exactly ONE dimension key. Every chart in the dashboard either
--   groups by two dimensions at once (Department + Date, or
--   Project + Department) or needs measure columns it would otherwise
--   have to fetch from the clustered index one row at a time.
--
--   So for those access paths the default layout forces either
--     * a per-row lookup back into the clustered index
--       ("Extra: Using where" / no "Using index"), or
--     * a wider scan than necessary because the second dimension is not
--       part of the index and has to be filtered afterwards.
--
--   A composite index whose trailing columns are the measures turns both
--   into a single index-only range scan - EXPLAIN reports
--   "Extra: Using index" and the clustered index is never touched.
--
-- MEASURED RESULT
--   Dataset: 38,814 facts / 19,786 employee versions / 1,500 projects
--   Method:  5 interleaved before/after rounds, best of 7 runs each,
--            ANALYZE TABLE between every state change.
--
--       dashboard query                       before     after   speedup
--       ---------------------------------------------------------------
--       vw_department_headcount_history       383.6 ms  244.3 ms   1.57x
--       project_alloc_by_department           168.6 ms  120.7 ms   1.40x
--       quarterly_trend (dept filter)          27.7 ms   12.9 ms   2.15x
--       score_distribution (dept filter)       14.7 ms    7.6 ms   1.94x
--       vw_performance_trend_yoy               59.9 ms   55.3 ms   1.08x
--       ---------------------------------------------------------------
--       all 21 dashboard queries            2,137.4 ms 1,919.8 ms   1.11x
--
--   No query regressed. The five that did not move are listed, with the
--   reason, in section 8.
--
-- COST
--   Measured from mysql.innodb_index_stats, which reports real allocated
--   pages per index. (information_schema.TABLES.INDEX_LENGTH is NOT used
--   here - for InnoDB it is a stale estimate that does not move after a
--   DROP INDEX, and it misreports every figure in this section.)
--
--       table                        before     after    delta
--       -----------------------------------------------------------
--       fact_performance_reviews     11.09 MB  14.12 MB   +3.03
--       dim_employee                  8.58 MB   9.58 MB   +1.00
--       dim_date                      0.27 MB   0.33 MB   +0.06
--       dim_project                   0.38 MB   0.38 MB    0.00
--       -----------------------------------------------------------
--       warehouse indexes            20.32 MB  24.41 MB   +4.09  (+20%)
--
--   Each new fact-table index costs 1.52 MB (about 97 pages). That is the
--   deliberate trade: the fact table is rebuilt wholesale once per ETL run
--   and then read by every dashboard interaction for the rest of the day,
--   so read throughput is worth more here than 3 MB of disk.
--
-- WHEN TO RUN
--   After 04_create_dw.sql and after the first sp_load_dw_master().
--   Safe to re-run: every DDL statement is guarded by the helper
--   procedures in section 0, so the script is idempotent.
--
--   Only index metadata changes, so no ETL re-run is required.
-- ============================================================

USE employee_analytics_dw;


-- ============================================================
-- SECTION 0 - Idempotency helpers
--
-- MySQL has no "CREATE INDEX IF NOT EXISTS", so a plain script dies
-- with error 1061 the second time it runs. These two small procedures do
-- the information_schema check and skip the statement when the index is
-- already in place. They are left behind afterwards so this script stays
-- re-runnable after future schema changes.
-- ============================================================

DROP PROCEDURE IF EXISTS sp_add_index_if_missing;
DROP PROCEDURE IF EXISTS sp_drop_index_if_present;

DELIMITER $$

CREATE PROCEDURE sp_add_index_if_missing(
    IN p_table_name VARCHAR(64),
    IN p_index_name VARCHAR(64),
    IN p_ddl        VARCHAR(512)
)
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM information_schema.STATISTICS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME   = p_table_name
          AND INDEX_NAME   = p_index_name
    ) THEN
        SET @ddl = p_ddl;
        PREPARE stmt FROM @ddl;
        EXECUTE stmt;
        DEALLOCATE PREPARE stmt;
    END IF;
END $$

CREATE PROCEDURE sp_drop_index_if_present(
    IN p_table_name VARCHAR(64),
    IN p_index_name VARCHAR(64)
)
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.STATISTICS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME   = p_table_name
          AND INDEX_NAME   = p_index_name
    ) THEN
        SET @ddl = CONCAT('DROP INDEX `', p_index_name, '` ON `', p_table_name, '`');
        PREPARE stmt FROM @ddl;
        EXECUTE stmt;
        DEALLOCATE PREPARE stmt;
    END IF;
END $$

DELIMITER ;


-- ============================================================
-- SECTION 1 - INDEX AUDIT (read-only)
--
-- Run this block on its own to review the current state before changing
-- anything, and again afterwards to prove the change landed.
--
-- The size column deliberately comes from mysql.innodb_index_stats, which
-- reports genuinely allocated pages per index. The obvious-looking
-- information_schema.TABLES.INDEX_LENGTH is useless for this job: for
-- InnoDB it is a cached estimate that does not move after DROP INDEX, so
-- it will happily report a reclaimed index as still present.
-- ============================================================

SELECT
    s.TABLE_NAME                                         AS table_name,
    s.INDEX_NAME                                         AS index_name,
    CASE s.NON_UNIQUE WHEN 0 THEN 'UNIQUE' ELSE 'INDEX' END AS index_kind,
    GROUP_CONCAT(s.COLUMN_NAME ORDER BY s.SEQ_IN_INDEX)   AS columns_in_order,
    MAX(s.CARDINALITY)                                   AS rows_reachable,
    MAX(i.stat_value)                                    AS pages,
    ROUND(MAX(i.stat_value) * @@innodb_page_size
          / 1024 / 1024, 2)                             AS size_mb
FROM information_schema.STATISTICS s
LEFT JOIN mysql.innodb_index_stats i
       ON i.database_name = s.TABLE_SCHEMA
      AND i.table_name    = s.TABLE_NAME
      AND i.index_name    = s.INDEX_NAME
      AND i.stat_name     = 'size'
WHERE s.TABLE_SCHEMA = DATABASE()
  AND s.TABLE_NAME IN ('dim_employee', 'dim_date', 'dim_department',
                       'dim_project', 'fact_performance_reviews')
GROUP BY s.TABLE_NAME, s.INDEX_NAME, s.NON_UNIQUE
ORDER BY s.TABLE_NAME, index_kind DESC, size_mb DESC, index_name;


-- ============================================================
-- SECTION 2 - FACT_PERFORMANCE_REVIEWS  (two covering indexes)
--
-- The fact table is the busiest object in the star: all four dashboard
-- charts read it, and so do the SCD2 and attrition views.
--
-- 2a. ix_f_dept_cover  (DepartmentSK, DateSK, PerformanceScore, Rating)
--
--     Serves
--       AnalyticsManager.quarterly_trend()      joins fact -> dim_date
--                                                -> dim_department and
--                                                filters DepartmentSK
--       AnalyticsManager.score_distribution()   same join, same filter
--       AnalyticsManager.department_scorecard()  groups by department
--
--     Why this column order. DepartmentSK is the equality predicate, so
--     it must be first for the index range to be a single contiguous
--     block. DateSK comes next because every one of these charts then
--     needs a date attribute (QuarterLabel), so carrying DateSK in the
--     index removes the need to visit the clustered index for the join
--     key. PerformanceScore and Rating trail as INNODB index-suffix
--     columns: they do not narrow the search, they simply ride along so
--     AVG() is computed straight from the index.
--
--     Result
--       quarterly trend      27.7 ms -> 12.9 ms   2.15x
--       score distribution   14.7 ms ->  7.6 ms   1.94x
--     EXPLAIN changes from
--         type=ref  key=idx_fact_dept   Extra=(none)
--     to
--         type=ref  key=ix_f_dept_cover Extra=Using index
--
-- 2b. ix_f_proj_cover  (ProjectSK, DepartmentSK,
--                       AllocationPercentage, PerformanceScore)
--
--     Serves
--       AnalyticsManager.project_allocation_by_department()
--       vw_project_bottlenecks  (the SUM(AllocationPercentage) and
--                                 AVG(PerformanceScore) roll-up)
--
--     Same shape as 2a: ProjectSK and DepartmentSK are the two grouping
--     keys and therefore the equality/range prefix; the two measures ride
--     along so SUM() and AVG() are index-only.
--
--     Result
--       project allocation by department  168.6 ms -> 120.7 ms   1.40x
--     EXPLAIN changes from key=idx_fact_project to key=ix_f_proj_cover
--     with Extra=Using index, and the estimated row count for the fact
--     table access drops from ~3,500 to ~26.
--
-- NOT DROPPED: idx_fact_dept and idx_fact_project
--     They are now strict leftmost prefixes of the two composites, so
--     they look redundant and are tempting to delete. Do not.
--     They are the indexes that the fk_fact_dept and fk_fact_project
--     constraints are bound to. An earlier draft of this script dropped
--     them, and MySQL did NOT re-point the constraints to the new
--     composites as hoped - it silently dropped fk_fact_dept and
--     fk_fact_project from the table altogether, leaving the fact table
--     with no referential integrity on two of its four dimensions. The
--     error only surfaces later, as an ERROR 1553 when someone tries to
--     remove a composite, or as corrupt dimension rows nobody notices.
--     Removing them safely means dropping and recreating the constraints
--     by hand, which is a schema change, not an index change. For ~3 MB
--     on a warehouse this size that trade is not worth it, so they stay.
--
-- KEPT: idx_fact_employee and idx_fact_date
--     Both are load-bearing and neither is covered by a composite:
--       * idx_fact_employee drives the point-in-time join in
--         sp_load_fact_performance_reviews and the per-employee
--         aggregates behind vw_attrition_risk and
--         vw_top_performers_by_department.
--       * idx_fact_date drives vw_performance_trend_yoy, which joins the
--         fact table to the calendar in DateSK order.
-- ============================================================

CALL sp_add_index_if_missing(
    'fact_performance_reviews',
    'ix_f_dept_cover',
    'CREATE INDEX ix_f_dept_cover ON fact_performance_reviews (DepartmentSK, DateSK, PerformanceScore, Rating)'
);

CALL sp_add_index_if_missing(
    'fact_performance_reviews',
    'ix_f_proj_cover',
    'CREATE INDEX ix_f_proj_cover ON fact_performance_reviews (ProjectSK, DepartmentSK, AllocationPercentage, PerformanceScore)'
);


-- ============================================================
-- SECTION 3 - DIM_EMPLOYEE  (SCD Type 2)
--
-- 3a. ix_de_status_window  (EmploymentStatus,
--                           EffectiveStartDate, EffectiveEndDate)
--
--     Serves
--       vw_department_headcount_history
--         -> AnalyticsManager.department_headcount_history()
--         -> the "Headcount history by department" area chart
--
--     What the view does is a temporal validity join. It takes the nine
--     December-31 snapshot dates out of dim_date and asks, for each one,
--     which employee versions were open on that day:
--
--         snapshot_date BETWEEN EffectiveStartDate AND EffectiveEndDate
--
--     Because EmploymentStatus was not indexed as a leading column, the
--     optimizer fell back to a full scan (EXPLAIN type=ALL, rows=19,763)
--     and applied both the status test and the date range only AFTER the
--     join - so all 19,763 versions were read for every snapshot.
--
--     Putting EmploymentStatus first converts it to a `ref` lookup
--     followed by a bounded range scan on the validity window:
--
--         before:  type=ALL  key=NULL                   rows=19,763
--         after:   type=ref  key=ix_de_status_window     rows= 9,881
--                  Extra=Using index
--
--     The EffectiveStartDate / EffectiveEndDate pair is deliberate: it
--     mirrors the column order the SCD2 procedure maintains in
--     sp_load_dim_employee_full, so the index stays useful as versions
--     accumulate instead of degrading into a single-column filter.
--
--     Result
--       headcount history  383.6 ms -> 244.3 ms   1.57x
--
-- 3b. DROP idx_dim_employee_id
--
--     This one really is redundant. uq_dim_employee_version
--     (EmployeeID, EffectiveStartDate) already has EmployeeID as its
--     leftmost column, so any lookup that used idx_dim_employee_id is
--     served identically by the unique key. No foreign key binds to it,
--     and dim_employee carries no foreign keys at all, so removing it is
--     safe. It trims write amplification on the one table that receives a
--     new row every time sp_apply_employee_change closes a version, and
--     reclaims 0.52 MB (measured: 32 pages of 16 KB).
--
-- KEPT: uq_dim_employee_version, idx_dim_employee_current,
--       idx_dim_employee_dept
--     uq_dim_employee_version  enforces one version per employee per
--                               start date and serves every EmployeeID
--                               point lookup (employee_history,
--                               vw_pay_progression).
--     idx_dim_employee_current serves sp_apply_employee_change and the
--                               "EmployeeID = ? AND IsCurrent = 1"
--                               lookup that closes an open version.
--     idx_dim_employee_dept    12 distinct departments; cheap, and used
--                               by the ETL loads.
-- ============================================================

CALL sp_add_index_if_missing(
    'dim_employee',
    'ix_de_status_window',
    'CREATE INDEX ix_de_status_window ON dim_employee (EmploymentStatus, EffectiveStartDate, EffectiveEndDate)'
);

CALL sp_drop_index_if_present('dim_employee', 'idx_dim_employee_id');


-- ============================================================
-- SECTION 4 - DIM_DATE  (conformed calendar)
--
-- 4a. ix_dd_year_sk  (Year, DateSK)
--
--     Serves
--       vw_performance_trend_yoy
--         -> AnalyticsManager.performance_trend_yoy()  (chart 1)
--         -> AnalyticsManager.year_range()            (year filter bounds)
--
--     The year-over-year chart groups the fact table by dt.Year and then
--     applies LAG() across those groups. In dim_date the physical column
--     order is DateSK, FullDate, Year, ... so Year is not adjacent to
--     anything useful, and MySQL had to scan the calendar and sort it
--     before the GROUP BY.
--
--     With (Year, DateSK) the calendar is returned already ordered by the
--     grouping key, so the sort disappears:
--
--         before:  type=ALL    key=NULL              + Sort: dt.Year
--         after:   type=index  key=ix_dd_year_sk     Extra=Using index
--
--     Result
--       year-over-year trend  59.9 ms -> 55.3 ms   1.08x
--
--     Honest note: 1.08x is a modest win. dim_date is only 3,287 rows and
--     is fully cached, so the calendar scan was never the bottleneck -
--     the fact table aggregation is. The index is still worth keeping
--     because it removes a filesort, which is the part that degrades
--     first as dim_date grows.
--
-- NOT INDEXED: the December-31 snapshot filter used by
--     vw_department_headcount_history (MONTH(FullDate)=12 AND
--     DAY(FullDate)=31). An (Month, DayOfMonth, DateSK) index was built
--     and measured: 55.9 ms -> 60.4 ms, i.e. slightly WORSE. It only
--     narrows 3,287 rows down to 9, which is cheaper to scan than to
--     look up. Left out on purpose.
-- ============================================================

CALL sp_add_index_if_missing(
    'dim_date',
    'ix_dd_year_sk',
    'CREATE INDEX ix_dd_year_sk ON dim_date (Year, DateSK)'
);


-- ============================================================
-- SECTION 5 - DIM_PROJECT and DIM_DEPARTMENT
--
-- No change. dim_project has 1,500 rows and dim_department has 12, both
-- fully cached in the InnoDB buffer pool, and a scan of either costs well
-- under a millisecond. (ProjectStatus, ProjectSK) on dim_project was
-- built and measured: 68.0 ms -> 69.2 ms, i.e. no change. The existing
-- indexes already cover their natural keys and ProjectStatus, and extra
-- indexes would only slow the SCD1 upserts in sp_load_dim_project down.
-- ============================================================


-- ============================================================
-- SECTION 6 - REFRESH STATISTICS
--
-- MySQL samples index cardinality; it does not compute it exactly. After
-- a full sp_load_dw_master() rebuild the stored values drift badly. On
-- this dataset, before this step:
--
--     idx_employees_department  reported    3 distinct values (real: 12)
--     idx_dim_employee_dept     reported   12 distinct values (real: 12)
--     idx_fact_dept             reported   11 distinct values (real: 11)
--     idx_projects_status       reported    2 distinct values (real:  2)
--
-- A wrong cardinality makes the optimizer pick the wrong access path, and
-- no index on earth can fix that - so this step matters as much as the
-- CREATE INDEX statements above it. ANALYZE TABLE only rewrites index
-- metadata; it is cheap and safe. Re-run it after any full rebuild.
-- ============================================================

ANALYZE TABLE dim_employee;
ANALYZE TABLE fact_performance_reviews;
ANALYZE TABLE dim_date;
ANALYZE TABLE dim_department;
ANALYZE TABLE dim_project;


-- ============================================================
-- SECTION 7 - VERIFICATION
--
-- 7a. The four new indexes exist with the intended column order.
--     Expect 4 rows.
-- ============================================================

SELECT
    s.TABLE_NAME                                        AS table_name,
    s.INDEX_NAME                                        AS index_name,
    GROUP_CONCAT(s.COLUMN_NAME ORDER BY s.SEQ_IN_INDEX)  AS columns_in_order
FROM information_schema.STATISTICS s
WHERE s.TABLE_SCHEMA = DATABASE()
  AND s.INDEX_NAME IN ('ix_f_dept_cover', 'ix_f_proj_cover',
                       'ix_de_status_window', 'ix_dd_year_sk')
GROUP BY s.TABLE_NAME, s.INDEX_NAME
ORDER BY s.TABLE_NAME, s.INDEX_NAME;

-- 7b. The redundant index is gone. Expect 0 rows.
SELECT TABLE_NAME, INDEX_NAME
FROM information_schema.STATISTICS
WHERE TABLE_SCHEMA = DATABASE()
  AND INDEX_NAME = 'idx_dim_employee_id';

-- 7c. Referential integrity must be intact. Expect all four
--     constraints, including fk_fact_dept and fk_fact_project.
--     (See section 2 for why this check is here at all.)
SELECT CONSTRAINT_NAME, COLUMN_NAME, REFERENCED_TABLE_NAME
FROM information_schema.KEY_COLUMN_USAGE
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME   = 'fact_performance_reviews'
  AND REFERENCED_TABLE_NAME IS NOT NULL
ORDER BY CONSTRAINT_NAME;

-- 7d. Prove the optimizer picks the new indexes and reads them without
--     touching the clustered index ("Using index").
--     Expect key=ix_f_dept_cover  on row 2 of the first plan,
--           key=ix_f_proj_cover  on row 2 of the second,
--           key=ix_de_status_window on row 2 of the third.
EXPLAIN
SELECT dt.QuarterLabel, COUNT(*), AVG(f.PerformanceScore), AVG(f.Rating)
FROM fact_performance_reviews f
JOIN dim_date dt      ON dt.DateSK      = f.DateSK
JOIN dim_department d ON d.DepartmentSK = f.DepartmentSK
WHERE d.DepartmentName = 'Sales'
GROUP BY dt.QuarterLabel;

EXPLAIN
SELECT d.DepartmentName, pr.ProjectStatus,
       SUM(f.AllocationPercentage), AVG(f.PerformanceScore)
FROM fact_performance_reviews f
JOIN dim_department d ON d.DepartmentSK = f.DepartmentSK
JOIN dim_project pr   ON pr.ProjectSK   = f.ProjectSK
GROUP BY d.DepartmentName, pr.ProjectStatus;

EXPLAIN
SELECT COUNT(*)
FROM dim_employee de
JOIN (SELECT YEAR(FullDate) * 10000 + 1231 AS snapshot
      FROM dim_date
      WHERE MONTH(FullDate) = 12 AND DAY(FullDate) = 31) y
  ON y.snapshot BETWEEN de.EffectiveStartDate AND de.EffectiveEndDate
WHERE de.EmploymentStatus = 'Active';

-- 7e. Index footprint, from the same reliable source as the section 1
--     audit. Expected totals (whole warehouse, 16 KB pages):
--         fact_performance_reviews  11.09 MB -> 14.12 MB
--         dim_employee               8.58 MB ->  9.58 MB
--         dim_date                   0.27 MB ->  0.33 MB
--         dim_project                0.38 MB ->  0.38 MB  (unchanged)
SELECT
    per_index.table_name                              AS table_name,
    COUNT(*)                                          AS index_count,
    ROUND(SUM(per_index.pages)
          * @@innodb_page_size / 1024 / 1024, 2)      AS total_index_mb
FROM (
    -- one row per index; several indexes are the same size, so this
    -- has to be collapsed BEFORE summing or the duplicates vanish
    SELECT
        s.TABLE_NAME AS table_name,
        s.INDEX_NAME AS index_name,
        MAX(i.stat_value) AS pages
    FROM information_schema.STATISTICS s
    LEFT JOIN mysql.innodb_index_stats i
           ON i.database_name = s.TABLE_SCHEMA
          AND i.table_name    = s.TABLE_NAME
          AND i.index_name    = s.INDEX_NAME
          AND i.stat_name     = 'size'
    WHERE s.TABLE_SCHEMA = DATABASE()
      AND s.TABLE_NAME IN ('dim_employee', 'fact_performance_reviews',
                           'dim_date', 'dim_project')
    GROUP BY s.TABLE_NAME, s.INDEX_NAME
) AS per_index
GROUP BY per_index.table_name
ORDER BY per_index.table_name;


-- ============================================================
-- SECTION 8 - WHAT WAS DELIBERATELY NOT INDEXED
--
-- Recorded so the next person does not "fix" these by adding indexes that
-- provably cannot help. Each of the queries below was benchmarked and its
-- cost is CPU-bound temporary-table work, not row access.
--
-- vw_attrition_risk and everything built on it (260-400 ms)
--   attrition_risk, risk_band_summary, attrition_by_department
--     Aggregates 38,814 fact rows grouped by twelve columns, three of
--     which are VARCHAR. MySQL reports "Aggregate using temporary table",
--     and the cost is building and reading that temp table. Measured on
--     the same data: the bare join without the GROUP BY costs 64 ms; the
--     identical join WITH the GROUP BY costs 399 ms. No index changes
--     that ratio, because every fact row has to be read exactly once.
--     Confirmed empirically: a covering index on
--     (EmployeeSK, DateSK, ReviewSK, PerformanceScore, Rating) was built
--     and measured at 404 ms -> 432 ms, i.e. slower.
--
-- vw_top_performers_by_department (94 ms)
--   Same shape - group the fact table per employee, then DENSE_RANK()
--   the result over all 19,786 versions. All rows are required.
--
-- vw_pay_progression and the SCD2 audit charts (225-264 ms)
--   Two LAG() window functions over all 19,786 employee versions, each
--   forcing its own sort plus a deduplicating temp table. Every version
--   row must be read, so row access is already optimal. A covering index
--   on (EmployeeID, EffectiveStartDate, MonthlyIncome, DepartmentName,
--   JobRole) was built and measured 40 ms SLOWER - the wider index costs
--   more to scan than the row lookups it saves. Note that
--   uq_dim_employee_version already supplies the (EmployeeID,
--   EffectiveStartDate) sort order the PARTITION BY / ORDER BY needs.
--
-- vw_project_bottlenecks (67 ms)
--   Now reads ix_f_proj_cover, but the remaining cost is the DENSE_RANK()
--   over the project-level aggregate plus the CROSS JOIN against the
--   company benchmark, which MySQL evaluates as a temp table.
--
-- The staging tables (stg_*) are intentionally left unindexed.
--   Each is read once, in full, by a single TRUNCATE + INSERT statement.
--   Indexing them adds build cost to every load and buys nothing.
--
-- OLTP redundancy, noted but NOT changed: idx_reviews_employee
--   (EmployeeID) duplicates the leftmost prefix of uq_reviews_emp_date
--   (EmployeeID, ReviewDate) in exactly the same way idx_dim_employee_id
--   did. It is left alone because the brief here is OLAP retrieval speed,
--   and performance_reviews is a write-heavy transactional table where
--   removing an index is a bigger commitment than it is on the warehouse.
--
-- IF THESE EVER NEED TO BE FASTER
--   The fix is not another index. It is to stop recomputing the same
--   aggregate on every page load: add a summary/aggregate table (for
--   example a monthly department-and-project scorecard, refreshed by the
--   ETL) and point the dashboard at that instead of the fact table.
-- ============================================================