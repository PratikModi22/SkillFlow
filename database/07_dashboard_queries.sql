USE employee_analytics_dw;

-- ============================================================
-- vw_kpi_summary - headline numbers for the dashboard cards
-- ============================================================

CREATE OR REPLACE VIEW vw_kpi_summary AS
WITH current_emp AS (
    SELECT
        COUNT(*)                                              AS total_employees,
        SUM(EmploymentStatus = 'Active')                      AS active_employees,
        SUM(EmploymentStatus = 'Terminated')                  AS terminated_employees,
        ROUND(AVG(MonthlyIncome), 2)                          AS avg_salary,
        ROUND(AVG(YearsOfService), 2)                         AS avg_tenure
    FROM dim_employee
    WHERE IsCurrent = 1
),
review_stats AS (
    SELECT
        COUNT(*)                                              AS total_reviews,
        ROUND(AVG(PerformanceScore), 2)                       AS avg_score,
        ROUND(AVG(Rating), 2)                                 AS avg_rating,
        SUM(PromotionRecommended)                             AS promotions,
        SUM(AttritionFlag)                                    AS reviews_by_leavers
    FROM fact_performance_reviews
),
dept_stats AS (
    SELECT COUNT(*) AS departments FROM dim_department
)
SELECT
    ce.total_employees,
    ce.active_employees,
    ce.terminated_employees,
    ROUND(100 * ce.terminated_employees / ce.total_employees, 2) AS attrition_pct,
    ce.avg_salary,
    ce.avg_tenure,
    rs.total_reviews,
    rs.avg_score,
    rs.avg_rating,
    rs.promotions,
    ds.departments
FROM current_emp ce
CROSS JOIN review_stats rs
CROSS JOIN dept_stats ds;

-- ============================================================
-- vw_performance_trend_yoy - year over year, using LAG()
-- ============================================================

CREATE OR REPLACE VIEW vw_performance_trend_yoy AS
WITH yearly AS (
    SELECT
        dt.Year                                     AS review_year,
        COUNT(*)                                    AS review_count,
        COUNT(DISTINCT f.EmployeeSK)                AS employees_reviewed,
        ROUND(AVG(f.PerformanceScore), 2)           AS avg_score,
        ROUND(AVG(f.Rating), 2)                     AS avg_rating,
        SUM(f.PromotionRecommended)                 AS promotions,
        SUM(f.AttritionFlag)                         AS reviews_by_leavers
    FROM fact_performance_reviews f
    JOIN dim_date dt ON dt.DateSK = f.DateSK
    GROUP BY dt.Year
)
SELECT
    review_year,
    review_count,
    employees_reviewed,
    avg_score,
    avg_rating,
    promotions,
    reviews_by_leavers,
    LAG(avg_score) OVER (ORDER BY review_year)                       AS prev_year_score,
    ROUND(avg_score - LAG(avg_score) OVER (ORDER BY review_year), 2) AS yoy_score_change,
    ROUND(100 * (avg_score - LAG(avg_score) OVER (ORDER BY review_year))
          / NULLIF(LAG(avg_score) OVER (ORDER BY review_year), 0), 2) AS yoy_score_pct,
    ROUND(100 * (review_count - LAG(review_count) OVER (ORDER BY review_year))
          / NULLIF(LAG(review_count) OVER (ORDER BY review_year), 0), 2) AS yoy_volume_pct
FROM yearly
ORDER BY review_year;

-- ============================================================
-- vw_top_performers_by_department - DENSE_RANK() within department
-- ============================================================

CREATE OR REPLACE VIEW vw_top_performers_by_department AS
WITH emp_scores AS (
    SELECT
        de.EmployeeSK,
        de.EmployeeID,
        CONCAT(de.FirstName, ' ', de.LastName) AS employee_name,
        de.DepartmentName,
        de.JobRole,
        de.JobLevel,
        de.MonthlyIncome,
        COUNT(*)                             AS review_count,
        ROUND(AVG(f.PerformanceScore), 2)    AS avg_score,
        ROUND(AVG(f.Rating), 2)              AS avg_rating,
        SUM(f.PromotionRecommended)          AS promotions
    FROM fact_performance_reviews f
    JOIN dim_employee de ON de.EmployeeSK = f.EmployeeSK
    GROUP BY de.EmployeeSK, de.EmployeeID, employee_name,
             de.DepartmentName, de.JobRole, de.JobLevel, de.MonthlyIncome
)
SELECT
    DepartmentName,
    EmployeeID,
    employee_name,
    JobRole,
    JobLevel,
    MonthlyIncome,
    review_count,
    avg_score,
    avg_rating,
    promotions,
    DENSE_RANK() OVER (
        PARTITION BY DepartmentName
        ORDER BY avg_score DESC
    ) AS dept_rank
