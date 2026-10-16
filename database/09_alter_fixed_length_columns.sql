-- ============================================================
-- 09_alter_fixed_length_columns.sql
-- Convert fixed-width business keys from VARCHAR(n) to CHAR(n).
--
-- WHY THIS EXISTS
--   02/03/04_create_*.sql now declare these columns as CHAR(n)
--   directly, so a database built with `run_pipeline.py --reset`
--   never needs this file. It exists for a database that was built
--   before that change, which is the usual case once the project has
--   been run once.
--
-- WHAT CHANGES
--   38 columns across the three layers, all zero-padded keys or a
--   phone number, none of them free text:
--
--       EmployeeID                 CHAR(11)   "EMP-" + 7 digits
--       DepartmentID               CHAR(8)    "DEPT-" + 3 digits
--       ProjectID                  CHAR(10)   "PROJ-" + 5 digits
--       AssignmentID               CHAR(11)   "ASG-" + 7 digits
--       ReviewID                   CHAR(12)   "REV-" + 8 digits
--       ManagerEmployeeID          CHAR(11)   same shape as EmployeeID
--       ProjectManagerEmployeeID   CHAR(11)   same shape as EmployeeID
--       ReviewerEmployeeID         CHAR(11)   same shape as EmployeeID
--       PhoneNumber                CHAR(10)   10 digits, unpadded
--       QuarterLabel               CHAR(7)    "YYYY-Qn"
--
--   The widths come from the generator, not from guesswork: every
--   value in the current dataset is exactly that long.
--
-- DELIBERATELY NOT CONVERTED
--   A few other columns read as constant-length today and are still
--   left as VARCHAR, because they are constant by accident rather
--   than by contract:
--
--       departments.DepartmentStatus    all 'Active' in this dataset,
--                                        but 'Inactive' is valid
--       dw_load_log.RunStatus           all 'SUCCESS', but 'FAILED'
--                                        is valid
--       stg_load_batches.BatchStatus    an ETL control value
--       stg_*.SourceFileName            fixed per table, but it is
--                                        external input that can change
--
--   CHAR(n) compares with trailing spaces ignored, so a status column
--   narrowed to its current value would quietly treat 'FAILED' and
--   'FAILED ' as equal, and would truncate anything longer. Those stay
--   VARCHAR.
--
-- MEASURED PERFORMANCE
--   Three states, 4 interleaved rounds, best of 7 executions each, with
--   ANALYZE TABLE between every state change:
--
--       A  original schema (VARCHAR + default indexes)   2088.6 ms
--       B  + index tuning (VARCHAR + tuned indexes)     1895.2 ms   A/B 1.10x
--       C  + this conversion (CHAR + tuned indexes)      1841.8 ms   B/C 1.03x
--                                                        A/C 1.13x
--
--   The conversion is worth about 3% on its own. Where it helps is the
--   queries that join or group on a fixed-width key:
--
--       quarterly_trend (dept filter)   12.7 ms -> 10.7 ms   1.19x
--       scd2_transfers                 223.3 ms -> 200.4 ms  1.11x
--       scd2_version_stats             260.3 ms -> 238.0 ms  1.09x
--
--   Worst case is year_range at 11.1 ms -> 12.1 ms, an 11 ms query
--   where the difference is close to noise.
--
--   THE POINT OF THIS CHANGE IS NOT SPEED
--   Measured across all 14 affected tables, total index storage is
--   71.05 MB before and 71.05 MB after: no change at all. InnoDB stores
--   CHAR(n) as variable length and does not keep the padding, so CHAR
--   buys no disk space here. What it buys is a schema that states the
--   key format instead of leaving it implicit in a generator script,
--   and a small gain on fixed-width comparisons.
--
-- HOW IT WORKS
--   1. Validate - abort if any value is too long or carries stray
--      whitespace. Losing a key to truncation is much worse than not
--      converting the column at all.
--   2. Drop the seven string foreign keys. MySQL refuses a foreign key
--      whose two sides have different types, and every column below is
--      on one, so they cannot be converted one at a time.
--   3. Convert all 38 columns.
--   4. Recreate the foreign keys with their original definitions.
--   5. ANALYZE, because a type change rewrites every index on the
--      column and resets the optimizer's statistics.
--   6. Verify.
--
--   Re-runnable. Steps 2-5 are all safe to repeat, and each FK drop is
--   guarded, so an interrupted run can simply be run again.
-- ============================================================

USE employee_analytics_dw;

SET SESSION sql_mode = CONCAT(@@sql_mode, ',STRICT_ALL_TABLES');


