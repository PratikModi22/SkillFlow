-- ============================================================
-- 06_etl_oltp_to_dw.sql
-- Populates the star schema from the OLTP tables.
--
-- Two complementary SCD Type 2 paths:
--
--   sp_load_dim_employee_full   FULL/HISTORICAL load. Replays every
--       version produced by the synthesizer (stg_employee_history), so
--       the warehouse ships with ~5,000 closed historical versions.
--       UPSERTS, it does not truncate: versions created through the
--       Streamlit UI survive a rebuild instead of being wiped.
--
--   sp_apply_employee_change    INCREMENTAL load, one employee at a
--       time. Called by the Streamlit app when a user edits a
--       department / role / salary: it closes the open version
--       (EffectiveEndDate, IsCurrent=0) and inserts a new version.
--
--   sp_load_fact_performance_reviews  point-in-time join. Each review
--       is matched to the Dim_Employee version that was valid on the
--       review date, so historical facts stay historically accurate.
--
-- The incremental/repair counterparts (sp_load_dim_employee_delta,
-- sp_load_fact_delta, sp_reconcile_dw, vw_dim_employee_drift,
-- vw_oltp_dw_reconciliation) live in 10_incremental_loads.sql.
-- ============================================================

USE employee_analytics_dw;

DROP PROCEDURE IF EXISTS sp_load_dim_department;
DROP PROCEDURE IF EXISTS sp_load_dim_project;
DROP PROCEDURE IF EXISTS sp_load_dim_employee_full;
DROP PROCEDURE IF EXISTS sp_repair_dim_employee_versions;
DROP PROCEDURE IF EXISTS sp_apply_employee_change;
DROP PROCEDURE IF EXISTS sp_load_fact_performance_reviews;
DROP PROCEDURE IF EXISTS sp_load_dw_master;

-- ============================================================
-- DIM_DEPARTMENT  (SCD Type 1)
-- ============================================================

DELIMITER $$
CREATE PROCEDURE sp_load_dim_department()
BEGIN
    INSERT INTO dim_department (DepartmentID, DepartmentName, DepartmentStatus)
    SELECT d.DepartmentID, d.DepartmentName, d.DepartmentStatus
    FROM departments d
    WHERE d.DepartmentID IS NOT NULL
    ON DUPLICATE KEY UPDATE
        DepartmentName   = VALUES(DepartmentName),
        DepartmentStatus = VALUES(DepartmentStatus);
END $$
DELIMITER ;

-- ============================================================
-- DIM_PROJECT  (SCD Type 1)
-- ============================================================

DELIMITER $$
CREATE PROCEDURE sp_load_dim_project()
BEGIN
    INSERT INTO dim_project
        (ProjectID, ProjectName, ProjectType, ProjectStatus,
         ProjectStartDate, ProjectEndDate, ProjectManagerEmployeeID,
         ProjectManagerName, ProjectDurationDays)
    SELECT
        p.ProjectID,
        p.ProjectName,
        p.ProjectType,
        p.ProjectStatus,
        p.ProjectStartDate,
        p.ProjectEndDate,
        p.ProjectManagerEmployeeID,
        CONCAT(m.FirstName, ' ', m.LastName),
        CASE
            WHEN p.ProjectEndDate IS NOT NULL
            THEN DATEDIFF(p.ProjectEndDate, p.ProjectStartDate)
            ELSE DATEDIFF(CURDATE(), p.ProjectStartDate)
        END
    FROM projects p
    LEFT JOIN employees m ON m.EmployeeID = p.ProjectManagerEmployeeID
    WHERE p.ProjectID IS NOT NULL
    ON DUPLICATE KEY UPDATE
        ProjectName               = VALUES(ProjectName),
        ProjectType               = VALUES(ProjectType),
        ProjectStatus             = VALUES(ProjectStatus),
        ProjectStartDate          = VALUES(ProjectStartDate),
        ProjectEndDate            = VALUES(ProjectEndDate),
        ProjectManagerEmployeeID  = VALUES(ProjectManagerEmployeeID),
        ProjectManagerName        = VALUES(ProjectManagerName),
        ProjectDurationDays       = VALUES(ProjectDurationDays);
END $$
DELIMITER ;

