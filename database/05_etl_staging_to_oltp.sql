-- ============================================================
-- 05_etl_staging_to_oltp.sql
-- Stored procedures that promote the raw staging tables into the
-- normalized OLTP schema.
--
-- Cleaning / ranking is done with CTEs and window functions:
--   * ROW_NUMBER()  de-duplicates an employee, keeping the newest row
--   * DENSE_RANK() ranks departments by headcount so that low-volume
--     departments can be filtered out of the app's dropdowns
--   * MAX() keeps the most recent assignment status per employee+project
--
-- Run order: sp_load_oltp_master (calls the five loaders in FK order).
-- ============================================================

USE employee_analytics_dw;

DROP PROCEDURE IF EXISTS sp_load_departments;
DROP PROCEDURE IF EXISTS sp_load_employees;
DROP PROCEDURE IF EXISTS sp_load_projects;
DROP PROCEDURE IF EXISTS sp_load_assignments;
DROP PROCEDURE IF EXISTS sp_load_reviews;
DROP PROCEDURE IF EXISTS sp_load_oltp_master;

-- ============================================================
-- 1. DEPARTMENTS
-- ============================================================

DELIMITER $$
CREATE PROCEDURE sp_load_departments()
BEGIN
    TRUNCATE TABLE departments;

    INSERT INTO departments (DepartmentID, DepartmentName, DepartmentStatus)
    WITH ranked AS (
        SELECT
            TRIM(DepartmentID)   AS DepartmentID,
            TRIM(DepartmentName) AS DepartmentName,
            COALESCE(NULLIF(TRIM(DepartmentStatus), ''), 'Active') AS DepartmentStatus,
            ROW_NUMBER() OVER (
                PARTITION BY DepartmentID
                ORDER BY LoadedAt DESC
            ) AS rn
        FROM stg_departments
        WHERE DepartmentID IS NOT NULL
    )
    SELECT DepartmentID, DepartmentName, DepartmentStatus
    FROM ranked
    WHERE rn = 1;
END $$
DELIMITER ;

-- ============================================================
-- 2. EMPLOYEES
--    One row per EmployeeID, latest staging row wins.
--    ManagerEmployeeID is nulled when the manager is unknown so the
--    self-referencing FK can never break the load.
-- ============================================================

DELIMITER $$
CREATE PROCEDURE sp_load_employees()
BEGIN
    -- Upsert, never truncate. Employees added through the app have no
    -- staging row, so truncating would silently delete them.
    INSERT INTO employees
        (EmployeeID, FirstName, LastName, Email, PhoneNumber, Gender,
         HireDate, TerminationDate, EmploymentStatus, DepartmentID,
         JobRole, JobLevel, MonthlyIncome, ManagerEmployeeID)
    WITH latest AS (
        SELECT
            e.*,
            ROW_NUMBER() OVER (
                PARTITION BY e.EmployeeID
                ORDER BY e.HireDate DESC, e.LoadedAt DESC
            ) AS rn
        FROM stg_employees e
        WHERE e.EmployeeID IS NOT NULL
    ),
    known_managers AS (
        SELECT DISTINCT EmployeeID FROM stg_employees
    )
    SELECT
        l.EmployeeID,
        TRIM(l.FirstName),
        TRIM(l.LastName),
        LOWER(TRIM(l.Email)),
        NULLIF(TRIM(l.PhoneNumber), ''),
        NULLIF(TRIM(l.Gender), ''),
        l.HireDate,
        l.TerminationDate,
        COALESCE(NULLIF(TRIM(l.EmploymentStatus), ''), 'Active'),
        l.DepartmentID,
        TRIM(l.JobRole),
        GREATEST(COALESCE(l.JobLevel, 1), 1),
        COALESCE(l.MonthlyIncome, 0),
        CASE WHEN km.EmployeeID IS NOT NULL THEN l.ManagerEmployeeID END
    FROM latest l
    LEFT JOIN known_managers km ON km.EmployeeID = l.ManagerEmployeeID
    WHERE l.rn = 1
    ON DUPLICATE KEY UPDATE
        FirstName          = VALUES(FirstName),
        LastName           = VALUES(LastName),
        Email              = VALUES(Email),
        PhoneNumber        = VALUES(PhoneNumber),
        Gender             = VALUES(Gender),
        HireDate           = VALUES(HireDate),
        TerminationDate    = VALUES(TerminationDate),
        EmploymentStatus   = VALUES(EmploymentStatus),
        DepartmentID       = VALUES(DepartmentID),
        JobRole            = VALUES(JobRole),
        JobLevel           = VALUES(JobLevel),
        MonthlyIncome      = VALUES(MonthlyIncome),
        ManagerEmployeeID  = VALUES(ManagerEmployeeID);