-- ============================================================
-- 0. HELPER PROCEDURES
-- ============================================================

DROP PROCEDURE IF EXISTS sp_drop_fk_if_exists;
DROP PROCEDURE IF EXISTS sp_check_fixed_width_fits;

DELIMITER //

CREATE PROCEDURE sp_drop_fk_if_exists(IN p_table VARCHAR(64), IN p_fk VARCHAR(64))
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
               WHERE CONSTRAINT_SCHEMA = DATABASE()
                 AND TABLE_NAME                 = p_table
                 AND CONSTRAINT_NAME            = p_fk
                 AND CONSTRAINT_TYPE            = 'FOREIGN KEY') THEN
        SET @sql = CONCAT('ALTER TABLE `', p_table, '` DROP FOREIGN KEY `', p_fk, '`');
        PREPARE stmt FROM @sql;
        EXECUTE stmt;
        DEALLOCATE PREPARE stmt;
    END IF;
END //


-- Walks the conversion list and refuses to continue if a column holds
-- a value that CHAR(n) could not hold faithfully. Without this a bad
-- row would be truncated by the ALTER in strict mode, or silently
-- padded if strict mode were off.
CREATE PROCEDURE sp_check_fixed_width_fits()
BEGIN
    DECLARE v_table  VARCHAR(64);
    DECLARE v_column VARCHAR(64);
    DECLARE v_width  INT;
    DECLARE v_bad    BIGINT DEFAULT 0;
    DECLARE v_msg    VARCHAR(255);
    DECLARE v_done   INT    DEFAULT 0;

    DECLARE cur CURSOR FOR
        SELECT t, col, want FROM (
            SELECT 'stg_departments' t, 'DepartmentID' col, 8 want UNION ALL
            SELECT 'stg_employees',                   'EmployeeID',           11 UNION ALL
            SELECT 'stg_employees',                   'DepartmentID',         8 UNION ALL
            SELECT 'stg_employees',                   'ManagerEmployeeID',   11 UNION ALL
            SELECT 'stg_employees',                   'PhoneNumber',         10 UNION ALL
            SELECT 'stg_employee_history',            'EmployeeID',           11 UNION ALL
            SELECT 'stg_employee_history',            'DepartmentID',         8 UNION ALL
            SELECT 'stg_employee_history',            'ManagerEmployeeID',   11 UNION ALL
            SELECT 'stg_projects',                    'ProjectID',           10 UNION ALL
            SELECT 'stg_projects',                    'ProjectManagerEmployeeID', 11 UNION ALL
            SELECT 'stg_employee_project_assignments','AssignmentID',        11 UNION ALL
            SELECT 'stg_employee_project_assignments','EmployeeID',          11 UNION ALL
            SELECT 'stg_employee_project_assignments','ProjectID',           10 UNION ALL
            SELECT 'stg_performance_reviews',         'ReviewID',            12 UNION ALL
            SELECT 'stg_performance_reviews',         'EmployeeID',          11 UNION ALL
            SELECT 'stg_performance_reviews',         'ReviewerEmployeeID',  11 UNION ALL
            SELECT 'departments',                     'DepartmentID',         8 UNION ALL
            SELECT 'employees',                       'EmployeeID',           11 UNION ALL
            SELECT 'employees',                       'DepartmentID',         8 UNION ALL
            SELECT 'employees',                       'ManagerEmployeeID',   11 UNION ALL
            SELECT 'employees',                       'PhoneNumber',         10 UNION ALL
            SELECT 'projects',                        'ProjectID',           10 UNION ALL
            SELECT 'projects',                        'ProjectManagerEmployeeID', 11 UNION ALL
            SELECT 'employee_project_assignments',    'AssignmentID',        11 UNION ALL
            SELECT 'employee_project_assignments',    'EmployeeID',          11 UNION ALL
            SELECT 'employee_project_assignments',    'ProjectID',           10 UNION ALL
            SELECT 'performance_reviews',             'ReviewID',            12 UNION ALL
            SELECT 'performance_reviews',             'EmployeeID',          11 UNION ALL
            SELECT 'performance_reviews',             'ReviewerEmployeeID',  11 UNION ALL
            SELECT 'dim_department',                  'DepartmentID',         8 UNION ALL
            SELECT 'dim_project',                     'ProjectID',           10 UNION ALL
            SELECT 'dim_project',                     'ProjectManagerEmployeeID', 11 UNION ALL
            SELECT 'dim_employee',                    'EmployeeID',           11 UNION ALL
            SELECT 'dim_employee',                    'DepartmentID',         8 UNION ALL
            SELECT 'dim_employee',                    'ManagerEmployeeID',   11 UNION ALL
            SELECT 'fact_performance_reviews',        'ReviewID',            12 UNION ALL
            SELECT 'dim_date',                        'QuarterLabel',         7
        ) AS list;

    DECLARE CONTINUE HANDLER FOR NOT FOUND SET v_done = 1;

    OPEN cur;
    read_loop: LOOP
        FETCH cur INTO v_table, v_column, v_width;
        IF v_done = 1 THEN
            LEAVE read_loop;
        END IF;

        -- Nothing to check if the table or column is absent.
        IF NOT EXISTS (SELECT 1 FROM information_schema.COLUMNS
                       WHERE TABLE_SCHEMA = DATABASE()
                         AND TABLE_NAME   = v_table
                         AND COLUMN_NAME  = v_column) THEN
            ITERATE read_loop;
        END IF;

        SET @q = CONCAT(
            'SELECT COUNT(*) INTO @fits_bad FROM `', v_table, '` ',
            'WHERE `', v_column, '` IS NOT NULL ',
            '  AND (CHAR_LENGTH(`', v_column, '`) > ', v_width,
            '       OR `', v_column, '` <> TRIM(`', v_column, '`))');
        PREPARE stmt FROM @q;
        EXECUTE stmt;
        DEALLOCATE PREPARE stmt;

        SET v_bad = @fits_bad;
        IF v_bad > 0 THEN
            SET v_msg = CONCAT('ABORT: ', v_table, '.', v_column, ' has ', v_bad,
                               ' value(s) that CHAR(', v_width, ') cannot hold exactly');
            SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = v_msg;
        END IF;
    END LOOP;
    CLOSE cur;
