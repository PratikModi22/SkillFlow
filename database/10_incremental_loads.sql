-- ============================================================
-- 10_incremental_loads.sql
-- Incremental (near real time) path from the OLTP into the star
-- schema, plus the reconciliation view that proves OLTP and OLAP
-- agree.
--
-- Why this exists
-- ---------------
-- 06_etl_oltp_to_dw.sql is a FULL rebuild: it replays ~20k dimension
-- versions and ~39k facts. That is the right way to recover from
-- anything, and the wrong way to publish one new performance review.
-- The Streamlit app writes to the OLTP continuously, so those writes
-- need a cheap path into the warehouse.
--
--   vw_dim_employee_drift        OLTP vs the newest Dim_Employee
--                                version, attribute by attribute.
--   sp_load_dim_employee_delta() seeds employees that have no version
--                                at all and versions the drifted ones.
--   sp_load_fact_delta()         upserts the recent review window plus
--                                any review the fact table never got.
--   sp_repair_dim_employee_versions()
--                                defined in 06: exactly one open
--                                version per employee.
--   sp_reconcile_dw()            all of the above, then reports.
--   vw_oltp_dw_reconciliation    the numbers the UI grades itself on.
--
-- Cost model on the current data set (15,008 employees / 20k versions
-- / 38,814 reviews):
--   sp_load_dim_employee_delta   ~2 index probes per employee, then a
--                                 cursor over the drift set (0 rows in
--                                 steady state)
--   sp_load_fact_delta           a 30 day window (~300 reviews) plus an
--                                 anti-join against the fact table
--   both together well under a second, against ~5 s for the full
--   rebuild.
-- ============================================================

USE employee_analytics_dw;

DROP PROCEDURE IF EXISTS sp_load_dim_employee_delta;
DROP PROCEDURE IF EXISTS sp_load_fact_delta;
DROP PROCEDURE IF EXISTS sp_reconcile_dw;

-- ============================================================
-- VW_DIM_EMPLOYEE_DRIFT
--
-- Every employee whose CURRENT OLTP state does not match the newest
-- Dim_Employee version the warehouse holds. This is the single source
-- of truth for "what still has to be published", and it is reused by
-- the delta loader, by the reconciliation panel in the app, and by the
-- verification query in etl/run_pipeline.py.
--
-- ROW_NUMBER() picks the newest version per employee: the version to
-- compare against is always the one with the greatest
-- EffectiveStartDate, never "the one with IsCurrent = 1", because a
-- terminated employee has no open version at all.
--
-- <=> is the NULL-safe equality operator. Without it, setting
-- ManagerEmployeeID or Gender to NULL would read as "different" on
-- every call and version the employee forever.
--
-- SCOPE: EMPLOYEES WITHOUT A STAGING HISTORY ONLY. This is the one
-- subtlety in the whole incremental path, so it is worth spelling out.
--
-- The synthesizer builds `employees` from the IBM snapshot and then
-- generates `employee_history` FORWARD from it: promotions, transfers
-- and raises dated after the snapshot. The newest history version
-- therefore does not describe the current `employees` row - 4,761 of
-- 15,000 employees differ (see employees_history_newer_than_oltp in
-- vw_oltp_dw_reconciliation). The warehouse is right to trust the
-- history: that forward-looking history is the entire point of the
-- SCD2 demo.
--
-- So for the 15,000 employees that have history, dim_employee is loaded
-- from stg_employee_history (sp_load_dim_employee_full) and the OLTP
-- row is NOT authoritative for the tracked attributes. Comparing them
-- would "discover" 4,761 phantom changes and version a third of the
-- company on the next sync.
--
-- For an employee with NO history - someone created through the Streamlit
-- app, or an employee whose source file never mentioned them - the OLTP
-- row IS the only source of truth, which is exactly the population this
-- view covers.
-- ============================================================

CREATE OR REPLACE VIEW vw_dim_employee_drift AS
WITH latest_version AS (
    SELECT
        d.EmployeeID,
        d.FirstName,
        d.LastName,
        d.Email,
        d.Gender,
        d.JobRole,
        d.JobLevel,
        d.MonthlyIncome,
        d.EmploymentStatus,
        d.HireDate,
        d.TerminationDate,
        d.DepartmentID,
        d.DepartmentName,
        d.ManagerEmployeeID,
        d.EffectiveStartDate,
        ROW_NUMBER() OVER (
            PARTITION BY d.EmployeeID
            ORDER BY d.EffectiveStartDate DESC
        ) AS version_rank
    FROM dim_employee d
)
SELECT
    e.EmployeeID,
    e.DepartmentID                    AS oltp_department_id,
    lv.DepartmentID                   AS dw_department_id,
    e.JobRole                         AS oltp_job_role,
    lv.JobRole                        AS dw_job_role,
    e.JobLevel                        AS oltp_job_level,
    lv.JobLevel                       AS dw_job_level,
    e.MonthlyIncome                   AS oltp_monthly_income,
    lv.MonthlyIncome                  AS dw_monthly_income,
    e.ManagerEmployeeID               AS oltp_manager_id,
    lv.ManagerEmployeeID              AS dw_manager_id,
    lv.EffectiveStartDate             AS dw_version_start
