-- ============================================================
-- 04_create_dw.sql
-- Analytical (OLAP) star schema for employee performance analytics.
--
--   Fact_PerformanceReviews   grain = one performance review
--     |-- Dim_Employee    (SCD Type 2, surrogate key EmployeeSK)
--     |-- Dim_Date        (surrogate key DateSK = YYYYMMDD)
--     |-- Dim_Project     (surrogate key ProjectSK)
--     '-- Dim_Department  (surrogate key DepartmentSK)
--
-- Fact_PerformanceReviews.ProjectSK is NULL for reviews that are not
-- tied to a project, so the star has two optional conformed dimensions.
--
-- NOTE: no DROP here - staging + OLTP data must survive.
--
-- FIXED-WIDTH ATTRIBUTES
--   The natural keys carried into the dimensions, plus the calendar
--   quarter label, have a width the generator guarantees, so they are
--   CHAR rather than VARCHAR:
--
--       EmployeeID     CHAR(11)   "EMP-" + 7 digits
--       DepartmentID   CHAR(8)    "DEPT-" + 3 digits
--       ProjectID      CHAR(10)   "PROJ-" + 5 digits
--       ReviewID       CHAR(12)   "REV-" + 8 digits
--       QuarterLabel   CHAR(7)    "YYYY-Qn"
--
--   The widths match 02_create_staging.sql and 03_create_oltp.sql.
--   Every dimension joins to the fact table on an INT surrogate key,
--   so no string foreign key spans these columns; the agreement
--   instead keeps the 05/06 INSERT ... SELECT joins free of implicit
--   conversions, which would otherwise prevent index use.
--
--   DepartmentStatus stays VARCHAR: it is uniform in this dataset by
--   coincidence, not by contract.
-- ============================================================

USE employee_analytics_dw;

-- The calendar below spans ~3,287 days, above MySQL's default
-- cte_max_recursion_depth of 1000.
SET SESSION cte_max_recursion_depth = 100000;

-- ============================================================
-- DIM_DATE
-- Conformed calendar dimension. DateSK is an integer YYYYMMDD so the
-- key is human readable and sorts naturally.
-- ============================================================

CREATE TABLE IF NOT EXISTS dim_date (
    DateSK       INT          NOT NULL,
    FullDate     DATE         NOT NULL,
    Year         SMALLINT     NOT NULL,
    Quarter      TINYINT      NOT NULL,
    Month        TINYINT      NOT NULL,
    MonthName    VARCHAR(10)  NOT NULL,
    DayOfMonth   TINYINT      NOT NULL,
    WeekOfYear   TINYINT      NOT NULL,
    IsWeekend    TINYINT(1)   NOT NULL DEFAULT 0,
    QuarterLabel CHAR(7)   NOT NULL,
    PRIMARY KEY (DateSK),
    UNIQUE KEY uq_dim_date_fulldate (FullDate)
) ENGINE = InnoDB;

-- ============================================================
-- DIM_DEPARTMENT
-- SCD Type 1: departments are looked up by natural key and updated
-- in place if the name/status ever changes.
-- ============================================================

CREATE TABLE IF NOT EXISTS dim_department (
    DepartmentSK     BIGINT       NOT NULL AUTO_INCREMENT,
    DepartmentID     CHAR(8)  NOT NULL,
    DepartmentName   VARCHAR(100) NOT NULL,
    DepartmentStatus VARCHAR(30)  NOT NULL DEFAULT 'Active',
    PRIMARY KEY (DepartmentSK),
    UNIQUE KEY uq_dim_department_id (DepartmentID)
) ENGINE = InnoDB;

-- ============================================================
-- DIM_PROJECT
-- ============================================================

CREATE TABLE IF NOT EXISTS dim_project (
    ProjectSK                 BIGINT       NOT NULL AUTO_INCREMENT,
    ProjectID                 CHAR(10)  NOT NULL,
    ProjectName               VARCHAR(255) NOT NULL,
    ProjectType               VARCHAR(100) NULL,
    ProjectStatus             VARCHAR(30)  NOT NULL,
    ProjectStartDate          DATE         NOT NULL,
    ProjectEndDate            DATE         NULL,
    ProjectManagerEmployeeID  CHAR(11)  NULL,
    ProjectManagerName        VARCHAR(200) NULL,
    ProjectDurationDays       INT          NULL,
    PRIMARY KEY (ProjectSK),
    UNIQUE KEY uq_dim_project_id (ProjectID),
    KEY idx_dim_project_status (ProjectStatus)
) ENGINE = InnoDB;

-- ============================================================
-- DIM_EMPLOYEE   (SLOWLY CHANGING DIMENSION - TYPE 2)
--
-- One row per employee PER VERSION. EmployeeSK is the surrogate key
-- the fact table points at, so history is preserved.
--
--   EmployeeID       natural / business key (stable across versions)
--   EffectiveStartDate / EffectiveEndDate  version validity window
--   IsCurrent        1 for the open (latest) version, else 0
--
-- Tracked attributes: DepartmentID/Name, JobRole, JobLevel, MonthlyIncome.
-- ============================================================