END //

DELIMITER ;


-- ============================================================
-- 1. VALIDATE
-- Raises SQLSTATE 45000 and stops the script on the first offending
-- column, before anything has been altered.
-- ============================================================

CALL sp_check_fixed_width_fits();
SELECT 'all 38 columns fit their target width' AS validation;


-- ============================================================
-- 2. DROP THE STRING FOREIGN KEYS
-- Definitions are restored verbatim in step 4.
-- ============================================================

CALL sp_drop_fk_if_exists('employees',                    'fk_employees_department');
CALL sp_drop_fk_if_exists('employees',                    'fk_employees_manager');
CALL sp_drop_fk_if_exists('projects',                     'fk_projects_manager');
CALL sp_drop_fk_if_exists('employee_project_assignments', 'fk_assignments_employee');
CALL sp_drop_fk_if_exists('employee_project_assignments', 'fk_assignments_project');
CALL sp_drop_fk_if_exists('performance_reviews',          'fk_reviews_employee');
CALL sp_drop_fk_if_exists('performance_reviews',          'fk_reviews_reviewer');


-- ============================================================
-- 3. CONVERT THE COLUMNS
-- MODIFY restates the whole column definition, so NOT NULL and
-- DEFAULT are repeated exactly as in 02/03/04_create_*.sql.
-- Columns already CHAR are simply rewritten as themselves.
-- ============================================================

-- staging
ALTER TABLE stg_departments
    MODIFY DepartmentID CHAR(8) NULL;
ALTER TABLE stg_employees
    MODIFY EmployeeID CHAR(11) NULL,
    MODIFY DepartmentID CHAR(8) NULL,
    MODIFY ManagerEmployeeID CHAR(11) NULL,
    MODIFY PhoneNumber CHAR(10) NULL;
ALTER TABLE stg_employee_history
    MODIFY EmployeeID CHAR(11) NULL,
    MODIFY DepartmentID CHAR(8) NULL,
    MODIFY ManagerEmployeeID CHAR(11) NULL;
ALTER TABLE stg_projects
    MODIFY ProjectID CHAR(10) NULL,
    MODIFY ProjectManagerEmployeeID CHAR(11) NULL;
ALTER TABLE stg_employee_project_assignments
    MODIFY AssignmentID CHAR(11) NULL,
    MODIFY EmployeeID CHAR(11) NULL,
    MODIFY ProjectID CHAR(10) NULL;
ALTER TABLE stg_performance_reviews
    MODIFY ReviewID CHAR(12) NULL,
    MODIFY EmployeeID CHAR(11) NULL,
    MODIFY ReviewerEmployeeID CHAR(11) NULL;

-- OLTP
ALTER TABLE departments
    MODIFY DepartmentID CHAR(8) NOT NULL;