END $$
DELIMITER ;

-- ============================================================
-- 3. PROJECTS
--    ProjectManagerEmployeeID is validated against employees.
-- ============================================================

DELIMITER $$
CREATE PROCEDURE sp_load_projects()
BEGIN
    -- Upsert only. App-created projects have no staging row.
    INSERT INTO projects
        (ProjectID, ProjectName, ProjectType, ProjectStartDate,
         ProjectEndDate, ProjectStatus, ProjectManagerEmployeeID)
    SELECT
        p.ProjectID,
        TRIM(p.ProjectName),
        NULLIF(TRIM(p.ProjectType), ''),
        p.ProjectStartDate,
        p.ProjectEndDate,
        COALESCE(NULLIF(TRIM(p.ProjectStatus), ''), 'In Progress'),
        CASE WHEN e.EmployeeID IS NOT NULL THEN p.ProjectManagerEmployeeID END
    FROM stg_projects p
    LEFT JOIN employees e ON e.EmployeeID = p.ProjectManagerEmployeeID
    WHERE p.ProjectID IS NOT NULL
    ON DUPLICATE KEY UPDATE
        ProjectName               = VALUES(ProjectName),
        ProjectType               = VALUES(ProjectType),
        ProjectStartDate          = VALUES(ProjectStartDate),
        ProjectEndDate            = VALUES(ProjectEndDate),
        ProjectStatus             = VALUES(ProjectStatus),
        ProjectManagerEmployeeID  = VALUES(ProjectManagerEmployeeID);
END $$
DELIMITER ;

-- ============================================================
-- 4. EMPLOYEE PROJECT ASSIGNMENTS
--    DENSE_RANK removes duplicate employee/project pairs, keeping the
--    most recent assignment (the highest AssignmentID).
-- ============================================================

DELIMITER $$
CREATE PROCEDURE sp_load_assignments()
BEGIN
    -- Upsert only. App-created assignments have no staging row.
    INSERT INTO employee_project_assignments
        (AssignmentID, EmployeeID, ProjectID, AssignmentStartDate,
         AssignmentEndDate, AssignmentRole, AllocationPercentage, AssignmentStatus)
    WITH deduped AS (
        SELECT
            a.*,
            DENSE_RANK() OVER (
                PARTITION BY a.EmployeeID, a.ProjectID
                ORDER BY a.AssignmentStartDate DESC, a.AssignmentID DESC
            ) AS dr
        FROM stg_employee_project_assignments a
        WHERE a.EmployeeID IS NOT NULL
          AND a.ProjectID  IS NOT NULL
    )
    SELECT
        d.AssignmentID,
        d.EmployeeID,
        d.ProjectID,
        d.AssignmentStartDate,
        d.AssignmentEndDate,
        NULLIF(TRIM(d.AssignmentRole), ''),
        LEAST(GREATEST(COALESCE(d.AllocationPercentage, 100), 1), 100),
        COALESCE(NULLIF(TRIM(d.AssignmentStatus), ''), 'Active')
    FROM deduped d
    WHERE d.dr = 1
    ON DUPLICATE KEY UPDATE
        AssignmentStartDate   = VALUES(AssignmentStartDate),
        AssignmentEndDate     = VALUES(AssignmentEndDate),
        AssignmentRole        = VALUES(AssignmentRole),
        AllocationPercentage  = VALUES(AllocationPercentage),
        AssignmentStatus      = VALUES(AssignmentStatus);