-- ============================================================
-- DIM_EMPLOYEE - FULL HISTORICAL LOAD (SCD Type 2)
--
-- Every row of stg_employee_history becomes one Dim_Employee version,
-- plus one version per employee that only exists in the OLTP (added
-- through the Streamlit UI, so it has no CSV history).
--
-- ROW_NUMBER() guards against a duplicated (EmployeeID, start date)
-- pair.
--
-- WHY THIS IS AN UPSERT AND NOT A TRUNCATE + INSERT
-- ----------------------------------------------
-- Dim_Employee carries a surrogate key that fact_performance_reviews
-- points at, and it is also where SCD2 versions created by the running
-- application live. Truncating it would
--   (a) renumber every EmployeeSK, and
--   (b) delete every transfer the user made through the UI.
-- The natural key uq_dim_employee_version (EmployeeID,
-- EffectiveStartDate) is therefore used as the merge key: a version
-- that the source still produces is refreshed in place, a version the
-- source no longer produces is left alone.
--
-- The one invariant an upsert cannot guarantee on its own is "exactly
-- one open version per employee", because the UI may have appended a
-- version that starts after the newest one the source knows about.
-- sp_repair_dim_employee_versions() below restores that invariant.
-- ============================================================

DELIMITER $$
CREATE PROCEDURE sp_load_dim_employee_full()
BEGIN
    INSERT INTO dim_employee
        (EmployeeID, FirstName, LastName, Email, Gender,
         JobRole, JobLevel, MonthlyIncome, EmploymentStatus,
         HireDate, TerminationDate, DepartmentID, DepartmentName,
         ManagerEmployeeID, YearsOfService,
         EffectiveStartDate, EffectiveEndDate, IsCurrent)

    WITH csv_versions AS (
        SELECT
            h.EmployeeID,
            e.FirstName,
            e.LastName,
            e.Email,
            e.Gender,
            h.JobRole,
            h.JobLevel,
            h.MonthlyIncome,
            e.EmploymentStatus,
            e.HireDate,
            e.TerminationDate,
            h.DepartmentID,
            d.DepartmentName,
            h.ManagerEmployeeID,
            h.EffectiveStartDate,
            h.EffectiveEndDate,
            -- dedupe guard: keep one row per employee + start date
            ROW_NUMBER() OVER (
                PARTITION BY h.EmployeeID, h.EffectiveStartDate
                ORDER BY h.EffectiveEndDate DESC
            ) AS rn
        FROM stg_employee_history h
        JOIN employees e  ON e.EmployeeID  = h.EmployeeID
        JOIN departments d ON d.DepartmentID = h.DepartmentID
        WHERE h.EffectiveStartDate IS NOT NULL
    ),
    oltp_only_versions AS (
        SELECT
            e.EmployeeID,
            e.FirstName,
            e.LastName,
            e.Email,
            e.Gender,
            e.JobRole,
            e.JobLevel,
            e.MonthlyIncome,
            e.EmploymentStatus,
            e.HireDate,
            e.TerminationDate,
            e.DepartmentID,
            d.DepartmentName,
            e.ManagerEmployeeID,
            -- history starts when they were hired
            e.HireDate                             AS EffectiveStartDate,
            CASE
                WHEN e.EmploymentStatus = 'Terminated'
                     AND e.TerminationDate IS NOT NULL
                     AND e.TerminationDate >= e.HireDate
                THEN e.TerminationDate
                ELSE NULL
            END                                    AS EffectiveEndDate,
            1                                      AS rn
        FROM employees e
        JOIN departments d ON d.DepartmentID = e.DepartmentID
        WHERE NOT EXISTS (
                SELECT 1
                FROM stg_employee_history h
                WHERE h.EmployeeID = e.EmployeeID
            )
          AND e.HireDate IS NOT NULL
    ),
    versions AS (
        SELECT * FROM csv_versions
        UNION ALL
        SELECT * FROM oltp_only_versions
    ),
    resolved AS (
        SELECT
            v.EmployeeID,
            v.FirstName,
            v.LastName,
            v.Email,
            v.Gender,
            v.JobRole,
            -- Every expression below needs an explicit alias: an
            -- unaliased COALESCE() is exposed by MySQL under the
            -- literal expression text as its column name, and the outer
            -- SELECT cannot then say "r.JobLevel".
            COALESCE(v.JobLevel, 1)                                       AS JobLevel,
            COALESCE(v.MonthlyIncome, 0)                                  AS MonthlyIncome,
            v.EmploymentStatus,
            v.HireDate,
            v.TerminationDate,
            v.DepartmentID,
            v.DepartmentName,
            v.ManagerEmployeeID,
            ROUND(TIMESTAMPDIFF(MONTH, v.HireDate,
                                COALESCE(v.EffectiveEndDate, CURDATE())) / 12, 2)
                                                                     AS YearsOfService,
            v.EffectiveStartDate,
            COALESCE(v.EffectiveEndDate, '9999-12-31')                   AS EffectiveEndDate,
            CASE WHEN v.EffectiveEndDate IS NULL THEN 1 ELSE 0 END       AS IsCurrent
        FROM versions v
        WHERE v.rn = 1
    )
    SELECT
        r.EmployeeID, r.FirstName, r.LastName, r.Email, r.Gender,
        r.JobRole, r.JobLevel, r.MonthlyIncome, r.EmploymentStatus,
        r.HireDate, r.TerminationDate, r.DepartmentID, r.DepartmentName,
        r.ManagerEmployeeID, r.YearsOfService,
        r.EffectiveStartDate, r.EffectiveEndDate, r.IsCurrent
    FROM resolved r
    ON DUPLICATE KEY UPDATE
        FirstName         = VALUES(FirstName),
        LastName          = VALUES(LastName),
        Email             = VALUES(Email),
        Gender            = VALUES(Gender),
        JobRole           = VALUES(JobRole),
        JobLevel          = VALUES(JobLevel),
        MonthlyIncome     = VALUES(MonthlyIncome),
        EmploymentStatus  = VALUES(EmploymentStatus),
        HireDate          = VALUES(HireDate),
        TerminationDate   = VALUES(TerminationDate),
        DepartmentID      = VALUES(DepartmentID),
        DepartmentName    = VALUES(DepartmentName),
        ManagerEmployeeID = VALUES(ManagerEmployeeID),
        YearsOfService    = VALUES(YearsOfService),
        EffectiveEndDate  = VALUES(EffectiveEndDate),
        IsCurrent         = VALUES(IsCurrent);
