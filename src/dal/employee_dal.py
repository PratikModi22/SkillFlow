"""
Employee Data Access Layer (DAL) & SCD Type 2 Bridge
====================================================
READING SEQUENCE STEP 5a: Understand OLTP operations and Data Warehouse SCD2 synchronization.

This module provides the complete Data Access Object (DAO) for the `employees` table
in the OLTP database, and serves as the operational bridge to Slowly Changing Dimension
Type 2 (SCD2) versioning in `dim_employee`.

Key Architectural Responsibilities:
-----------------------------------
1. OLTP CRUD Operations: Full lifecycle management (Create, Read, Update, Delete) for employees.
2. Atomic Dual-Write (OLTP + DW):
   - When an employee is created or updated, both the OLTP write and the Data Warehouse
     dimension version update are executed inside a single database transaction.
3. SCD Type 2 Department Transfers:
   - When an employee changes departments in the UI, `change_department()` calls the stored
     procedure `sp_apply_employee_change`, which closes the current version (sets `IsCurrent=0`
     and `EffectiveEndDate`) and inserts a brand new version (`IsCurrent=1`).
4. Exception Safety:
   - Every public method catches raw SQLAlchemy errors and raises typed application exceptions
     (`NotFoundError`, `DuplicateRecordError`, `ValidationError`, `QueryError`), ensuring the UI
     never crashes with unhandled tracebacks.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

import pandas as pd
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from ..db_manager import BaseDataManager, translate_db_error
from ..exceptions import (
    DuplicateRecordError,
    NotFoundError,
    QueryError,
    ValidationError,
)
from ..models.employee import Employee

# Configure module logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger(__name__)

# Standard SQL projection columns for employee queries
SELECT_COLUMNS = """
    e.EmployeeID, e.FirstName, e.LastName, e.Email, e.PhoneNumber, e.Gender,
    e.HireDate, e.TerminationDate, e.EmploymentStatus, e.DepartmentID,
    d.DepartmentName, e.JobRole, e.JobLevel, e.MonthlyIncome, e.ManagerEmployeeID