FROM employees e
JOIN latest_version lv
  ON lv.EmployeeID = e.EmployeeID
 AND lv.version_rank = 1
LEFT JOIN departments dep ON dep.DepartmentID = e.DepartmentID
WHERE NOT EXISTS (
          SELECT 1 FROM stg_employee_history h WHERE h.EmployeeID = e.EmployeeID
      )
  AND NOT (
           lv.FirstName         <=> e.FirstName
       AND lv.LastName          <=> e.LastName
       AND lv.Email             <=> e.Email
       AND lv.Gender            <=> e.Gender
       AND lv.JobRole           <=> e.JobRole
       AND lv.JobLevel          <=> e.JobLevel
       AND lv.MonthlyIncome     <=> e.MonthlyIncome
       AND lv.EmploymentStatus  <=> e.EmploymentStatus
       AND lv.DepartmentID      <=> e.DepartmentID
       AND lv.DepartmentName    <=> dep.DepartmentName
       AND lv.ManagerEmployeeID <=> e.ManagerEmployeeID
       AND lv.HireDate          <=> e.HireDate
       AND lv.TerminationDate   <=> e.TerminationDate
);

-- ============================================================
-- SP_LOAD_DIM_EMPLOYEE_DELTA
--
-- Set-based for the bulk case, a cursor for the exception case:
--
--   1. SEED - employees with no Dim_Employee version AND no
--      stg_employee_history row. Those are employees created through
--      the Streamlit app: no CSV history exists, so sp_load_dim_employee
--      _full() cannot invent a version for them either. This one INSERT
--      also repairs any employee left behind by an earlier failure.
--
--   2. VERSION - everyone in vw_dim_employee_drift. Each gets a fresh
--      SCD2 version dated today, because an attribute moved outside the
--      app (a manual UPDATE, a spreadsheet import) and the warehouse
--      has to catch up. In steady state this set is empty, which is
--      why a cursor is affordable here.
--
-- sp_apply_employee_change does the versioning, including its change
-- detection, so this procedure never invents versions of its own and
-- the two paths cannot drift apart.
-- ============================================================

DELIMITER $$
CREATE PROCEDURE sp_load_dim_employee_delta()
BEGIN
    DECLARE v_seeded  INT DEFAULT 0;
    DECLARE v_drifted INT DEFAULT 0;
    DECLARE v_id      VARCHAR(50);
    DECLARE v_done    INT DEFAULT 0;
    DECLARE v_batch   BIGINT DEFAULT NULL;

    DECLARE cur_drift CURSOR FOR
        SELECT EmployeeID FROM vw_dim_employee_drift;

    DECLARE CONTINUE HANDLER FOR NOT FOUND SET v_done = 1;

    INSERT INTO dw_load_log (ProcedureName, RunStatus)
    VALUES ('sp_load_dim_employee_delta', 'RUNNING');
    SET v_batch = LAST_INSERT_ID();

    -- 1. seed employees the warehouse has never seen
    INSERT INTO dim_employee
        (EmployeeID, FirstName, LastName, Email, Gender,
         JobRole, JobLevel, MonthlyIncome, EmploymentStatus,
         HireDate, TerminationDate, DepartmentID, DepartmentName,
         ManagerEmployeeID, YearsOfService,
         EffectiveStartDate, EffectiveEndDate, IsCurrent)
    SELECT
        e.EmployeeID,
        e.FirstName,
        e.LastName,
        e.Email,
        e.Gender,
        e.JobRole,
        COALESCE(e.JobLevel, 1),
        COALESCE(e.MonthlyIncome, 0),
        e.EmploymentStatus,
        e.HireDate,
        e.TerminationDate,
        e.DepartmentID,
        d.DepartmentName,
        e.ManagerEmployeeID,
        ROUND(TIMESTAMPDIFF(MONTH, e.HireDate, CURDATE()) / 12, 2),
        e.HireDate,
        CASE
            WHEN e.EmploymentStatus = 'Terminated'
                 AND e.TerminationDate IS NOT NULL
                 AND e.TerminationDate >= e.HireDate
            THEN e.TerminationDate
            ELSE CAST('9999-12-31' AS DATE)
        END,
        CASE
            WHEN e.EmploymentStatus = 'Terminated'
                 AND e.TerminationDate IS NOT NULL
                 AND e.TerminationDate >= e.HireDate
            THEN 0
            ELSE 1
        END
    FROM employees e
    JOIN departments d ON d.DepartmentID = e.DepartmentID
    WHERE NOT EXISTS (
              SELECT 1 FROM dim_employee x WHERE x.EmployeeID = e.EmployeeID
          )
      AND NOT EXISTS (
              SELECT 1 FROM stg_employee_history h WHERE h.EmployeeID = e.EmployeeID
          )
      AND e.HireDate IS NOT NULL;

    SET v_seeded = ROW_COUNT();

    -- 2. version the employees whose attributes moved
    SELECT COUNT(*) INTO v_drifted FROM vw_dim_employee_drift;

    IF v_drifted > 0 THEN
        OPEN cur_drift;
        drift_loop: LOOP
            FETCH cur_drift INTO v_id;
            IF v_done = 1 THEN
                LEAVE drift_loop;
            END IF;
            CALL sp_apply_employee_change(v_id, CURDATE(), 0);
        END LOOP;
        CLOSE cur_drift;
    END IF;

    UPDATE dw_load_log
       SET RunEndTime = CURRENT_TIMESTAMP,
           RowsAffected = v_seeded + v_drifted,
           RunStatus   = 'SUCCESS'
     WHERE DWLoadID = v_batch;

    SELECT v_seeded  AS employees_seeded,
           v_drifted AS employees_versioned;
