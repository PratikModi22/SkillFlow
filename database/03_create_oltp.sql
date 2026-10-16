-- ============================================================
-- 03_create_oltp.sql
-- Operational (OLTP) relational schema for the HR application.
--
-- Grain / rules:
--   * Natural (surrogate-free) primary keys, 3NF.
--   * Departments and Projects are slowly-changing Type 1 in OLTP.
--     The historical tracking of employee changes lives in the DW.
--   * Employee self-reference on ManagerEmployeeID.
--
-- NOTE: do NOT DROP the database in this script. The staging data
--       loaded by etl/load_staging.py must survive.
--
-- FIXED-WIDTH BUSINESS KEYS
--   The synthetic keys are zero-padded to a fixed width by the
--   generator, so they are declared CHAR(n), not VARCHAR(n):
--
--       EmployeeID      EMP-0000001   "EMP-" + 7 digits = 11
--       DepartmentID    DEPT-002      "DEPT-" + 3 digits = 8
--       ProjectID       PROJ-00001    "PROJ-" + 5 digits = 10
--       AssignmentID    ASG-0000001   "ASG-" + 7 digits = 11
--       ReviewID        REV-00000001  "REV-" + 8 digits = 12
--       PhoneNumber     9205977775    10 digits, unpadded
--
--   A CHAR column states that contract in the schema instead of leaving
--   it implicit, and it keeps a 1,000,000th employee from producing a
--   different key width. The width must be identical in every layer
--   (staging, OLTP, OLAP) because MySQL refuses a foreign key between
--   columns of differing types, and because a type mismatch across a
--   join forces a conversion that disables index use.
--
--   DepartmentStatus stays VARCHAR: it reads CONSTANT today only
--   because this dataset happens to contain no non-active department.
-- ============================================================

USE employee_analytics_dw;

-- ============================================================
-- DEPARTMENTS
-- ============================================================

CREATE TABLE IF NOT EXISTS departments (
    DepartmentID     CHAR(8)      NOT NULL,
    DepartmentName   VARCHAR(100)  NOT NULL,
    DepartmentStatus VARCHAR(30)   NOT NULL DEFAULT 'Active',
    PRIMARY KEY (DepartmentID),
    CONSTRAINT uq_departments_name UNIQUE (DepartmentName)
) ENGINE = InnoDB;

-- ============================================================
-- EMPLOYEES
-- ============================================================

CREATE TABLE IF NOT EXISTS employees (
    EmployeeID         CHAR(11)      NOT NULL,
    FirstName          VARCHAR(100)   NOT NULL,
    LastName           VARCHAR(100)   NOT NULL,
    Email              VARCHAR(255)   NOT NULL,
    PhoneNumber        CHAR(10)      NULL,
    Gender             VARCHAR(30)    NULL,
    HireDate           DATE           NOT NULL,
    TerminationDate    DATE           NULL,
    EmploymentStatus   VARCHAR(30)    NOT NULL DEFAULT 'Active',
    DepartmentID       CHAR(8)       NOT NULL,
    JobRole            VARCHAR(100)   NOT NULL,
    JobLevel           INT            NOT NULL DEFAULT 1,
    MonthlyIncome      DECIMAL(12, 2) NOT NULL,
    ManagerEmployeeID  CHAR(11)      NULL,

    PRIMARY KEY (EmployeeID),
    UNIQUE KEY uq_employees_email (Email),
    KEY idx_employees_department (DepartmentID),
    KEY idx_employees_manager (ManagerEmployeeID),
    KEY idx_employees_hiredate (HireDate),

    CONSTRAINT fk_employees_department
        FOREIGN KEY (DepartmentID) REFERENCES departments (DepartmentID),
    CONSTRAINT fk_employees_manager
        FOREIGN KEY (ManagerEmployeeID) REFERENCES employees (EmployeeID)
) ENGINE = InnoDB;

-- ============================================================
-- PROJECTS
-- ============================================================

CREATE TABLE IF NOT EXISTS projects (
    ProjectID                    CHAR(10)      NOT NULL,
    ProjectName                  VARCHAR(255)  NOT NULL,
    ProjectType                  VARCHAR(100)  NULL,
    ProjectStartDate             DATE          NOT NULL,
    ProjectEndDate               DATE          NULL,
    ProjectStatus                VARCHAR(30)   NOT NULL DEFAULT 'In Progress',
    ProjectManagerEmployeeID     CHAR(11)      NULL,

    PRIMARY KEY (ProjectID),
    KEY idx_projects_manager (ProjectManagerEmployeeID),
    KEY idx_projects_status (ProjectStatus),

    CONSTRAINT fk_projects_manager
        FOREIGN KEY (ProjectManagerEmployeeID) REFERENCES employees (EmployeeID)
) ENGINE = InnoDB;

-- ============================================================
-- EMPLOYEE PROJECT ASSIGNMENTS  (many-to-many bridge)
-- ============================================================

CREATE TABLE IF NOT EXISTS employee_project_assignments (
    AssignmentID          CHAR(11)      NOT NULL,
    EmployeeID            CHAR(11)      NOT NULL,
    ProjectID             CHAR(10)      NOT NULL,
    AssignmentStartDate   DATE          NOT NULL,
    AssignmentEndDate     DATE          NULL,
    AssignmentRole        VARCHAR(100)  NULL,
    AllocationPercentage  DECIMAL(5, 2) NOT NULL DEFAULT 100.00,
    AssignmentStatus      VARCHAR(30)   NOT NULL DEFAULT 'Active',

    PRIMARY KEY (AssignmentID),
    KEY idx_assignments_employee (EmployeeID),
    KEY idx_assignments_project (ProjectID),
    CONSTRAINT uq_assignments_emp_proj UNIQUE (EmployeeID, ProjectID, AssignmentStartDate),

    CONSTRAINT chk_assignments_allocation
        CHECK (AllocationPercentage > 0 AND AllocationPercentage <= 100),
    CONSTRAINT fk_assignments_employee
        FOREIGN KEY (EmployeeID) REFERENCES employees (EmployeeID),
    CONSTRAINT fk_assignments_project
        FOREIGN KEY (ProjectID) REFERENCES projects (ProjectID)
) ENGINE = InnoDB;

-- ============================================================
-- PERFORMANCE REVIEWS
-- ============================================================

CREATE TABLE IF NOT EXISTS performance_reviews (
    ReviewID                 CHAR(12)      NOT NULL,
    EmployeeID               CHAR(11)      NOT NULL,
    ReviewDate               DATE           NOT NULL,
    Rating                   DECIMAL(5, 2)  NOT NULL,
    PerformanceScore         DECIMAL(5, 2)  NOT NULL,
    PromotionRecommendation  VARCHAR(30)    NOT NULL DEFAULT 'No',
    ReviewerEmployeeID       CHAR(11)      NULL,

    PRIMARY KEY (ReviewID),
    KEY idx_reviews_employee (EmployeeID),
    KEY idx_reviews_date (ReviewDate),
    CONSTRAINT uq_reviews_emp_date UNIQUE (EmployeeID, ReviewDate),

    CONSTRAINT chk_reviews_rating  CHECK (Rating >= 1 AND Rating <= 5),
    CONSTRAINT chk_reviews_score   CHECK (PerformanceScore >= 0 AND PerformanceScore <= 100),
    CONSTRAINT fk_reviews_employee
        FOREIGN KEY (EmployeeID) REFERENCES employees (EmployeeID),
    CONSTRAINT fk_reviews_reviewer
        FOREIGN KEY (ReviewerEmployeeID) REFERENCES employees (EmployeeID)
) ENGINE = InnoDB;