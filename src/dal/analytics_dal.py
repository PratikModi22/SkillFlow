"""
Analytics & Data Warehouse Access Layer (DAL)
=============================================
READING SEQUENCE STEP 5d: Understand OLAP Star Schema querying and ETL synchronization.

Provides read-only access to the dimensional model (Star Schema) and controls ETL pipelines.
Every analytical query maps to one of the SQL analytical views (`vw_*`) created in
`database/07_dashboard_queries.sql` or `database/10_incremental_loads.sql`.

Key Architectural Highlights:
-----------------------------
1. Zero Business Logic in Python:
   All complex computations (Window functions `LAG()`, `DENSE_RANK()`, `RANK()`, multi-table
   joins, and aggregations) are executed inside MySQL database views for maximum performance.
2. SCD Type 2 Auditing:
   Queries point-in-time headcount and salary evolution views (`vw_department_headcount_history`,
   `vw_pay_progression`) derived from `dim_employee`.
3. Warehouse Health & Reconciliation:
   Audits Data Warehouse integrity using `vw_oltp_dw_reconciliation` to detect any desynchronization
   or data drift between the OLTP tables and the Star Schema.
4. ETL Pipeline Triggers:
   Provides both full rebuilds (`run_full_refresh()`) and fast incremental deltas (`sync_warehouse()`).
"""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from ..db_manager import BaseDataManager
from ..exceptions import NotFoundError, QueryError

# Configure module logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger(__name__)


# ============================================================================
# [ANALYTICS DATA MANAGER]
# ============================================================================