END $$
DELIMITER ;

-- ============================================================
-- SP_LOAD_FACT_DELTA(p_LookbackDays)
--
-- Upserts the fact rows that can actually have changed:
--
--   * every review inside the rolling window, because a Dim_Employee
--     version created by a transfer re-points the point-in-time join
--     for any review dated after the transfer, and the transfer can be
--     back dated, and
--   * every review the fact table has never seen, whatever its date.
--
-- Reviews older than the window are immutable history: their employee
-- version, their department and their project assignment cannot change
-- retroactively, so they are left alone. That is what keeps the load
-- proportional to the change rate instead of to the table size.
--
-- The upsert key is ReviewID (uq_fact_review_id), so re-running the
-- procedure refreshes a row instead of duplicating it, which makes the
-- whole thing idempotent and safe to retry after a failure.
--
-- LoadBatchID stamps each row with the dw_load_log run that produced
-- it, so a fact row can be traced back to the pipeline run.
-- ============================================================

DELIMITER $$
CREATE PROCEDURE sp_load_fact_delta(
    IN p_LookbackDays INT
)
BEGIN
    DECLARE v_cutoff DATE;
    DECLARE v_rows  INT DEFAULT 0;
    DECLARE v_new   INT DEFAULT 0;
    DECLARE v_batch BIGINT DEFAULT NULL;

    SET p_LookbackDays = LEAST(GREATEST(COALESCE(p_LookbackDays, 30), 0), 3650);
    SET v_cutoff = DATE_SUB(CURDATE(), INTERVAL p_LookbackDays DAY);

    INSERT INTO dw_load_log (ProcedureName, RunStatus)
    VALUES ('sp_load_fact_delta', 'RUNNING');
    SET v_batch = LAST_INSERT_ID();

    -- Counted BEFORE the upsert, because afterwards every row looks
    -- updated. This is the number the app shows as "new reviews found".
    SELECT COUNT(*) INTO v_new
      FROM performance_reviews r
     WHERE (r.ReviewDate >= v_cutoff
            OR NOT EXISTS (SELECT 1 FROM fact_performance_reviews f
                            WHERE f.ReviewID = r.ReviewID))
       AND NOT EXISTS (SELECT 1 FROM fact_performance_reviews f
                        WHERE f.ReviewID = r.ReviewID);

    INSERT INTO fact_performance_reviews
        (ReviewID, EmployeeSK, DateSK, DepartmentSK, ProjectSK,
         Rating, PerformanceScore, PromotionRecommended, ReviewCount,
         AttritionFlag, TenureYears, AllocationPercentage, LoadBatchID)

    WITH project_pick AS (
        SELECT
            r.ReviewID,
            a.ProjectID,
            a.AllocationPercentage,
            ROW_NUMBER() OVER (
                PARTITION BY r.ReviewID
                ORDER BY
                    (CASE WHEN a.AssignmentEndDate IS NULL
                            OR r.ReviewDate <= a.AssignmentEndDate
                           THEN 0 ELSE 1 END),
                    a.AssignmentStartDate DESC,
                    a.AllocationPercentage DESC
            ) AS rn
        FROM performance_reviews r
        JOIN employee_project_assignments a
          ON  a.EmployeeID = r.EmployeeID
          AND a.AssignmentStartDate <= r.ReviewDate
    )
    SELECT
        r.ReviewID,
        de.EmployeeSK,
        dd.DateSK,
        dp.DepartmentSK,
        dpr.ProjectSK,
        r.Rating,
        r.PerformanceScore,
        CASE WHEN r.PromotionRecommendation = 'Yes' THEN 1 ELSE 0 END,
        1,
        CASE WHEN de.TerminationDate IS NOT NULL THEN 1 ELSE 0 END,
        ROUND(TIMESTAMPDIFF(MONTH, de.HireDate, r.ReviewDate) / 12, 2),
        pp.AllocationPercentage,
        v_batch
    FROM performance_reviews r
    JOIN dim_employee de
      ON  de.EmployeeSK = (
            SELECT x.EmployeeSK
            FROM dim_employee x
            WHERE x.EmployeeID = r.EmployeeID
              AND r.ReviewDate BETWEEN x.EffectiveStartDate
                                   AND x.EffectiveEndDate
            ORDER BY x.EffectiveStartDate DESC
            LIMIT 1)
    JOIN dim_date dd       ON dd.FullDate = r.ReviewDate
    JOIN dim_department dp ON dp.DepartmentID = de.DepartmentID
    LEFT JOIN project_pick pp  ON pp.ReviewID = r.ReviewID AND pp.rn = 1
    LEFT JOIN dim_project dpr  ON dpr.ProjectID = pp.ProjectID
    WHERE r.ReviewDate >= v_cutoff
       OR NOT EXISTS (
              SELECT 1
              FROM fact_performance_reviews f
              WHERE f.ReviewID = r.ReviewID
          )
    ON DUPLICATE KEY UPDATE
        EmployeeSK           = VALUES(EmployeeSK),
        DateSK               = VALUES(DateSK),
        DepartmentSK         = VALUES(DepartmentSK),
        ProjectSK            = VALUES(ProjectSK),
        Rating               = VALUES(Rating),
        PerformanceScore     = VALUES(PerformanceScore),
        PromotionRecommended = VALUES(PromotionRecommended),
        ReviewCount          = VALUES(ReviewCount),
        AttritionFlag        = VALUES(AttritionFlag),
        TenureYears          = VALUES(TenureYears),
        AllocationPercentage = VALUES(AllocationPercentage),
        LoadBatchID          = VALUES(LoadBatchID);

    -- NOT ROW_COUNT(): MySQL counts 2 for every row an
    -- ON DUPLICATE KEY UPDATE touched, so a refresh-only run would
    -- claim twice as many rows as it wrote. Counting the rows this
    -- batch stamped gives the exact number.
    SELECT COUNT(*) INTO v_rows
      FROM fact_performance_reviews
     WHERE LoadBatchID = v_batch;

    UPDATE dw_load_log
       SET RunEndTime   = CURRENT_TIMESTAMP,
           RowsAffected = v_rows,
           RunStatus    = 'SUCCESS'
     WHERE DWLoadID = v_batch;

    SELECT v_rows AS fact_rows_refreshed,
           v_new  AS new_reviews,
           v_cutoff AS window_start;