END $$
DELIMITER ;

-- ============================================================
-- DIM_EMPLOYEE - VERSION INVARIANT REPAIR
--
-- Guarantees the invariant the star schema depends on: of all the
-- versions of one employee, exactly ONE is open (IsCurrent = 1) and it
-- is the newest one.
--
-- Needed because the incremental path can leave a newer version than
-- the source knows about - e.g. a transfer applied through the UI on
-- top of the version stg_employee_history last produced. The full load
-- then upserts the older version as open, and two open rows would make
-- the point-in-time fact join ambiguous.
--
-- Cost: one grouped pass over ~20k rows, index-only. Historical end
-- dates are never touched; only a version that is wrongly flagged open
-- is closed on the day before the newest version starts.
-- ============================================================

DELIMITER $$
CREATE PROCEDURE sp_repair_dim_employee_versions()
BEGIN
    DECLARE v_closed INT DEFAULT 0;

    UPDATE dim_employee d
    JOIN (
        SELECT EmployeeID, MAX(EffectiveStartDate) AS newest_start
        FROM dim_employee
        GROUP BY EmployeeID
    ) m ON m.EmployeeID = d.EmployeeID
    SET d.IsCurrent        = 0,
        d.EffectiveEndDate = DATE_SUB(m.newest_start, INTERVAL 1 DAY)
    WHERE d.IsCurrent = 1
      AND d.EffectiveStartDate < m.newest_start;

    SET v_closed = ROW_COUNT();

    -- Defensive: a row that is not the newest one can never be open,
    -- whatever the flag says.
    UPDATE dim_employee d
    JOIN (
        SELECT EmployeeID, MAX(EffectiveStartDate) AS newest_start
        FROM dim_employee
        GROUP BY EmployeeID
    ) m ON m.EmployeeID = d.EmployeeID
    SET d.IsCurrent = 0
    WHERE d.IsCurrent = 1
      AND d.EffectiveStartDate <> m.newest_start;

    SELECT v_closed AS versions_closed;
END $$
DELIMITER ;