class AnalyticsManager(BaseDataManager):
    """
    [CORE DATA ACCESS LAYER CLASS]
    Executes analytical queries against the OLAP dimensional star schema.
    """

    # ------------------------------------------------------------------
    # Executive KPI Cards & Filters
    # ------------------------------------------------------------------

    def kpi_summary(self) -> dict:
        """
        [CORE / IMPORTANT FUNCTION]
        Queries `vw_kpi_summary` to retrieve headline executive KPIs for the dashboard.

        Returns metrics:
        - total_employees, active_employees, terminated_employees, attrition_pct
        - avg_performance_score, avg_rating, total_reviews
        - promotable_employees, high_risk_attrition_count, bottleneck_project_count

        Returns:
        --------
        dict
            Dictionary containing executive KPI values.
        """
        row = self.fetch_one("SELECT * FROM vw_kpi_summary")
        return row or {}

    def department_list(self) -> list[str]:
        """
        [HELPER / UTILITY FUNCTION]
        Retrieves a sorted list of unique department names from `dim_department`.
        """
        return [
            r["DepartmentName"]
            for r in self.fetch_dicts(
                "SELECT DepartmentName FROM dim_department ORDER BY DepartmentName"
            )
        ]

    def year_range(self) -> tuple[int, int]:
        """
        [HELPER / UTILITY FUNCTION]
        Determines minimum and maximum review years present in `fact_performance_reviews`.
        Sets slider bounds on the Streamlit dashboard filter.
        """
        bounds = self.fetch_value(
            "SELECT CONCAT(MIN(dt.Year), ',', MAX(dt.Year)) "
            "FROM fact_performance_reviews f "
            "JOIN dim_date dt ON dt.DateSK = f.DateSK",
            default="",
        )
        if not bounds:
            return (2020, 2026)
        low, _, high = str(bounds).partition(",")
        return (int(low), int(high))

    # ------------------------------------------------------------------
    # Chart 1: Year-over-Year Performance Trend (SQL LAG)
    # ------------------------------------------------------------------

    def performance_trend_yoy(self) -> pd.DataFrame:
        """
        [CORE / IMPORTANT FUNCTION]
        Queries `vw_performance_trend_yoy` to display year-over-year performance changes.

        SQL Implementation:
        -------------------
        Uses SQL `LAG(avg_score, 1)` partitioned/ordered by review year to calculate:
        - `yoy_score_change`: Absolute change from prior year.
        - `yoy_score_pct`: Percentage growth or decline.
        - `yoy_volume_pct`: Growth in review evaluation volume.
        """
        try:
            return self.fetch_frame(
                """
                SELECT review_year, review_count, employees_reviewed,
                       avg_score, avg_rating, promotions, reviews_by_leavers,
                       prev_year_score, yoy_score_change, yoy_score_pct,
                       yoy_volume_pct
                FROM vw_performance_trend_yoy
                ORDER BY review_year
                """
            )
        except QueryError as exc:
            logger.error("performance_trend_yoy failed: %s", exc)
            raise

    def quarterly_trend(self, department: str | None = None) -> pd.DataFrame:
        """
        [HELPER / UTILITY FUNCTION]
        Aggregates average scores and ratings by calendar quarter (`QuarterLabel`).
        """
        sql = """
            SELECT dt.QuarterLabel AS quarter,
                   COUNT(*)        AS review_count,
                   ROUND(AVG(f.PerformanceScore), 2) AS avg_score,
                   ROUND(AVG(f.Rating), 2)           AS avg_rating
            FROM fact_performance_reviews f
            JOIN dim_date dt        ON dt.DateSK = f.DateSK
            JOIN dim_department dep ON dep.DepartmentSK = f.DepartmentSK
            WHERE 1 = 1
        """
        params: dict[str, Any] = {}
        if department:
            sql += " AND dep.DepartmentName = :department"
            params["department"] = department
        sql += " GROUP BY dt.QuarterLabel ORDER BY dt.QuarterLabel"
        return self.fetch_frame(sql, params)

    def score_distribution(self, department: str | None = None) -> pd.DataFrame:
        """
        [HELPER / UTILITY FUNCTION]
        Buckets employee review scores into discrete histogram bands (<40, 40-49, ..., 90-100).
        """
        sql = """
            SELECT
                CASE
                    WHEN f.PerformanceScore < 40 THEN '20-39'
                    WHEN f.PerformanceScore < 50 THEN '40-49'
                    WHEN f.PerformanceScore < 60 THEN '50-59'
                    WHEN f.PerformanceScore < 70 THEN '60-69'
                    WHEN f.PerformanceScore < 80 THEN '70-79'
                    WHEN f.PerformanceScore < 90 THEN '80-89'
                    ELSE '90-100'
                END                       AS score_band,
                COUNT(*)                  AS reviews,
                ROUND(AVG(f.PerformanceScore), 2) AS avg_score
            FROM fact_performance_reviews f
            JOIN dim_department dep ON dep.DepartmentSK = f.DepartmentSK
            WHERE 1 = 1
        """
        params: dict[str, Any] = {}
        if department:
            sql += " AND dep.DepartmentName = :department"
            params["department"] = department
        sql += " GROUP BY score_band ORDER BY score_band"
        return self.fetch_frame(sql, params)

    # ------------------------------------------------------------------
    # Chart 2: Top Performers Leaderboard (SQL DENSE_RANK)
    # ------------------------------------------------------------------

    def top_performers(
        self,
        department: str | None = None,
        top_n: int = 5,
        min_reviews: int = 2,
    ) -> pd.DataFrame:
        """
        [CORE / IMPORTANT FUNCTION]
        Queries `vw_top_performers_by_department` to rank employees within each department.

        SQL Implementation:
        -------------------
        Uses `DENSE_RANK() OVER (PARTITION BY DepartmentName ORDER BY avg_score DESC)`.
        `min_reviews` prevents outlier bias from employees with only a single review.
        """
        sql = """
            SELECT DepartmentName, EmployeeID, employee_name, JobRole, JobLevel,
                   MonthlyIncome, review_count, avg_score, avg_rating,
                   promotions, dept_rank
            FROM vw_top_performers_by_department
            WHERE review_count >= :min_reviews
        """
        params: dict[str, Any] = {"min_reviews": int(min_reviews)}
        if department:
            sql += " AND DepartmentName = :department"
            params["department"] = department
        sql += """
            ORDER BY DepartmentName, dept_rank, avg_score DESC
            LIMIT :cap
        """
        params["cap"] = int(top_n) * 20
        return self.fetch_frame(sql, params)

    def department_scorecard(self) -> pd.DataFrame:
        """
        [CORE / IMPORTANT FUNCTION]
        Computes department performance rankings using `RANK() OVER (ORDER BY AVG(f.PerformanceScore) DESC)`.
        Also calculates attrition percentage and total promotions per department.
        """
        return self.fetch_frame(
            """
            SELECT dep.DepartmentName                              AS department,
                   COUNT(f.ReviewSK)                              AS reviews,
                   COUNT(DISTINCT f.EmployeeSK)                   AS employees_reviewed,
                   ROUND(AVG(f.PerformanceScore), 2)              AS avg_score,
                   ROUND(AVG(f.Rating), 2)                        AS avg_rating,
                   SUM(f.PromotionRecommended)                    AS promotions,
                   ROUND(100 * SUM(f.AttritionFlag) / COUNT(*), 2) AS leaver_review_pct,
                   RANK() OVER (ORDER BY AVG(f.PerformanceScore) DESC) AS dept_rank
            FROM fact_performance_reviews f
            JOIN dim_department dep ON dep.DepartmentSK = f.DepartmentSK
            GROUP BY dep.DepartmentName
            ORDER BY avg_score DESC
            """
        )

    # ------------------------------------------------------------------
    # Chart 3: Employee Attrition Risk Model
    # ------------------------------------------------------------------

    def attrition_risk(
        self,
        department: str | None = None,
        risk_band: str | None = None,
        min_risk: float = 0.0,
        limit: int = 300,
    ) -> pd.DataFrame:
        """
        [CORE / IMPORTANT FUNCTION]
        Queries `vw_attrition_risk` to identify employees at risk of leaving.

        Business Risk Calculation:
        --------------------------
        Combines performance score, appraisal rating, tenure, and compensation level
        into a weighted composite risk score (0 to 100) and assigns a band (High, Medium, Low).
        """
        sql = """
            SELECT EmployeeID, employee_name, DepartmentName, JobRole, JobLevel,
                   MonthlyIncome, EmploymentStatus, YearsOfService,
                   review_count, avg_score, avg_rating, last_review_date,
                   risk_band, risk_score
            FROM vw_attrition_risk
            WHERE risk_score >= :min_risk
        """
        params: dict[str, Any] = {"min_risk": float(min_risk), "limit": int(limit)}
        if department:
            sql += " AND DepartmentName = :department"
            params["department"] = department
        if risk_band:
            sql += " AND risk_band = :risk_band"
            params["risk_band"] = risk_band
        sql += " ORDER BY risk_score DESC LIMIT :limit"
        return self.fetch_frame(sql, params)

    def attrition_by_department(self) -> pd.DataFrame:
        """
        [HELPER / UTILITY FUNCTION]
        Aggregates headcount, terminated leavers, and attrition percentage per department.
        """
        return self.fetch_frame(
            """
            SELECT dep.DepartmentName AS department,
                   COUNT(DISTINCT de.EmployeeSK)                       AS headcount,
                   SUM(de.EmploymentStatus = 'Terminated')             AS leavers,
                   ROUND(100 * SUM(de.EmploymentStatus = 'Terminated')
                         / COUNT(*), 2)                                 AS attrition_pct,
                   ROUND(AVG(CASE WHEN a.EmployeeID IS NOT NULL
                                   THEN a.risk_score END), 2)          AS avg_risk_score
            FROM dim_employee de
            JOIN dim_department dep ON dep.DepartmentID = de.DepartmentID
            LEFT JOIN vw_attrition_risk a ON a.EmployeeID = de.EmployeeID
            WHERE de.IsCurrent = 1
            GROUP BY dep.DepartmentName
            ORDER BY attrition_pct DESC
            """
        )

    def risk_band_summary(self) -> pd.DataFrame:
        """
        [HELPER / UTILITY FUNCTION]
        Summarizes employee counts and mean risk scores grouped by risk band (High, Medium, Low).
        """
        return self.fetch_frame(
            """
            SELECT risk_band, COUNT(*) AS employees,
                   ROUND(AVG(risk_score), 2) AS avg_risk_score
            FROM vw_attrition_risk
            GROUP BY risk_band
            ORDER BY avg_risk_score DESC
            """
        )

    # ------------------------------------------------------------------
    # Chart 4: Project Bottlenecks & Capacity
    # ------------------------------------------------------------------

    def project_bottlenecks(
        self,
        status: str | None = None,
        min_people: int = 1,
        limit: int = 40,
    ) -> pd.DataFrame:
        """
        [CORE / IMPORTANT FUNCTION]
        Queries `vw_project_bottlenecks` to identify heavily loaded projects with performance deficits.

        Calculates load rank and score gap compared to the overall company benchmark.
        Categorizes projects into 'Critical Bottleneck', 'Heavy Load', 'Underperforming', or 'Normal'.
        """
        sql = """
            SELECT ProjectID, ProjectName, ProjectType, ProjectStatus,
                   ProjectManagerName, people_assigned, total_allocation_pct,
                   avg_allocation_pct, review_count, avg_score,
                   company_avg_score, score_gap, load_rank,
                   lowest_score_rank, bottleneck_status
            FROM vw_project_bottlenecks
            WHERE people_assigned >= :min_people
        """
        params: dict[str, Any] = {"min_people": int(min_people), "limit": int(limit)}
        if status:
            sql += " AND ProjectStatus = :status"
            params["status"] = status
        sql += " ORDER BY total_allocation_pct DESC LIMIT :limit"
        return self.fetch_frame(sql, params)

    def bottleneck_status_counts(self) -> pd.DataFrame:
        """
        [HELPER / UTILITY FUNCTION]
        Returns project distribution across bottleneck classification categories.
        """
        return self.fetch_frame(
            """
            SELECT bottleneck_status, COUNT(*) AS projects,
                   ROUND(AVG(avg_score), 2) AS avg_score
            FROM vw_project_bottlenecks
            GROUP BY bottleneck_status
            ORDER BY projects DESC
            """
        )

    def project_allocation_by_department(self) -> pd.DataFrame:
        """
        [HELPER / UTILITY FUNCTION]
        Computes total allocation percentages and project counts across departments.
        Feeds the allocation heatmap on the executive dashboard.
        """
        return self.fetch_frame(
            """
            SELECT dep.DepartmentName  AS department,
                   pr.ProjectStatus    AS project_status,
                   ROUND(SUM(f.AllocationPercentage), 2) AS total_allocation,
                   COUNT(DISTINCT pr.ProjectSK)            AS projects,
                   ROUND(AVG(f.PerformanceScore), 2)       AS avg_score
            FROM fact_performance_reviews f
            JOIN dim_department dep ON dep.DepartmentSK = f.DepartmentSK
            JOIN dim_project pr     ON pr.ProjectSK    = f.ProjectSK
            GROUP BY dep.DepartmentName, pr.ProjectStatus
            ORDER BY total_allocation DESC
            """
        )

    # ------------------------------------------------------------------
    # Slowly Changing Dimension (SCD Type 2) Historical Views
    # ------------------------------------------------------------------

    def department_headcount_history(self) -> pd.DataFrame:
        """
        [CORE / IMPORTANT FUNCTION]
        Replays point-in-time annual department headcounts replayed from `dim_employee` SCD2 slices.
        Queries `vw_department_headcount_history`.
        """
        return self.fetch_frame(
            """
            SELECT snapshot_year, DepartmentName, headcount, avg_salary,
                   headcount_change, cumulative_headcount_in_year
            FROM vw_department_headcount_history
            ORDER BY snapshot_year, DepartmentName
            """
        )

    def pay_progression(self, employee_id: str) -> pd.DataFrame:
        """
        [CORE / IMPORTANT FUNCTION]
        Fetches the complete compensation and promotion history for an individual employee.
        Queries `vw_pay_progression` using `LAG(MonthlyIncome)` to track pay raises over time.
        """
        frame = self.fetch_frame(
            """
            SELECT EmployeeID, employee_name, DepartmentName, JobRole,
                   MonthlyIncome, prev_salary, prev_department,
                   salary_change, salary_change_pct, change_type,
                   EffectiveStartDate, EffectiveEndDate, IsCurrent
            FROM vw_pay_progression
            WHERE EmployeeID = :employee_id
            ORDER BY EffectiveStartDate
            """,
            {"employee_id": employee_id},
        )
        if frame.empty:
            raise NotFoundError(
                f"No warehouse history for employee '{employee_id}'."
            )
        return frame

    def scd2_version_stats(self) -> pd.DataFrame:
        """
        [HELPER / UTILITY FUNCTION]
        Audits total SCD Type 2 dimension version count grouped by change type
        (Initial Load, Department Transfer, Salary Adjustment, Promotion).
        """
        return self.fetch_frame(
            """
            SELECT change_type,
                   COUNT(*)              AS version_rows,
                   COUNT(DISTINCT EmployeeID) AS employees,
                   ROUND(AVG(salary_change_pct), 2) AS avg_salary_change_pct
            FROM vw_pay_progression
            GROUP BY change_type
            ORDER BY version_rows DESC
            """
        )

    def scd2_transfers(self, limit: int = 100) -> pd.DataFrame:
        """
        [CORE / IMPORTANT FUNCTION]
        Lists recorded department transfers from `vw_pay_progression`,
        showing previous department, new department, effective transfer date, and salary changes.
        """
        return self.fetch_frame(
            """
            SELECT EmployeeID, employee_name,
                   prev_department AS from_department,
                   DepartmentName  AS to_department,
                   EffectiveStartDate AS transfer_date,
                   MonthlyIncome, prev_salary, salary_change_pct
            FROM vw_pay_progression
            WHERE change_type = 'Department Transfer'
            ORDER BY EffectiveStartDate DESC
            LIMIT :limit
            """,
            {"limit": int(limit)},
        )

    # ------------------------------------------------------------------
    # Data Warehouse Quality & ETL Logging
    # ------------------------------------------------------------------

    def warehouse_row_counts(self) -> pd.DataFrame:
        """
        [HELPER / UTILITY FUNCTION]
        Queries row counts across all dimensions and fact tables for the data quality panel.
        """
        return self.fetch_frame(
            """
            SELECT 'dim_date'                 AS table_name, COUNT(*) AS rows_count FROM dim_date
            UNION ALL SELECT 'dim_department',           COUNT(*) FROM dim_department
            UNION ALL SELECT 'dim_project',              COUNT(*) FROM dim_project
            UNION ALL SELECT 'dim_employee',             COUNT(*) FROM dim_employee
            UNION ALL SELECT 'dim_employee (current)',   COUNT(*) FROM dim_employee WHERE IsCurrent = 1
            UNION ALL SELECT 'dim_employee (historical)',COUNT(*) FROM dim_employee WHERE IsCurrent = 0
            UNION ALL SELECT 'fact_performance_reviews',  COUNT(*) FROM fact_performance_reviews
            """
        )

    def load_log(self, limit: int = 20) -> pd.DataFrame:
        """
        [HELPER / UTILITY FUNCTION]
        Fetches execution history of ETL procedures from the `dw_load_log` audit table.
        """
        return self.fetch_frame(
            """
            SELECT DWLoadID, ProcedureName, RunStartTime, RunEndTime,
                   RowsAffected, RunStatus, ErrorMessage
            FROM dw_load_log
            ORDER BY DWLoadID DESC
            LIMIT :limit
            """,
            {"limit": int(limit)},
        )

    def run_full_refresh(self) -> list[tuple]:
        """
        [CORE / IMPORTANT FUNCTION]
        Executes a complete end-to-end Data Warehouse rebuild.
        Calls `sp_load_oltp_master` to refresh OLTP from staging,
        then calls `sp_load_dw_master` to reload all dimensions and fact tables.
        """
        logger.info("Running sp_load_oltp_master")
        self.db.call_procedure("sp_load_oltp_master")
        logger.info("Running sp_load_dw_master")
        return self.db.call_procedure("sp_load_dw_master")

    # ------------------------------------------------------------------
    # Reconciliation & Incremental Synchronization
    # ------------------------------------------------------------------

    # Fundamental integrity invariants that must evaluate to zero for the warehouse to be synchronized.
    RECONCILIATION_INVARIANTS = (
        ("employees_missing_from_dim",
         "OLTP employees with no Dim_Employee version"),
        ("dim_rows_without_employee",
         "Dim_Employee versions for an employee who no longer exists"),
        ("reviews_missing_from_fact",
         "performance_reviews with no fact row"),
        ("facts_without_review",
         "fact rows with no matching review"),
        ("reviews_outside_calendar",
         "reviews dated outside dim_date"),
        ("employees_with_two_open_versions",
         "employees with two open versions (ambiguous point-in-time join)"),
        ("stale_open_versions",
         "open versions that are not the newest one"),
    )

    def reconciliation(self) -> dict:
        """
        [CORE / IMPORTANT FUNCTION]
        Compares OLTP tables against Data Warehouse tables via `vw_oltp_dw_reconciliation`.

        Checks that all critical structural invariants equal zero (e.g. no missing dimension rows,
        no orphaned facts, no multiple open SCD2 versions).
        Returns a dictionary with status flags and pending drift counts.
        """
        row = self.fetch_one("SELECT * FROM vw_oltp_dw_reconciliation")
        if row is None:
            raise NotFoundError(
                "vw_oltp_dw_reconciliation is missing. Has "
                "database/10_incremental_loads.sql been deployed?"
            )

        row["invariants"] = {
            name: {"value": int(row.get(name) or 0), "label": label}
            for name, label in self.RECONCILIATION_INVARIANTS
        }
        row["invariants_ok"] = all(
            item["value"] == 0 for item in row["invariants"].values()
        )
        row["pending_changes"] = int(row.get("drifted_employees") or 0)
        return row

    def pending_changes(self, limit: int = 50) -> pd.DataFrame:
        """
        [HELPER / UTILITY FUNCTION]
        Queries `vw_dim_employee_drift` to list employees whose OLTP attributes differ from DW.
        """
        return self.fetch_frame(
            """
            SELECT EmployeeID, oltp_department_id, dw_department_id,
                   oltp_job_role, dw_job_role,
                   oltp_job_level, dw_job_level,
                   oltp_monthly_income, dw_monthly_income,
                   oltp_manager_id, dw_manager_id,
                   dw_version_start
            FROM vw_dim_employee_drift
            ORDER BY dw_version_start, EmployeeID
            LIMIT :limit
            """,
            {"limit": int(limit)},
        )

    def _report_row(self, procedure: str, *args) -> dict:
        """
        [HELPER / UTILITY FUNCTION]
        Executes a stored procedure and returns its summary report row as a dict.
        """
        rows = self.db.call_procedure_dict(procedure, *args)
        return rows

    def sync_warehouse(self, lookback_days: int = 30) -> dict:
        """
        [CORE / IMPORTANT FUNCTION]
        Publishes pending OLTP modifications to the Data Warehouse incrementally without full rebuild.

        Three-Step Delta Execution:
        ---------------------------
        1. `sp_load_dim_employee_delta()` : Seeds new employees and creates SCD2 versions for changed attributes.
        2. `sp_load_fact_delta(lookback_days)` : Refreshes newly added or re-pointed appraisal facts.
        3. `sp_repair_dim_employee_versions()` : Cleans up open version timestamps to enforce point-in-time integrity.

        Returns:
        --------
        dict
            Execution report with count of seeded employees, versioned employees, and refreshed fact rows.
        """
        lookback_days = max(0, min(int(lookback_days), 3650))

        logger.info("Incremental sync: dim delta")
        dim_report = self._report_row("sp_load_dim_employee_delta")

        logger.info("Incremental sync: fact delta (%d days)", lookback_days)
        fact_report = self._report_row("sp_load_fact_delta", lookback_days)

        logger.info("Incremental sync: reconciliation")
        self.db.call_procedure("sp_repair_dim_employee_versions")

        result = {
            "employees_seeded": int(dim_report.get("employees_seeded", 0)),
            "employees_versioned": int(dim_report.get("employees_versioned", 0)),
            "fact_rows_refreshed": int(fact_report.get("fact_rows_refreshed", 0)),
            "new_reviews": int(fact_report.get("new_reviews", 0)),
            "window_start": fact_report.get("window_start"),
            "reconciliation": self.reconciliation(),
        }
        logger.info(
            "Incremental sync complete: %d seeded, %d versioned, "
            "%d fact rows refreshed, %d new reviews",
            result["employees_seeded"], result["employees_versioned"],
            result["fact_rows_refreshed"], result["new_reviews"],
        )
        return result


__all__ = ["AnalyticsManager"]