CREATE TABLE IF NOT EXISTS dim_employee (
    EmployeeSK        BIGINT        NOT NULL AUTO_INCREMENT,
    EmployeeID        CHAR(11)   NOT NULL,
    FirstName         VARCHAR(100)  NOT NULL,
    LastName          VARCHAR(100)  NOT NULL,
    Email             VARCHAR(255)  NOT NULL,
    Gender            VARCHAR(30)   NULL,
    JobRole           VARCHAR(100)  NOT NULL,
    JobLevel          INT           NOT NULL DEFAULT 1,
    MonthlyIncome     DECIMAL(12, 2) NOT NULL,
    EmploymentStatus  VARCHAR(30)   NOT NULL DEFAULT 'Active',
    HireDate          DATE          NOT NULL,
    TerminationDate   DATE          NULL,
    DepartmentID      CHAR(8)   NOT NULL,
    DepartmentName    VARCHAR(100)  NOT NULL,
    ManagerEmployeeID CHAR(11)   NULL,
    YearsOfService    DECIMAL(6, 2) NULL,

    EffectiveStartDate DATE NOT NULL,
    EffectiveEndDate   DATE NULL DEFAULT '9999-12-31',
    IsCurrent          TINYINT(1)  NOT NULL DEFAULT 1,

    PRIMARY KEY (EmployeeSK),
    UNIQUE KEY uq_dim_employee_version (EmployeeID, EffectiveStartDate),
    KEY idx_dim_employee_id (EmployeeID),
    KEY idx_dim_employee_current (EmployeeID, IsCurrent),
    KEY idx_dim_employee_dept (DepartmentID),
    CONSTRAINT chk_dim_employee_version
        CHECK (EffectiveEndDate >= EffectiveStartDate)
) ENGINE = InnoDB;

-- ============================================================
-- FACT_PERFORMANCE_REVIEWS
-- Grain: exactly one performance review.
-- Degenerate dimension: ReviewID stays on the fact table.
-- Additive measures: PerformanceScore, Rating, ReviewCount(=1).
-- ============================================================

CREATE TABLE IF NOT EXISTS fact_performance_reviews (
    ReviewSK               BIGINT        NOT NULL AUTO_INCREMENT,
    ReviewID               CHAR(12)   NOT NULL,

    -- foreign keys to the four conformed dimensions
    EmployeeSK             BIGINT        NOT NULL,
    DateSK                 INT           NOT NULL,
    DepartmentSK           BIGINT        NOT NULL,
    ProjectSK              BIGINT        NULL,

    -- measures
    Rating                 DECIMAL(5, 2) NOT NULL,
    PerformanceScore       DECIMAL(5, 2) NOT NULL,
    PromotionRecommended   TINYINT(1)    NOT NULL DEFAULT 0,
    ReviewCount            INT           NOT NULL DEFAULT 1,

    -- review-time snapshot used for attrition risk analytics
    AttritionFlag          TINYINT(1)    NOT NULL DEFAULT 0,
    TenureYears            DECIMAL(6, 2) NULL,
    AllocationPercentage   DECIMAL(5, 2) NULL,
    LoadBatchID            BIGINT        NULL,

    PRIMARY KEY (ReviewSK),
    UNIQUE KEY uq_fact_review_id (ReviewID),
    KEY idx_fact_employee (EmployeeSK),
    KEY idx_fact_date (DateSK),
    KEY idx_fact_dept (DepartmentSK),
    KEY idx_fact_project (ProjectSK),
    CONSTRAINT fk_fact_employee
        FOREIGN KEY (EmployeeSK) REFERENCES dim_employee (EmployeeSK),
    CONSTRAINT fk_fact_date
        FOREIGN KEY (DateSK) REFERENCES dim_date (DateSK),
    CONSTRAINT fk_fact_dept
        FOREIGN KEY (DepartmentSK) REFERENCES dim_department (DepartmentSK),
    CONSTRAINT fk_fact_project
        FOREIGN KEY (ProjectSK) REFERENCES dim_project (ProjectSK)
) ENGINE = InnoDB;

-- ============================================================
-- POPULATE dim_date  (recursive CTE, 2018-01-01 .. 2026-12-31)
-- Idempotent: re-running only fills the gaps.
-- ============================================================

INSERT INTO dim_date
    (DateSK, FullDate, Year, Quarter, Month, MonthName,
     DayOfMonth, WeekOfYear, IsWeekend, QuarterLabel)
WITH RECURSIVE calendar AS (
    SELECT DATE '2018-01-01' AS d
    UNION ALL
    SELECT d + INTERVAL 1 DAY
    FROM calendar
    WHERE d < DATE '2026-12-31'
),
missing AS (
    SELECT c.d
    FROM calendar c
    LEFT JOIN dim_date dd ON dd.FullDate = c.d
    WHERE dd.DateSK IS NULL
)
SELECT
    YEAR(m.d) * 10000 + MONTH(m.d) * 100 + DAY(m.d),
    m.d,
    YEAR(m.d),
    QUARTER(m.d),
    MONTH(m.d),
    DATE_FORMAT(m.d, '%M'),
    DAY(m.d),
    WEEKOFYEAR(m.d),
    DAYOFWEEK(m.d) IN (1, 7),
    CONCAT(YEAR(m.d), '-Q', QUARTER(m.d))
FROM missing m;

-- ============================================================
-- ETL CONTROL TABLE for the warehouse
-- ============================================================

CREATE TABLE IF NOT EXISTS dw_load_log (
    DWLoadID      BIGINT       NOT NULL AUTO_INCREMENT,
    ProcedureName VARCHAR(100) NOT NULL,
    RunStartTime  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    RunEndTime    DATETIME     NULL,
    RowsAffected  BIGINT       NULL,
    RunStatus     VARCHAR(20)  NOT NULL DEFAULT 'RUNNING',
    ErrorMessage  TEXT         NULL,
    PRIMARY KEY (DWLoadID)
) ENGINE = InnoDB;

-- restore the default for any later session statements
SET SESSION cte_max_recursion_depth = 1000;