"""


# ============================================================================
# [EMPLOYEE DATA MANAGER]
# ============================================================================

class EmployeeManager(BaseDataManager):
    """
    [CORE DATA ACCESS LAYER CLASS]
    Encapsulates all database queries and transactions for Employee records.
    Inherits connection pool access and query execution helpers from BaseDataManager.
    """

    # ------------------------------------------------------------------
    # Query Builders & Read Operations
    # ------------------------------------------------------------------

    def _employee_predicate(
        self,
        search: str | None,
        department_id: str | None,
        employment_status: str | None,
    ) -> tuple[str, dict[str, Any]]:
        """
        [HELPER / UTILITY FUNCTION]
        Constructs a dynamic SQL WHERE clause and parameter dictionary for employee filtering.

        Ensures that `count_employees()` and `list_employees()` apply identical filter logic,
        preventing discrepancies between the display count and the actual returned rows.

        Parameters:
        -----------
        search : str | None
            Search term matched against FirstName, LastName, Email, or EmployeeID.
        department_id : str | None
            Exact department ID filter (e.g. 'DEP-001').
        employment_status : str | None
            Filter by status ('Active', 'Terminated', 'On Leave').

        Returns:
        --------
        tuple[str, dict[str, Any]]
            (WHERE clause string, named parameters dict).
        """
        clauses: list[str] = []
        params: dict[str, Any] = {}

        if search:
            clauses.append(
                """(e.FirstName  LIKE :search
                OR e.LastName   LIKE :search
                OR e.Email      LIKE :search
                OR e.EmployeeID LIKE :search)"""
            )
            params["search"] = f"%{search.strip()}%"
        if department_id:
            clauses.append("e.DepartmentID = :department_id")
            params["department_id"] = department_id
        if employment_status:
            clauses.append("e.EmploymentStatus = :employment_status")
            params["employment_status"] = employment_status

        return (" AND ".join(clauses) if clauses else "1 = 1"), params

    def count_employees(
        self,
        search: str | None = None,
        department_id: str | None = None,
        employment_status: str | None = None,
    ) -> int:
        """
        [HELPER / UTILITY FUNCTION]
        Returns the total number of employees matching the specified criteria, ignoring row limits.

        Used in the UI header: e.g. "Showing 500 of 13,736 matching employees".
        """
        where, params = self._employee_predicate(
            search, department_id, employment_status
        )
        return int(
            self.fetch_value(
                f"""
                SELECT COUNT(*)
                FROM employees e
                JOIN departments d ON d.DepartmentID = e.DepartmentID
                WHERE {where}
                """,
                params,
                default=0,
            )
        )

    def list_employees(
        self,
        search: str | None = None,
        department_id: str | None = None,
        employment_status: str | None = None,
        limit: int = 500,
        recent_first: bool = False,
    ) -> pd.DataFrame:
        """
        [CORE / IMPORTANT FUNCTION]
        Fetches a filtered, paginated list of employees for the directory table and dropdown pickers.

        Critical Architecture Note (`recent_first`):
        -------------------------------------------
        By default, IDs are generated sequentially (e.g. seeded data EMP-0000001 to EMP-0015000).
        Newly onboarded employees receive IDs like EMP-0015001+.
        If ordering by ascending ID with a LIMIT of 500, newly onboarded employees would be
        invisible in dropdowns!
        Setting `recent_first=True` orders by `EmployeeID DESC`, guaranteeing that newly created
        employees immediately appear at the top of forms and pickers.

        Returns:
        --------
        pd.DataFrame
            DataFrame containing matching employee rows joined with department names.
        """
        where, params = self._employee_predicate(
            search, department_id, employment_status
        )
        order = (
            "ORDER BY e.EmployeeID DESC"
            if recent_first
            else "ORDER BY e.EmployeeID"
        )
        sql = f"""
            SELECT {SELECT_COLUMNS}
            FROM employees e
            JOIN departments d ON d.DepartmentID = e.DepartmentID
            WHERE {where}
            {order}
            LIMIT :limit
        """
        params["limit"] = int(limit)

        try:
            return self.fetch_frame(sql, params)
        except QueryError as exc:
            logger.error("list_employees failed: %s", exc)
            raise

    def get_employee(self, employee_id: str) -> Employee:
        """
        [CORE / IMPORTANT FUNCTION]
        Fetches a single employee by their EmployeeID and converts to an `Employee` entity.

        Raises:
        -------
        NotFoundError
            If no employee with the given ID exists.
        """
        sql = f"""
            SELECT {SELECT_COLUMNS}
            FROM employees e
            JOIN departments d ON d.DepartmentID = e.DepartmentID
            WHERE e.EmployeeID = :employee_id
        """
        row = self.fetch_one(sql, {"employee_id": employee_id})
        if row is None:
            raise NotFoundError(f"Employee '{employee_id}' does not exist.")
        return Employee.from_row(row)

    def find_by_email(self, email: str) -> Employee | None:
        """
        [HELPER / UTILITY FUNCTION]
        Looks up an employee by email address.
        Used to prevent duplicate email registrations during onboarding.
        """
        row = self.fetch_one(
            f"SELECT {SELECT_COLUMNS} FROM employees e "
            "JOIN departments d ON d.DepartmentID = e.DepartmentID "
            "WHERE e.Email = :email",
            {"email": email.strip().lower()},
        )
        return Employee.from_row(row) if row else None

    def next_employee_id(self) -> str:
        """
        [CORE / IMPORTANT FUNCTION]
        Generates the next sequential Employee identifier (format: EMP-0015001).

        Queries `SELECT MAX(CAST(SUBSTRING(EmployeeID, 5) AS UNSIGNED)) FROM employees`
        and increments by 1. Formatted as 7-digit zero-padded string.
        """
        value = self.fetch_value(
            "SELECT COALESCE(MAX(CAST(SUBSTRING(EmployeeID, 5) AS UNSIGNED)), 0) + 1 "
            "FROM employees",
            default=1,
        )
        return f"EMP-{int(value):07d}"

    def list_departments(self) -> list[dict]:
        """
        [HELPER / UTILITY FUNCTION]
        Retrieves all departments along with live total headcount and active headcount.
        Populates department selection dropdowns across the UI.
        """
        return self.fetch_dicts(
            """
            SELECT d.DepartmentID,
                   d.DepartmentName,
                   COUNT(e.EmployeeID) AS headcount,
                   SUM(e.EmploymentStatus = 'Active') AS active_count
            FROM departments d
            LEFT JOIN employees e ON e.DepartmentID = d.DepartmentID
            GROUP BY d.DepartmentID, d.DepartmentName
            ORDER BY d.DepartmentName
            """
        )

    def count_by_status(self) -> pd.DataFrame:
        """
        [HELPER / UTILITY FUNCTION]
        Returns headcount grouped by employment status (Active, Terminated, On Leave).
        """
        return self.fetch_frame(
            "SELECT EmploymentStatus, COUNT(*) AS headcount "
            "FROM employees GROUP BY EmploymentStatus"
        )

    def distinct_job_roles(self) -> list[str]:
        """
        [HELPER / UTILITY FUNCTION]
        Returns a sorted list of unique job titles currently present in the database.
        """
        rows = self.fetch_frame(
            "SELECT DISTINCT JobRole FROM employees ORDER BY JobRole"
        )
        return [r["JobRole"] for r in rows.to_dict("records")]

    def distinct_managers(self) -> list[dict]:
        """
        [HELPER / UTILITY FUNCTION]
        Returns active employees eligible to be assigned as managers in the onboarding form.
        Ordered by JobLevel descending.
        """
        return self.fetch_dicts(
            "SELECT e.EmployeeID, CONCAT(e.FirstName, ' ', e.LastName) AS name, "
            "       e.JobRole "
            "FROM employees e "
            "WHERE e.EmploymentStatus = 'Active' "
            "ORDER BY e.JobLevel DESC, name "
            "LIMIT 300"
        )

    # ------------------------------------------------------------------
    # Write Operations & Dual-Write Architecture
    # ------------------------------------------------------------------

    def add_employee(self, employee: Employee) -> dict:
        """
        [CORE / IMPORTANT FUNCTION]
        Creates a new employee in the OLTP database AND seeds their initial Dim_Employee
        version in the Data Warehouse.

        Dual-Write Atomic Architecture:
        ------------------------------
        1. Validates business invariants via `employee.validate()`.
        2. Checks for email uniqueness.
        3. Executes inside a single database transaction:
           a. `INSERT INTO employees (...)`
           b. `CALL sp_apply_employee_change(:employee_id, :hire_date, 0)`
        4. Verifies that exactly 1 current version exists in `dim_employee`.
        5. If any error occurs, rolls back both OLTP and DW writes completely.

        Returns:
        --------
        dict
            Dict containing `employee_id` and `dim_current` version count.
        """
        try:
            employee.validate()
        except ValidationError as exc:
            logger.warning("Rejected employee %s: %s", employee.employee_id, exc)
            raise

        if self.find_by_email(employee.email) is not None:
            raise DuplicateRecordError(f"Email '{employee.email}' is already in use.")

        sql = """
            INSERT INTO employees
                (EmployeeID, FirstName, LastName, Email, PhoneNumber, Gender,
                 HireDate, TerminationDate, EmploymentStatus, DepartmentID,
                 JobRole, JobLevel, MonthlyIncome, ManagerEmployeeID)
            VALUES
                (:employee_id, :first_name, :last_name, :email, :phone_number, :gender,
                 :hire_date, :termination_date, :employment_status, :department_id,
                 :job_role, :job_level, :monthly_income, :manager_employee_id)
        """
        params = {
            "employee_id": employee.employee_id,
            "first_name": employee.first_name,
            "last_name": employee.last_name,
            "email": employee.email.strip().lower(),
            "phone_number": employee.phone_number,
            "gender": employee.gender,
            "hire_date": employee.hire_date,
            "termination_date": employee.termination_date,
            "employment_status": employee.employment_status,
            "department_id": employee.department_id,
            "job_role": employee.job_role,
            "job_level": employee.job_level,
            "monthly_income": employee.monthly_income,
            "manager_employee_id": employee.manager_employee_id,
        }

        try:
            with self.db.transaction() as conn:
                conn.execute(text(sql), params)
                conn.execute(
                    text(
                        "CALL sp_apply_employee_change"
                        "(:employee_id, :change_date, :force)"
                    ),
                    {
                        "employee_id": employee.employee_id,
                        "change_date": employee.hire_date,
                        "force": 0,
                    },
                )
        except QueryError as exc:
            logger.error("add_employee failed for %s: %s", employee.employee_id, exc)
            raise
        except SQLAlchemyError as exc:
            logger.warning("Warehouse refused %s: %s", employee.employee_id, exc)
            raise translate_db_error(exc) from exc

        # Confirm that the warehouse registered the dimension version
        dim_current = int(
            self.fetch_value(
                "SELECT COUNT(*) FROM dim_employee "
                "WHERE EmployeeID = :employee_id AND IsCurrent = 1",
                {"employee_id": employee.employee_id},
            )
        )
        if dim_current != 1:
            raise QueryError(
                f"Employee '{employee.employee_id}' was created in the OLTP but "
                f"the warehouse has {dim_current} open Dim_Employee versions, "
                f"expected 1. The OLTP write has been rolled back."
            )

        logger.info(
            "Added employee %s (%s); Dim_Employee open versions=%d",
            employee.employee_id, employee.full_name, dim_current,
        )

        return {
            "employee_id": employee.employee_id,
            "dim_current": dim_current,
        }

    def update_employee(self, employee: Employee) -> int:
        """
        [CORE / IMPORTANT FUNCTION]
        Updates employee attributes in OLTP AND synchronizes the change to the Data Warehouse.

        Publishes changes to `dim_employee` via `sp_apply_employee_change` inside the same
        transaction so that updates (e.g. promotions, salary changes) are reflected in DW analytics.
        """
        self.get_employee(employee.employee_id)

        try:
            employee.validate()
        except ValidationError as exc:
            logger.warning("Rejected update for %s: %s", employee.employee_id, exc)
            raise

        sql = """
            UPDATE employees
               SET FirstName         = :first_name,
                   LastName          = :last_name,
                   Email             = :email,
                   PhoneNumber       = :phone_number,
                   Gender            = :gender,
                   HireDate          = :hire_date,
                   TerminationDate   = :termination_date,
                   EmploymentStatus  = :employment_status,
                   DepartmentID      = :department_id,
                   JobRole           = :job_role,
                   JobLevel          = :job_level,
                   MonthlyIncome     = :monthly_income,
                   ManagerEmployeeID = :manager_employee_id
             WHERE EmployeeID = :employee_id
        """
        params = {
            "employee_id": employee.employee_id,
            "first_name": employee.first_name,
            "last_name": employee.last_name,
            "email": employee.email.strip().lower(),
            "phone_number": employee.phone_number,
            "gender": employee.gender,
            "hire_date": employee.hire_date,
            "termination_date": employee.termination_date,
            "employment_status": employee.employment_status,
            "department_id": employee.department_id,
            "job_role": employee.job_role,
            "job_level": employee.job_level,
            "monthly_income": employee.monthly_income,
            "manager_employee_id": employee.manager_employee_id,
        }
        try:
            with self.db.transaction() as conn:
                affected = conn.execute(text(sql), params).rowcount
                conn.execute(
                    text(
                        "CALL sp_apply_employee_change"
                        "(:employee_id, :change_date, :force)"
                    ),
                    {
                        "employee_id": employee.employee_id,
                        "change_date": date.today(),
                        "force": 0,
                    },
                )
            logger.info(
                "Updated employee %s and published the change to Dim_Employee",
                employee.employee_id,
            )
            return affected
        except QueryError as exc:
            logger.error("update_employee failed for %s: %s", employee.employee_id, exc)
            raise
        except SQLAlchemyError as exc:
            logger.warning("Warehouse refused the update of %s: %s",
                           employee.employee_id, exc)
            raise translate_db_error(exc) from exc

    def delete_employee(self, employee_id: str) -> int:
        """
        [CORE / IMPORTANT FUNCTION]
        Performs a hard delete of an employee from BOTH OLTP (`employees`) and DW (`dim_employee`).

        Safety Check:
        -------------
        Enforces referential integrity by checking `dependency_count(employee_id) == 0`.
        Rejects deletion if the employee has existing performance reviews, project assignments,
        or serves as manager to active subordinates.
        """
        if not self.dependency_count(employee_id) == 0:
            raise ValidationError(
                f"Employee '{employee_id}' still has reviews or assignments."
            )
        try:
            with self.db.transaction() as conn:
                affected = conn.execute(
                    text("DELETE FROM employees WHERE EmployeeID = :employee_id"),
                    {"employee_id": employee_id},
                ).rowcount
                conn.execute(
                    text("DELETE FROM dim_employee WHERE EmployeeID = :employee_id"),
                    {"employee_id": employee_id},
                )
            logger.info("Deleted employee %s and their Dim_Employee versions",
                        employee_id)
            return affected
        except QueryError as exc:
            logger.error("delete_employee failed for %s: %s", employee_id, exc)
            raise

    # ------------------------------------------------------------------
    # SCD Type 2 Department Transfer Implementation
    # ------------------------------------------------------------------

    def change_department(
        self,
        employee_id: str,
        department_id: str,
        change_date: date | None = None,
    ) -> dict:
        """
        [CORE / IMPORTANT FUNCTION]
        Executes a Slowly Changing Dimension Type 2 (SCD2) department transfer.

        How SCD Type 2 Works Here:
        --------------------------
        1. Updates `employees.DepartmentID = :department_id` in OLTP.
        2. Calls `CALL sp_apply_employee_change(:employee_id, :change_date, 0)`:
           - In `dim_employee`, the current record (`IsCurrent = 1`) has its
             `EffectiveEndDate` set to `change_date - 1 DAY`, and `IsCurrent` set to 0.
           - A new record is inserted into `dim_employee` with `EffectiveStartDate = change_date`,
             `EffectiveEndDate = '9999-12-31'`, `IsCurrent = 1`, and the new DepartmentID.
        3. All steps share a single atomic transaction.
        4. If `change_date` is today and a record already exists with today's start date,
           `sp_apply_employee_change` safely updates that record in place rather than creating
           overlapping dates.

        Parameters:
        -----------
        employee_id : str
            ID of the employee being transferred.
        department_id : str
            Destination department ID.
        change_date : date | None
            Effective date of transfer (defaults to today).

        Returns:
        --------
        dict
            Transfer details including previous department, target department, and new version count.
        """
        effective = change_date or date.today()

        employee = self.get_employee(employee_id)
        if employee.department_id == department_id:
            raise ValidationError(
                f"{employee.full_name} is already in {employee.department_name or department_id}."
            )

        department = self.fetch_one(
            "SELECT DepartmentID, DepartmentName FROM departments "
            "WHERE DepartmentID = :department_id",
            {"department_id": department_id},
        )
        if department is None:
            raise NotFoundError(f"Department '{department_id}' does not exist.")

        previous = {
            "department_id": employee.department_id,
            "department_name": employee.department_name,
        }
        target = {
            "department_id": department["DepartmentID"],
            "department_name": department["DepartmentName"],
        }

        try:
            with self.db.transaction() as conn:
                conn.execute(
                    text(
                        "UPDATE employees SET DepartmentID = :department_id "
                        "WHERE EmployeeID = :employee_id"
                    ),
                    {"department_id": department_id, "employee_id": employee_id},
                )
                conn.execute(
                    text(
                        "CALL sp_apply_employee_change"
                        "(:employee_id, :change_date, :force)"
                    ),
                    {"employee_id": employee_id, "change_date": effective,
                     "force": 0},
                )
        except QueryError as exc:
            logger.error("change_department failed for %s: %s", employee_id, exc)
            raise
        except SQLAlchemyError as exc:
            logger.warning("Warehouse refused the transfer of %s: %s",
                           employee_id, exc)
            raise translate_db_error(exc) from exc

        logger.info(
            "SCD2 applied: %s moved %s -> %s on %s",
            employee_id, previous["department_id"], department_id, effective,
        )

        return {
            "employee_id": employee_id,
            "employee_name": employee.full_name,
            "from_department": previous,
            "to_department": target,
            "effective_date": effective,
            "scd2_version": self.version_count(employee_id),
        }

    def version_count(self, employee_id: str) -> int:
        """
        [HELPER / UTILITY FUNCTION]
        Counts the total number of SCD Type 2 historical records for an employee in `dim_employee`.
        """
        return int(
            self.fetch_value(
                "SELECT COUNT(*) FROM dim_employee WHERE EmployeeID = :employee_id",
                {"employee_id": employee_id},
                default=0,
            )
        )

    def employee_history(self, employee_id: str) -> pd.DataFrame:
        """
        [CORE / IMPORTANT FUNCTION]
        Fetches the complete SCD Type 2 historical timeline for an employee from `dim_employee`.

        Displays every version, start/end dates, past departments, past job levels, and salaries.
        """
        return self.fetch_frame(
            """
            SELECT EmployeeSK, DepartmentName, JobRole, JobLevel, MonthlyIncome,
                   EffectiveStartDate, EffectiveEndDate, IsCurrent,
                   YearsOfService
            FROM dim_employee
            WHERE EmployeeID = :employee_id
            ORDER BY EffectiveStartDate
            """,
            {"employee_id": employee_id},
        )

    def dependency_count(self, employee_id: str) -> int:
        """
        [HELPER / UTILITY FUNCTION]
        Counts total references across performance reviews, project assignments, and direct reports.
        Used to safeguard foreign-key integrity before deletion.
        """
        return int(
            self.fetch_value(
                """
                SELECT
                    (SELECT COUNT(*) FROM performance_reviews WHERE EmployeeID = :eid) +
                    (SELECT COUNT(*) FROM employee_project_assignments WHERE EmployeeID = :eid) +
                    (SELECT COUNT(*) FROM employees WHERE ManagerEmployeeID = :eid)
                """,
                {"eid": employee_id},
                default=0,
            )
        )


__all__ = ["EmployeeManager"]