ALTER TABLE employees
    MODIFY EmployeeID CHAR(11) NOT NULL,
    MODIFY DepartmentID CHAR(8) NOT NULL,
    MODIFY ManagerEmployeeID CHAR(11) NULL,
    MODIFY PhoneNumber CHAR(10) NULL;
ALTER TABLE projects
    MODIFY ProjectID CHAR(10) NOT NULL,
    MODIFY ProjectManagerEmployeeID CHAR(11) NULL;
ALTER TABLE employee_project_assignments
    MODIFY AssignmentID CHAR(11) NOT NULL,
    MODIFY EmployeeID CHAR(11) NOT NULL,
    MODIFY ProjectID CHAR(10) NOT NULL;
ALTER TABLE performance_reviews
    MODIFY ReviewID CHAR(12) NOT NULL,
    MODIFY EmployeeID CHAR(11) NOT NULL,
    MODIFY ReviewerEmployeeID CHAR(11) NULL;

-- OLAP
ALTER TABLE dim_department
    MODIFY DepartmentID CHAR(8) NOT NULL;
ALTER TABLE dim_project
    MODIFY ProjectID CHAR(10) NOT NULL,
    MODIFY ProjectManagerEmployeeID CHAR(11) NULL;
ALTER TABLE dim_employee
    MODIFY EmployeeID CHAR(11) NOT NULL,
    MODIFY DepartmentID CHAR(8) NOT NULL,
    MODIFY ManagerEmployeeID CHAR(11) NULL;
ALTER TABLE fact_performance_reviews
    MODIFY ReviewID CHAR(12) NOT NULL;
ALTER TABLE dim_date
    MODIFY QuarterLabel CHAR(7) NOT NULL;


-- ============================================================
-- 4. RECREATE THE FOREIGN KEYS
-- Definitions copied verbatim from 03_create_oltp.sql.
-- ============================================================

ALTER TABLE employees
    ADD CONSTRAINT fk_employees_department
        FOREIGN KEY (DepartmentID) REFERENCES departments (DepartmentID),
    ADD CONSTRAINT fk_employees_manager
        FOREIGN KEY (ManagerEmployeeID) REFERENCES employees (EmployeeID);
ALTER TABLE projects
    ADD CONSTRAINT fk_projects_manager
        FOREIGN KEY (ProjectManagerEmployeeID) REFERENCES employees (EmployeeID);
ALTER TABLE employee_project_assignments
    ADD CONSTRAINT fk_assignments_employee
        FOREIGN KEY (EmployeeID) REFERENCES employees (EmployeeID),
    ADD CONSTRAINT fk_assignments_project
        FOREIGN KEY (ProjectID) REFERENCES projects (ProjectID);
ALTER TABLE performance_reviews
    ADD CONSTRAINT fk_reviews_employee
        FOREIGN KEY (EmployeeID) REFERENCES employees (EmployeeID),
    ADD CONSTRAINT fk_reviews_reviewer
        FOREIGN KEY (ReviewerEmployeeID) REFERENCES employees (EmployeeID);


-- ============================================================
-- 5. REFRESH STATISTICS
-- ============================================================

ANALYZE TABLE stg_departments;
ANALYZE TABLE stg_employees;
ANALYZE TABLE stg_employee_history;
ANALYZE TABLE stg_projects;
ANALYZE TABLE stg_employee_project_assignments;
ANALYZE TABLE stg_performance_reviews;
ANALYZE TABLE departments;
ANALYZE TABLE employees;
ANALYZE TABLE projects;
ANALYZE TABLE employee_project_assignments;
ANALYZE TABLE performance_reviews;
ANALYZE TABLE dim_department;
ANALYZE TABLE dim_project;
ANALYZE TABLE dim_employee;
ANALYZE TABLE fact_performance_reviews;
ANALYZE TABLE dim_date;


-- ============================================================
-- 6. VERIFY
-- ============================================================