END $$
DELIMITER ;

-- ============================================================
-- VW_OLTP_DW_RECONCILIATION
--
-- One row, every reason the warehouse and the OLTP could disagree.
-- The app shows it on the Warehouse tab and etl/run_pipeline.py fails
-- the build when the "must be zero" columns are not.
--
-- drifted_employees is expected to be non-zero only while someone has
-- edited an app-created employee outside the app and has not run a
-- sync yet; that is exactly the window the reconciliation panel exists
-- to make visible. It deliberately excludes employees that have a
-- staging history - see the note on vw_dim_employee_drift.
--
-- employees_history_newer_than_oltp is the flip side and is NOT an
-- error: it counts the employees whose synthesized history describes a
-- promotion or transfer that was never written back to the `employees`
-- snapshot. It is reported so that the number is visible instead of
-- looking like a defect.
-- ============================================================

CREATE OR REPLACE VIEW vw_oltp_dw_reconciliation AS
SELECT
    (SELECT COUNT(*) FROM employees)                                        AS oltp_employees,
    (SELECT COUNT(*) FROM employees
      WHERE EmploymentStatus = 'Active')                                    AS oltp_active,
    (SELECT COUNT(DISTINCT EmployeeID) FROM dim_employee)                  AS dw_employees,
    (SELECT COUNT(*) FROM dim_employee WHERE IsCurrent = 1)                 AS dw_open_versions,
    (SELECT COUNT(*) FROM employees e
      WHERE NOT EXISTS (SELECT 1 FROM dim_employee d
                         WHERE d.EmployeeID = e.EmployeeID))                AS employees_missing_from_dim,
    (SELECT COUNT(*) FROM dim_employee d
      WHERE NOT EXISTS (SELECT 1 FROM employees e
                         WHERE e.EmployeeID = d.EmployeeID))                AS dim_rows_without_employee,
    (SELECT COUNT(*) FROM vw_dim_employee_drift)                            AS drifted_employees,
    (SELECT COUNT(*) FROM performance_reviews r
      WHERE NOT EXISTS (SELECT 1 FROM fact_performance_reviews f
                         WHERE f.ReviewID = r.ReviewID))                    AS reviews_missing_from_fact,
    (SELECT COUNT(*) FROM fact_performance_reviews f
      WHERE NOT EXISTS (SELECT 1 FROM performance_reviews r
                         WHERE r.ReviewID = f.ReviewID))                    AS facts_without_review,
    (SELECT COUNT(*) FROM performance_reviews r
      WHERE NOT EXISTS (SELECT 1 FROM dim_date d
                         WHERE d.FullDate = r.ReviewDate))                 AS reviews_outside_calendar,
    -- The invariant sp_repair_dim_employee_versions() maintains: a fact
    -- row resolves EmployeeSK through a date range, so an employee with
    -- two open versions would make that join ambiguous.
    (SELECT COUNT(*) FROM (
        SELECT EmployeeID
        FROM dim_employee
        WHERE IsCurrent = 1
        GROUP BY EmployeeID
        HAVING COUNT(*) > 1) x)                                            AS employees_with_two_open_versions,
    -- ...and an open version that is not the newest one is just as wrong.
    (SELECT COUNT(*) FROM dim_employee d
      WHERE d.IsCurrent = 1
        AND d.EffectiveStartDate < (SELECT MAX(x.EffectiveStartDate)
                                     FROM dim_employee x
                                     WHERE x.EmployeeID = d.EmployeeID))    AS stale_open_versions,
    -- Informational, not a defect. See the header comment.
    (SELECT COUNT(*)
       FROM employees e
       JOIN (SELECT EmployeeID, MAX(EffectiveStartDate) AS newest
               FROM stg_employee_history
               GROUP BY EmployeeID) h ON h.EmployeeID = e.EmployeeID
       JOIN stg_employee_history s
         ON s.EmployeeID = h.EmployeeID AND s.EffectiveStartDate = h.newest
      WHERE NOT (s.DepartmentID      <=> e.DepartmentID
             AND s.JobRole           <=> e.JobRole
             AND s.JobLevel          <=> e.JobLevel
             AND s.MonthlyIncome     <=> e.MonthlyIncome
             AND s.ManagerEmployeeID <=> e.ManagerEmployeeID)
    )                                                                       AS employees_history_newer_than_oltp;