FROM emp_scores;

-- ============================================================
-- vw_attrition_risk - leavers plus low performers still employed
-- ============================================================

CREATE OR REPLACE VIEW vw_attrition_risk AS
WITH emp_profile AS (
    SELECT
        de.EmployeeSK,
        de.EmployeeID,
        CONCAT(de.FirstName, ' ', de.LastName) AS employee_name,
        de.DepartmentName,
        de.JobRole,
        de.JobLevel,
        de.MonthlyIncome,
        de.EmploymentStatus,
        de.HireDate,
        de.TerminationDate,
        de.YearsOfService,
        COUNT(f.ReviewSK)                        AS review_count,
        ROUND(AVG(f.PerformanceScore), 2)        AS avg_score,
        ROUND(AVG(f.Rating), 2)                  AS avg_rating,
        MAX(dt.FullDate)                         AS last_review_date
    FROM dim_employee de
    JOIN fact_performance_reviews f ON f.EmployeeSK = de.EmployeeSK
    JOIN dim_date dt              ON dt.DateSK    = f.DateSK
    WHERE de.IsCurrent = 1
    GROUP BY de.EmployeeSK, de.EmployeeID, employee_name, de.DepartmentName,
             de.JobRole, de.JobLevel, de.MonthlyIncome, de.EmploymentStatus,
             de.HireDate, de.TerminationDate, de.YearsOfService
)
SELECT
    EmployeeID,
    employee_name,
    DepartmentName,
    JobRole,
    JobLevel,
    MonthlyIncome,
    EmploymentStatus,
    YearsOfService,
    review_count,
    avg_score,
    avg_rating,
    last_review_date,
    CASE WHEN EmploymentStatus = 'Terminated' THEN 'Left Company'
         WHEN avg_score < 50 THEN 'High Risk - Low Score'
         WHEN avg_rating <= 2 THEN 'High Risk - Low Rating'
         WHEN YearsOfService < 1 THEN 'Medium Risk - New Joiner'
         ELSE 'Low Risk' END AS risk_band,
    -- simple 0-100 risk score, higher = more likely to leave
    LEAST(100, ROUND(
        (COALESCE(100 - avg_score, 0) * 0.5) +
        (COALESCE((5 - avg_rating) * 12.5, 0) * 0.3) +
        (CASE WHEN YearsOfService < 1 THEN 20 ELSE 0 END) +
        (CASE WHEN EmploymentStatus = 'Terminated' THEN 100 ELSE 0 END)
    , 2)) AS risk_score
FROM emp_profile;

-- ============================================================
-- vw_project_bottlenecks - DENSE_RANK() on total allocation
-- ============================================================

CREATE OR REPLACE VIEW vw_project_bottlenecks AS
WITH project_load AS (
    SELECT
        dp.ProjectSK,
        dp.ProjectID,
        dp.ProjectName,
        dp.ProjectType,
        dp.ProjectStatus,
        dp.ProjectManagerName,
        COUNT(DISTINCT f.EmployeeSK)              AS people_assigned,
        ROUND(SUM(f.AllocationPercentage), 2)     AS total_allocation_pct,
        ROUND(AVG(f.AllocationPercentage), 2)     AS avg_allocation_pct,
        COUNT(f.ReviewSK)                         AS review_count,
        ROUND(AVG(f.PerformanceScore), 2)         AS avg_score
    FROM fact_performance_reviews f
    JOIN dim_project dp ON dp.ProjectSK = f.ProjectSK
    GROUP BY dp.ProjectSK, dp.ProjectID, dp.ProjectName, dp.ProjectType,
             dp.ProjectStatus, dp.ProjectManagerName
),
benchmark AS (
    SELECT AVG(avg_score) AS company_avg_score FROM project_load
)
SELECT
    p.ProjectID,
    p.ProjectName,
    p.ProjectType,
    p.ProjectStatus,
    p.ProjectManagerName,
    p.people_assigned,
    p.total_allocation_pct,
    p.avg_allocation_pct,
    p.review_count,
    p.avg_score,
    ROUND(b.company_avg_score, 2)                                  AS company_avg_score,
    ROUND(p.avg_score - b.company_avg_score, 2)                    AS score_gap,
    DENSE_RANK() OVER (ORDER BY p.total_allocation_pct DESC)        AS load_rank,
    DENSE_RANK() OVER (ORDER BY p.avg_score ASC)                   AS lowest_score_rank,
    CASE
        WHEN p.avg_score < b.company_avg_score
         AND p.total_allocation_pct > 1000 THEN 'Critical Bottleneck'
        WHEN p.avg_score < b.company_avg_score THEN 'Underperforming'
        WHEN p.total_allocation_pct > 1000 THEN 'Over-Allocated'
        ELSE 'Healthy'
    END AS bottleneck_status