-- 6a. nothing left as VARCHAR. Expect zero rows.
SELECT c.TABLE_NAME, c.COLUMN_NAME, c.CHARACTER_MAXIMUM_LENGTH
FROM information_schema.COLUMNS c
WHERE c.TABLE_SCHEMA = DATABASE()
  AND c.DATA_TYPE = 'varchar'
  AND (c.TABLE_NAME, c.COLUMN_NAME) IN (
        ('stg_departments','DepartmentID'), ('stg_employees','EmployeeID'),
        ('stg_employees','DepartmentID'), ('stg_employees','ManagerEmployeeID'),
        ('stg_employees','PhoneNumber'), ('stg_employee_history','EmployeeID'),
        ('stg_employee_history','DepartmentID'), ('stg_employee_history','ManagerEmployeeID'),
        ('stg_projects','ProjectID'), ('stg_projects','ProjectManagerEmployeeID'),
        ('stg_employee_project_assignments','AssignmentID'),
        ('stg_employee_project_assignments','EmployeeID'),
        ('stg_employee_project_assignments','ProjectID'),
        ('stg_performance_reviews','ReviewID'), ('stg_performance_reviews','EmployeeID'),
        ('stg_performance_reviews','ReviewerEmployeeID'), ('departments','DepartmentID'),
        ('employees','EmployeeID'), ('employees','DepartmentID'),
        ('employees','ManagerEmployeeID'), ('employees','PhoneNumber'),
        ('projects','ProjectID'), ('projects','ProjectManagerEmployeeID'),
        ('employee_project_assignments','AssignmentID'),
        ('employee_project_assignments','EmployeeID'),
        ('employee_project_assignments','ProjectID'), ('performance_reviews','ReviewID'),
        ('performance_reviews','EmployeeID'), ('performance_reviews','ReviewerEmployeeID'),
        ('dim_department','DepartmentID'), ('dim_project','ProjectID'),
        ('dim_project','ProjectManagerEmployeeID'), ('dim_employee','EmployeeID'),
        ('dim_employee','DepartmentID'), ('dim_employee','ManagerEmployeeID'),
        ('fact_performance_reviews','ReviewID'), ('dim_date','QuarterLabel'))
ORDER BY c.TABLE_NAME, c.COLUMN_NAME;

-- 6b. all 11 foreign keys restored (7 string + 4 INT on the fact table)
SELECT TABLE_NAME, CONSTRAINT_NAME, COLUMN_NAME, REFERENCED_TABLE_NAME, REFERENCED_COLUMN_NAME
FROM information_schema.KEY_COLUMN_USAGE
WHERE TABLE_SCHEMA = DATABASE()
  AND REFERENCED_TABLE_NAME IS NOT NULL
ORDER BY TABLE_NAME, CONSTRAINT_NAME;

-- 6c. no key changed shape, and none is padded. Expect 0.
SELECT 'keys of unexpected width' AS check_name, COUNT(*) AS problems FROM (
    SELECT EmployeeID     FROM employees                     WHERE CHAR_LENGTH(EmployeeID)     <> 11
    UNION ALL SELECT EmployeeID     FROM performance_reviews               WHERE CHAR_LENGTH(EmployeeID)     <> 11
    UNION ALL SELECT EmployeeID     FROM employee_project_assignments     WHERE CHAR_LENGTH(EmployeeID)     <> 11
    UNION ALL SELECT EmployeeID     FROM dim_employee                      WHERE CHAR_LENGTH(EmployeeID)     <> 11
    UNION ALL SELECT ManagerEmployeeID FROM employees                    WHERE CHAR_LENGTH(ManagerEmployeeID) <> 11
    UNION ALL SELECT ReviewerEmployeeID FROM performance_reviews          WHERE CHAR_LENGTH(ReviewerEmployeeID) <> 11
    UNION ALL SELECT DepartmentID   FROM departments                      WHERE CHAR_LENGTH(DepartmentID)   <> 8
    UNION ALL SELECT DepartmentID   FROM employees                        WHERE CHAR_LENGTH(DepartmentID)   <> 8
    UNION ALL SELECT DepartmentID   FROM dim_department                   WHERE CHAR_LENGTH(DepartmentID)   <> 8
    UNION ALL SELECT ProjectID      FROM projects                         WHERE CHAR_LENGTH(ProjectID)      <> 10
    UNION ALL SELECT ProjectID      FROM dim_project                      WHERE CHAR_LENGTH(ProjectID)      <> 10
    UNION ALL SELECT ProjectManagerEmployeeID FROM dim_project            WHERE CHAR_LENGTH(ProjectManagerEmployeeID) <> 11
    UNION ALL SELECT AssignmentID   FROM employee_project_assignments     WHERE CHAR_LENGTH(AssignmentID)   <> 11
    UNION ALL SELECT ReviewID       FROM performance_reviews              WHERE CHAR_LENGTH(ReviewID)       <> 12
    UNION ALL SELECT ReviewID       FROM fact_performance_reviews          WHERE CHAR_LENGTH(ReviewID)       <> 12
    UNION ALL SELECT QuarterLabel   FROM dim_date                         WHERE CHAR_LENGTH(QuarterLabel)   <> 7
) AS bad;

DROP PROCEDURE IF EXISTS sp_drop_fk_if_exists;
DROP PROCEDURE IF EXISTS sp_check_fixed_width_fits;