-- ============================================================
-- SP_RECONCILE_DW(p_LookbackDays)
--
-- One button in the UI: bring the warehouse back in step with the OLTP
-- and report what moved. Cheap enough to run after every write, which
-- is the point - the app no longer needs a full rebuild to publish a
-- review or a transfer.
--
-- Order matters: the dimensions are refreshed before the fact table,
-- because the fact rows point at the dimension surrogate keys.
-- ============================================================

DELIMITER $$
CREATE PROCEDURE sp_reconcile_dw(
    IN p_LookbackDays INT
)
BEGIN
    DECLARE v_started DATETIME DEFAULT CURRENT_TIMESTAMP;

    CALL sp_load_dim_department();
    CALL sp_load_dim_project();
    CALL sp_load_dim_employee_delta();
    CALL sp_repair_dim_employee_versions();
    CALL sp_load_fact_delta(p_LookbackDays);

    INSERT INTO dw_load_log
        (ProcedureName, RunEndTime, RowsAffected, RunStatus)
    VALUES
        ('sp_reconcile_dw', CURRENT_TIMESTAMP, NULL, 'SUCCESS');

    -- The delta procedures report through the log; the caller gets the
    -- reconciliation snapshot, which is the interesting part.
    SELECT * FROM vw_oltp_dw_reconciliation;
END $$
DELIMITER ;