-- ============================================================
-- DIM_EMPLOYEE - INCREMENTAL SCD2 (used by the Streamlit app)
--
--   sp_apply_employee_change(p_EmployeeID, p_ChangeDate, p_Force)
--
-- 1. refuses an unknown employee instead of silently doing nothing,
-- 2. compares the tracked attributes against the newest version the
--    warehouse holds and returns early when nothing actually moved,
-- 3. closes the open version on the day before p_ChangeDate and
--    inserts the new version,
-- 4. SAME-DAY CORRECTION: when p_ChangeDate equals the start date of
--    the newest version there is no room for a second row (the natural
--    key is EmployeeID + EffectiveStartDate and no zero-length version
--    window is representable), so that version is rewritten in place.
--    Without this branch a second change on the same day was accepted
--    by the OLTP and then dropped by the warehouse.
-- 5. refuses a change dated before the newest version already starts,
--    which would otherwise create an overlapping window.
--
-- p_Force = 1 skips the change detection and versions unconditionally
-- (used by the repair/reconciliation path).
--
-- Callers must pass p_EmployeeID as a literal or parameter; the
-- procedure resolves the department itself, so a missing department
-- row is reported as an error instead of inserting nothing.
-- ============================================================

DELIMITER $$
CREATE PROCEDURE sp_apply_employee_change(
    IN p_EmployeeID VARCHAR(50),
    IN p_ChangeDate DATE,
    IN p_Force       TINYINT
)
BEGIN
    DECLARE v_versions   INT  DEFAULT 0;
    DECLARE v_last_start DATE DEFAULT NULL;
    DECLARE v_changed    INT  DEFAULT 0;
    DECLARE v_new_start  DATE;
    DECLARE v_new_end    DATE;