FROM project_load p
CROSS JOIN benchmark b;

-- ============================================================
-- vw_department_headcount_history - replays the SCD2 dimension
-- ============================================================

CREATE OR REPLACE VIEW vw_department_headcount_history AS
WITH year_ends AS (
    SELECT DISTINCT
        YEAR(FullDate)                     AS snapshot_year,
        YEAR(FullDate) * 10000 + 1231      AS DateSK
    FROM dim_date
    WHERE MONTH(FullDate) = 12 AND DAY(FullDate) = 31
),
snapshot AS (
    SELECT
        y.snapshot_year,
        de.DepartmentName,
        COUNT(DISTINCT de.EmployeeSK) AS headcount,
        ROUND(AVG(de.MonthlyIncome), 2) AS avg_salary
    FROM year_ends y
    JOIN dim_employee de
      ON y.DateSK BETWEEN de.EffectiveStartDate AND de.EffectiveEndDate
    WHERE de.EmploymentStatus = 'Active'
    GROUP BY y.snapshot_year, de.DepartmentName
)
SELECT
    snapshot_year,
    DepartmentName,
    headcount,
    avg_salary,
    headcount - LAG(headcount) OVER (
        PARTITION BY DepartmentName ORDER BY snapshot_year
    ) AS headcount_change,
    SUM(headcount) OVER (
        PARTITION BY snapshot_year ORDER BY DepartmentName
        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
    ) AS cumulative_headcount_in_year
FROM snapshot
ORDER BY snapshot_year, DepartmentName;

-- ============================================================
-- vw_pay_progression - SCD2 driven salary movement per employee
-- ============================================================

CREATE OR REPLACE VIEW vw_pay_progression AS
WITH versions AS (
    SELECT
        EmployeeID,
        CONCAT(FirstName, ' ', LastName) AS employee_name,
        DepartmentName,
        JobRole,
        MonthlyIncome,
        EffectiveStartDate,
        EffectiveEndDate,
        IsCurrent,
        LAG(MonthlyIncome) OVER (
            PARTITION BY EmployeeID ORDER BY EffectiveStartDate
        ) AS prev_salary,
        LAG(DepartmentName) OVER (
            PARTITION BY EmployeeID ORDER BY EffectiveStartDate
        ) AS prev_department
    FROM dim_employee
)
SELECT
    EmployeeID,
    employee_name,
    DepartmentName,
    JobRole,
    MonthlyIncome,
    prev_salary,
    prev_department,
    ROUND(MonthlyIncome - prev_salary, 2)            AS salary_change,
    ROUND(100 * (MonthlyIncome - prev_salary)
          / NULLIF(prev_salary, 0), 2)                AS salary_change_pct,
    CASE
        WHEN prev_department IS NOT NULL
         AND DepartmentName <> prev_department THEN 'Department Transfer'
        WHEN prev_salary IS NOT NULL
         AND MonthlyIncome > prev_salary            THEN 'Salary Increase'
        WHEN prev_salary IS NOT NULL
         AND MonthlyIncome < prev_salary            THEN 'Salary Decrease'
        ELSE 'Initial'
    END AS change_type,
    EffectiveStartDate,
    EffectiveEndDate,
    IsCurrent
FROM versions
ORDER BY EmployeeID, EffectiveStartDate;