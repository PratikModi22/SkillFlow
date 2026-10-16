USE employee_analytics_dw;

-- ============================================================
-- STAGING BATCH CONTROL
--
-- Fixed-width source keys are declared CHAR(n) to match the OLTP and
-- OLAP layers exactly (EmployeeID 11, DepartmentID 8, ProjectID 10,
-- AssignmentID 11, ReviewID 12, PhoneNumber 10). The widths must agree
-- across layers: MySQL rejects a foreign key between columns of
-- different types, and a mismatch across a join forces a per-row
-- conversion that stops an index being used.
--
-- SourceFileName stays VARCHAR. A filename is external input that can
-- be renamed, and CHAR would truncate the value rather than warn.
-- ============================================================

CREATE TABLE IF NOT EXISTS stg_load_batches (
    LoadBatchID BIGINT AUTO_INCREMENT PRIMARY KEY,
    BatchStartTime DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    BatchEndTime DATETIME NULL,
    BatchStatus VARCHAR(20) NOT NULL DEFAULT 'RUNNING',
    SourceSystem VARCHAR(100) DEFAULT 'Synthetic Data Generator',
    ErrorMessage TEXT NULL
);


-- ============================================================
-- DEPARTMENTS
-- Grain: one row per department
-- ============================================================

CREATE TABLE IF NOT EXISTS stg_departments (
    DepartmentID CHAR(8),
    DepartmentName VARCHAR(100),
    DepartmentStatus VARCHAR(30),

    LoadBatchID BIGINT,
    SourceFileName VARCHAR(255),
    LoadedAt DATETIME DEFAULT CURRENT_TIMESTAMP
);


-- ============================================================
-- EMPLOYEES
-- Grain: one row per current employee
-- ============================================================

CREATE TABLE IF NOT EXISTS stg_employees (
    EmployeeID CHAR(11),
    FirstName VARCHAR(100),
    LastName VARCHAR(100),
    Email VARCHAR(255),
    PhoneNumber CHAR(10),

    Gender VARCHAR(30),

    HireDate DATE,
    TerminationDate DATE,
    EmploymentStatus VARCHAR(30),

    DepartmentID CHAR(8),
    JobRole VARCHAR(100),
    JobLevel INT,
    MonthlyIncome DECIMAL(12,2),

    ManagerEmployeeID CHAR(11),

    LoadBatchID BIGINT,
    SourceFileName VARCHAR(255),
    LoadedAt DATETIME DEFAULT CURRENT_TIMESTAMP
);


-- ============================================================
-- EMPLOYEE HISTORY
-- Grain: one historical version of one employee
-- ============================================================

CREATE TABLE IF NOT EXISTS stg_employee_history (

    EmployeeID CHAR(11),

    DepartmentID CHAR(8),
    JobRole VARCHAR(100),
    JobLevel INT,
    MonthlyIncome DECIMAL(12,2),
    ManagerEmployeeID CHAR(11),

    EffectiveStartDate DATE,
    EffectiveEndDate DATE,
    IsCurrent BOOLEAN,

    LoadBatchID BIGINT,
    SourceFileName VARCHAR(255),
    LoadedAt DATETIME DEFAULT CURRENT_TIMESTAMP
);


-- ============================================================
-- PROJECTS
-- Grain: one row per project
-- ============================================================

CREATE TABLE IF NOT EXISTS stg_projects (
    ProjectID CHAR(10),
    ProjectName VARCHAR(255),
    ProjectType VARCHAR(100),

    ProjectStartDate DATE,
    ProjectEndDate DATE,

    ProjectStatus VARCHAR(30),

    ProjectManagerEmployeeID CHAR(11),

    LoadBatchID BIGINT,
    SourceFileName VARCHAR(255),
    LoadedAt DATETIME DEFAULT CURRENT_TIMESTAMP
);


-- ============================================================
-- EMPLOYEE PROJECT ASSIGNMENTS
-- Grain: one employee-project assignment period
-- ============================================================

CREATE TABLE IF NOT EXISTS stg_employee_project_assignments (
    AssignmentID CHAR(11),

    EmployeeID CHAR(11),
    ProjectID CHAR(10),

    AssignmentStartDate DATE,
    AssignmentEndDate DATE,

    AssignmentRole VARCHAR(100),
    AllocationPercentage DECIMAL(5,2),
    AssignmentStatus VARCHAR(30),

    LoadBatchID BIGINT,
    SourceFileName VARCHAR(255),
    LoadedAt DATETIME DEFAULT CURRENT_TIMESTAMP
);


-- ============================================================
-- PERFORMANCE REVIEWS
-- Grain: one performance review event for one employee
-- ============================================================

CREATE TABLE IF NOT EXISTS stg_performance_reviews (
    ReviewID CHAR(12),

    EmployeeID CHAR(11),
    ReviewDate DATE,

    Rating DECIMAL(5,2),
    PerformanceScore DECIMAL(5,2),

    PromotionRecommendation VARCHAR(30),

    ReviewerEmployeeID CHAR(11),

    LoadBatchID BIGINT,
    SourceFileName VARCHAR(255),
    LoadedAt DATETIME DEFAULT CURRENT_TIMESTAMP
);