DECLARE v_is_current TINYINT DEFAULT 1;
    DECLARE v_real_end   DATE DEFAULT NULL;
    DECLARE v_years      DECIMAL(6,2) DEFAULT 0;
    DECLARE v_rows       INT DEFAULT 0;
    DECLARE v_message    VARCHAR(255);

    SET p_ChangeDate = COALESCE(p_ChangeDate, CURDATE());
    SET p_Force      = COALESCE(p_Force, 0);

    IF NOT EXISTS (SELECT 1 FROM employees e WHERE e.EmployeeID = p_EmployeeID) THEN
        SET v_message = CONCAT(
            'sp_apply_employee_change: employee ''', p_EmployeeID,
            ''' does not exist in the OLTP employees table.');
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = v_message;
    END IF;

    -- Checked up front, not after the open version has been closed: the
    -- version INSERT joins departments, so an unknown DepartmentID would
    -- silently write nothing and leave the employee with no open version.
    IF NOT EXISTS (
        SELECT 1
        FROM employees e
        JOIN departments d ON d.DepartmentID = e.DepartmentID
        WHERE e.EmployeeID = p_EmployeeID
    ) THEN
        SET v_message = CONCAT(
            'sp_apply_employee_change: employee ''', p_EmployeeID,
            ''' is not assigned to an existing department.');
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = v_message;
    END IF;

    SET v_versions   = (SELECT COUNT(*)
                        FROM dim_employee d
                        WHERE d.EmployeeID = p_EmployeeID);
    SET v_last_start = (SELECT MAX(d.EffectiveStartDate)
                        FROM dim_employee d
                        WHERE d.EmployeeID = p_EmployeeID);
    SET v_new_start  = p_ChangeDate;

    -- 5. no version may start before the newest one already stored
    IF v_versions > 0 AND v_new_start < v_last_start THEN
        SET v_message = CONCAT(
            'Change date ', v_new_start, ' precedes the current version of ',
            p_EmployeeID, ' which starts ', v_last_start, '.');
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = v_message;
    END IF;

    -- 2. nothing tracked changed -> no new version
    change_guard: BEGIN
        IF v_versions > 0 AND p_Force = 0 THEN
            SET v_changed = (
                SELECT COUNT(*)
                FROM employees e
                JOIN dim_employee d      ON d.EmployeeID  = e.EmployeeID
                LEFT JOIN departments dep ON dep.DepartmentID = e.DepartmentID
                WHERE e.EmployeeID = p_EmployeeID
                  AND d.EffectiveStartDate = v_last_start
                  AND NOT (
                           d.FirstName         <=> e.FirstName
                       AND d.LastName          <=> e.LastName
                       AND d.Email             <=> e.Email
                       AND d.Gender            <=> e.Gender
                       AND d.JobRole           <=> e.JobRole
                       AND d.JobLevel          <=> e.JobLevel
                       AND d.MonthlyIncome     <=> e.MonthlyIncome
                       AND d.EmploymentStatus  <=> e.EmploymentStatus
                       AND d.DepartmentID      <=> e.DepartmentID
                       AND d.DepartmentName    <=> dep.DepartmentName
                       AND d.ManagerEmployeeID <=> e.ManagerEmployeeID
                       AND d.HireDate          <=> e.HireDate
                       AND d.TerminationDate   <=> e.TerminationDate
                  )
            );
            IF v_changed = 0 THEN
                LEAVE change_guard;
            END IF;
        END IF;
    END change_guard;

    -- Validity window of the new version. A terminated employee closes
    -- their last version on the termination date, which is exactly what
    -- oltp_only_versions in the full load produces, so an incremental
    -- load and a full rebuild agree on the flags. The guard keeps the
    -- CHECK (EffectiveEndDate >= EffectiveStartDate) satisfied when a
    -- termination predates the change being applied.
    SELECT CASE
               WHEN e.EmploymentStatus = 'Terminated'
                    AND e.TerminationDate IS NOT NULL
                    AND e.TerminationDate >= v_new_start
               THEN e.TerminationDate
               ELSE CAST('9999-12-31' AS DATE)
           END
      INTO v_new_end
      FROM employees e
     WHERE e.EmployeeID = p_EmployeeID;

    SET v_is_current = CASE WHEN v_new_end = CAST('9999-12-31' AS DATE)
                            THEN 1 ELSE 0 END;

    -- YearsOfService is measured to the end of the validity window, the
    -- same expression sp_load_dim_employee_full() uses. If the two paths
    -- disagreed, a rebuild would overwrite this version with a different
    -- value and a version would change without anyone editing anything.
    SET v_real_end = CASE WHEN v_is_current = 1 THEN CURDATE() ELSE v_new_end END;
    SELECT ROUND(TIMESTAMPDIFF(MONTH, e.HireDate, v_real_end) / 12, 2)
      INTO v_years
      FROM employees e
     WHERE e.EmployeeID = p_EmployeeID;

    IF v_versions > 0 AND v_new_start = v_last_start THEN
        -- 4. same-day correction: rewrite the version in place
        UPDATE dim_employee d
          JOIN employees e       ON e.EmployeeID  = d.EmployeeID
          LEFT JOIN departments dep ON dep.DepartmentID = e.DepartmentID
           SET d.FirstName         = e.FirstName,
               d.LastName          = e.LastName,
               d.Email             = e.Email,
               d.Gender            = e.Gender,
               d.JobRole           = e.JobRole,
               d.JobLevel          = e.JobLevel,
               d.MonthlyIncome     = e.MonthlyIncome,
               d.EmploymentStatus  = e.EmploymentStatus,
               d.HireDate          = e.HireDate,
               d.TerminationDate   = e.TerminationDate,
               d.DepartmentID      = e.DepartmentID,
               d.DepartmentName    = dep.DepartmentName,
               d.ManagerEmployeeID = e.ManagerEmployeeID,
               d.YearsOfService    = v_years,
               d.EffectiveEndDate  = v_new_end,
               d.IsCurrent         = v_is_current
         WHERE d.EmployeeID        = p_EmployeeID
           AND d.EffectiveStartDate = v_new_start;
    ELSE
        -- 3. close every open version, then append the new one
        UPDATE dim_employee
           SET EffectiveEndDate = DATE_SUB(v_new_start, INTERVAL 1 DAY),
               IsCurrent        = 0
         WHERE EmployeeID = p_EmployeeID
           AND IsCurrent  = 1;

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
            e.JobLevel,
            e.MonthlyIncome,
            e.EmploymentStatus,
            e.HireDate,
            e.TerminationDate,
            e.DepartmentID,
            d.DepartmentName,
            e.ManagerEmployeeID,
            v_years,
            v_new_start,
            v_new_end,
            v_is_current
        FROM employees e
        JOIN departments d ON d.DepartmentID = e.DepartmentID
        WHERE e.EmployeeID = p_EmployeeID;
    END IF;

    SET v_rows = (SELECT COUNT(*)
                  FROM dim_employee d
                  WHERE d.EmployeeID = p_EmployeeID);

    IF v_rows = 0 THEN
        SET v_message = CONCAT(
            'sp_apply_employee_change: no Dim_Employee version could be '
            'written for ', p_EmployeeID, '.');
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = v_message;
    END IF;
END $$
DELIMITER ;

-- ============================================================
-- FACT_PERFORMANCE_REVIEWS
--
-- Point-in-time historisation:
--   EmployeeSK   = the Dim_Employee version valid on ReviewDate
--   DepartmentSK = that version's department
--   DateSK       = ReviewDate
--   ProjectSK    = the assignment running on ReviewDate, falling back
--                  to the most recent one that had started by then
--                  (ROW_NUMBER guarantees one row per review)
--
-- The version is picked with ORDER BY EffectiveStartDate DESC LIMIT 1
-- rather than a plain join, so that a day with two overlapping versions
-- (only possible if the data was edited by hand) still produces exactly
-- one fact row instead of failing on the unique key.
-- ============================================================

DELIMITER $$
CREATE PROCEDURE sp_load_fact_performance_reviews()
BEGIN
    TRUNCATE TABLE fact_performance_reviews;

    INSERT INTO fact_performance_reviews
        (ReviewID, EmployeeSK, DateSK, DepartmentSK, ProjectSK,
         Rating, PerformanceScore, PromotionRecommended, ReviewCount,
         AttritionFlag, TenureYears, AllocationPercentage)

    WITH project_pick AS (
        SELECT
            r.ReviewID,
            a.ProjectID,
            a.AllocationPercentage,
            ROW_NUMBER() OVER (
                PARTITION BY r.ReviewID
                ORDER BY
                    -- 1st: an assignment that was still running on the
                    --      review date, otherwise
                    -- 2nd: the most recent assignment that had already
                    --      started by then
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
        pp.AllocationPercentage
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
    JOIN dim_date dd        ON dd.FullDate = r.ReviewDate
    JOIN dim_department dp  ON dp.DepartmentID = de.DepartmentID
    LEFT JOIN project_pick pp ON pp.ReviewID = r.ReviewID AND pp.rn = 1
    LEFT JOIN dim_project dpr ON dpr.ProjectID = pp.ProjectID;
END $$
DELIMITER ;

-- ============================================================
-- MASTER PROCEDURE
--
-- Full rebuild, still the safe way to recover from anything:
--   dim_department / dim_project  upsert (SCD Type 1)
--   dim_employee                  upsert of every source version
--   version invariant             repaired
--   fact                          truncate + point-in-time rebuild
--
-- The "employees that exist in OLTP but not in the warehouse" catch-up
-- that used to live here (a WHILE loop with LIMIT/OFFSET, which was
-- both O(n^2) and order-dependent) is now sp_load_dim_employee_delta()
-- in 10_incremental_loads.sql: set-based for brand new employees and a
-- cursor only for the handful that actually drifted.
-- ============================================================

DELIMITER $$
CREATE PROCEDURE sp_load_dw_master()
BEGIN
    DECLARE v_rows BIGINT DEFAULT 0;
    DECLARE v_start DATETIME DEFAULT CURRENT_TIMESTAMP;

    -- The fact table is derived data, so it is rebuilt from scratch.
    TRUNCATE TABLE fact_performance_reviews;

    CALL sp_load_dim_department();
    CALL sp_load_dim_project();
    CALL sp_load_dim_employee_full();
    CALL sp_load_fact_performance_reviews();

    -- Catch up anything the source-driven load cannot see (employees
    -- added through the app) and repair the open-version invariant.
    CALL sp_load_dim_employee_delta();
    CALL sp_repair_dim_employee_versions();

    SELECT
        (SELECT COUNT(*) FROM dim_department)             AS dim_department,
        (SELECT COUNT(*) FROM dim_project)                AS dim_project,
        (SELECT COUNT(*) FROM dim_employee)               AS dim_employee,
        (SELECT COUNT(*) FROM dim_employee WHERE IsCurrent=1) AS emp_current,
        (SELECT COUNT(*) FROM dim_employee WHERE IsCurrent=0) AS emp_historical,
        (SELECT COUNT(*) FROM fact_performance_reviews)   AS fact_reviews;

    SELECT
        (SELECT COUNT(*) FROM dim_department)             +
        (SELECT COUNT(*) FROM dim_project)                +
        (SELECT COUNT(*) FROM dim_employee)               +
        (SELECT COUNT(*) FROM fact_performance_reviews)   INTO v_rows;

    INSERT INTO dw_load_log
        (ProcedureName, RunEndTime, RowsAffected, RunStatus)
    VALUES
        ('sp_load_dw_master', CURRENT_TIMESTAMP, v_rows, 'SUCCESS');
END $$
DELIMITER ;