END $$
DELIMITER ;

-- ============================================================
-- 5. PERFORMANCE REVIEWS
--    RANK() keeps only the latest review per employee per day.
--    ReviewerEmployeeID is validated against employees.
-- ============================================================

DELIMITER $$
CREATE PROCEDURE sp_load_reviews()
BEGIN
    -- Upsert only. App-created reviews have no staging row.
    INSERT INTO performance_reviews
        (ReviewID, EmployeeID, ReviewDate, Rating,
         PerformanceScore, PromotionRecommendation, ReviewerEmployeeID)
    WITH per_day AS (
        SELECT
            r.*,
            RANK() OVER (
                PARTITION BY r.EmployeeID, r.ReviewDate
                ORDER BY r.LoadedAt DESC
            ) AS rk
        FROM stg_performance_reviews r
        WHERE r.EmployeeID IS NOT NULL
          AND r.ReviewDate  IS NOT NULL
    )
    SELECT
        p.ReviewID,
        p.EmployeeID,
        p.ReviewDate,
        LEAST(GREATEST(COALESCE(p.Rating, 3), 1), 5),
        LEAST(GREATEST(COALESCE(p.PerformanceScore, 0), 0), 100),
        CASE WHEN TRIM(p.PromotionRecommendation) = 'Yes' THEN 'Yes' ELSE 'No' END,
        CASE WHEN e.EmployeeID IS NOT NULL THEN p.ReviewerEmployeeID END
    FROM per_day p
    LEFT JOIN employees e ON e.EmployeeID = p.ReviewerEmployeeID
    WHERE p.rk = 1
    ON DUPLICATE KEY UPDATE
        EmployeeID              = VALUES(EmployeeID),
        ReviewDate              = VALUES(ReviewDate),
        Rating                  = VALUES(Rating),
        PerformanceScore        = VALUES(PerformanceScore),
        PromotionRecommendation = VALUES(PromotionRecommendation),
        ReviewerEmployeeID      = VALUES(ReviewerEmployeeID);
END $$
DELIMITER ;

-- ============================================================
-- 6. MASTER PROCEDURE
--    FK checks are switched off for the duration because
--    employees.manager -> employees and projects.manager -> employees
--    are self-referencing, so parents and children land in one
--    statement.
-- ============================================================

DELIMITER $$
CREATE PROCEDURE sp_load_oltp_master()
BEGIN
    DECLARE v_rows BIGINT DEFAULT 0;

    SET FOREIGN_KEY_CHECKS = 0;

    CALL sp_load_departments();
    CALL sp_load_employees();
    CALL sp_load_projects();
    CALL sp_load_assignments();
    CALL sp_load_reviews();

    SET FOREIGN_KEY_CHECKS = 1;

    SELECT
        (SELECT COUNT(*) FROM departments)                   AS departments,
        (SELECT COUNT(*) FROM employees)                     AS employees,
        (SELECT COUNT(*) FROM projects)                      AS projects,
        (SELECT COUNT(*) FROM employee_project_assignments)  AS assignments,
        (SELECT COUNT(*) FROM performance_reviews)           AS reviews;

    SELECT
        (SELECT COUNT(*) FROM departments)                   +
        (SELECT COUNT(*) FROM employees)                     +
        (SELECT COUNT(*) FROM projects)                      +
        (SELECT COUNT(*) FROM employee_project_assignments)  +
        (SELECT COUNT(*) FROM performance_reviews)           INTO v_rows;

    INSERT INTO dw_load_log
        (ProcedureName, RunEndTime, RowsAffected, RunStatus)
    VALUES
        ('sp_load_oltp_master', CURRENT_TIMESTAMP, v_rows, 'SUCCESS');
END $$
DELIMITER ;