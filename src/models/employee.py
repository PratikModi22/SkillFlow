"""
Employee Domain Entity Model
============================
READING SEQUENCE STEP 4a: Understand domain entities and business rules.

This module encapsulates a single employee record and its inherent business rules.
The class stays completely free of SQL; database persistence is delegated to
`src/dal/employee_dal.py`.

Design Highlights:
------------------
- Dataclass representation mapping directly to OLTP `employees` table schema.
- Built-in business rule validation (`validate()`) executed before any DB write.
- Derived properties (`tenure_years`, `annual_income`, `is_active`) computed on-the-fly.
- Mutation methods (`apply_department_change`, `apply_salary_change`, `terminate`)
  that enforce valid state transitions.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Any, Mapping

from ..exceptions import ValidationError
from ..validation import (
    validate_email,
    validate_name,
    validate_number,
    validate_phone,
    validate_role,
)

# Standard allowable employment statuses in OLTP
EMPLOYMENT_STATUSES = ("Active", "Terminated", "On Leave")


# ============================================================================
# [EMPLOYEE DOMAIN ENTITY]
# ============================================================================

@dataclass
class Employee:
    """
    [CORE DOMAIN ENTITY CLASS]
    Represents an individual employee record within the Enterprise OLTP system.

    Fields mirror columns from the OLTP `employees` table (`database/03_create_oltp.sql`),
    with additional transient fields for analytical enrichment.
    """

    # Primary identifier (format: EMP-0000001)
    employee_id: str
    first_name: str
    last_name: str
    email: str
    hire_date: date
    department_id: str
    job_role: str
    monthly_income: float = 0.0
    phone_number: str | None = None
    gender: str | None = None
    termination_date: date | None = None
    employment_status: str = "Active"
    job_level: int = 1
    manager_employee_id: str | None = None

    # Analytical fields populated only when joining with warehouse views; never persisted to OLTP.
    department_name: str | None = field(default=None, repr=False)
    average_score: float | None = field(default=None, repr=False)
    risk_score: float | None = field(default=None, repr=False)

    # ------------------------------------------------------------------
    # Construction & Serialization
    # ------------------------------------------------------------------

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "Employee":
        """
        [CORE / IMPORTANT FUNCTION]
        Factory constructor that builds an Employee entity from a database row dictionary.

        Safely handles SQL column casing, type coercion (strings, dates, floats),
        and NULL/None handling.

        Parameters:
        -----------
        row : Mapping[str, Any]
            Dictionary representing a database record fetched from MySQL.

        Returns:
        --------
        Employee
            A strongly typed Employee instance.
        """
        return cls(
            employee_id=str(row["EmployeeID"]),
            first_name=row["FirstName"],
            last_name=row["LastName"],
            email=row["Email"],
            hire_date=_as_date(row["HireDate"]),
            department_id=str(row["DepartmentID"]),
            job_role=row["JobRole"],
            monthly_income=float(row.get("MonthlyIncome") or 0),
            phone_number=row.get("PhoneNumber"),
            gender=row.get("Gender"),
            termination_date=_as_date(row.get("TerminationDate")),
            employment_status=row.get("EmploymentStatus") or "Active",
            job_level=int(row.get("JobLevel") or 1),
            manager_employee_id=row.get("ManagerEmployeeID"),
            department_name=row.get("DepartmentName"),
            average_score=(
                float(row["avg_score"]) if row.get("avg_score") is not None else None
            ),
            risk_score=(
                float(row["risk_score"]) if row.get("risk_score") is not None else None
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        """
        [HELPER / UTILITY FUNCTION]
        Serializes the Employee dataclass into a flat dictionary suitable for
        DataFrame construction or JSON serialization.
        """
        return asdict(self)

    # ------------------------------------------------------------------
    # Derived Properties
    # ------------------------------------------------------------------

    @property
    def full_name(self) -> str:
        """
        [HELPER / UTILITY FUNCTION]
        Returns the concatenated first and last name (e.g. "Aarti Sharma").
        """
        return f"{self.first_name} {self.last_name}".strip()

    @property
    def is_active(self) -> bool:
        """
        [HELPER / UTILITY FUNCTION]
        Returns True if the employee is currently active in the company.
        """
        return self.employment_status == "Active"

    @property
    def has_left(self) -> bool:
        """
        [HELPER / UTILITY FUNCTION]
        Returns True if the employee has a recorded termination date.
        """
        return self.termination_date is not None

    @property
    def tenure_years(self) -> float:
        """
        [HELPER / UTILITY FUNCTION]
        Calculates completed service years between hire date and today (or exit date).
        Uses standard 365.25 days per leap year accounting.
        """
        end = self.termination_date or date.today()
        days = (end - self.hire_date).days
        return round(max(days, 0) / 365.25, 2)

    @property
    def annual_income(self) -> float:
        """
        [HELPER / UTILITY FUNCTION]
        Calculates annual base salary (MonthlyIncome * 12).
        """
        return round(self.monthly_income * 12, 2)

    @property
    def initials(self) -> str:
        """
        [HELPER / UTILITY FUNCTION]
        Returns capitalized first letter of first name and last name (e.g. "AS").
        """
        return f"{self.first_name[:1]}{self.last_name[:1]}".upper()

    # ------------------------------------------------------------------
    # Business Invariants & Lifecycle Behavior
    # ------------------------------------------------------------------

    def validate(self) -> None:
        """
        [CORE / IMPORTANT FUNCTION]
        Enforces domain-level business invariants before writing or updating records.

        Validation Rules:
        -----------------
        1. EmployeeID must be non-empty.
        2. First and Last names must comply with Unicode name regex.
        3. HireDate is strictly required.
        4. Email must follow valid standard email format.
        5. Phone number, if provided, must be exactly 10 digits.
        6. JobRole must not be blank and fit within column size.
        7. JobLevel must be an integer between 1 and 10.
        8. MonthlyIncome must be >= 0 and within DECIMAL(12,2) capacity.
        9. TerminationDate must not precede HireDate.
        10. EmploymentStatus must be in ('Active', 'Terminated', 'On Leave').

        Raises:
        -------
        ValidationError
            If any business constraint is violated.
        """
        if not self.employee_id or not self.employee_id.strip():
            raise ValidationError("EmployeeID is required.")

        # Names: letters plus real-name punctuation, length bounded by VARCHAR(100)
        validate_name(self.first_name, "First name")
        validate_name(self.last_name, "Last name")

        if self.hire_date is None:
            raise ValidationError("HireDate is required.")

        # Address: full syntax check
        validate_email(self.email)

        # Phone: optional, but when present must be exactly 10 digits
        if self.phone_number:
            validate_phone(self.phone_number)

        if self.department_id not in (None, "") and not str(self.department_id).strip():
            raise ValidationError("DepartmentID cannot be blank.")

        # Role validation
        validate_role(self.job_role, "Job role")

        # Job level: 1 to 10
        try:
            level = int(self.job_level)
        except (TypeError, ValueError):
            raise ValidationError("JobLevel must be a whole number.") from None
        if str(level) != str(self.job_level).strip():
            raise ValidationError("JobLevel must be a whole number.")
        if not 1 <= level <= 10:
            raise ValidationError("JobLevel must be between 1 and 10.")

        # Monthly income bounds
        income = validate_number(self.monthly_income, "Monthly income", minimum=0)
        if income is not None and income > 9_999_999_999:
            raise ValidationError("MonthlyIncome is larger than the column allows.")

        # Termination sanity check
        if self.termination_date and self.termination_date < self.hire_date:
            raise ValidationError("TerminationDate cannot precede HireDate.")

        # Status check
        if self.employment_status not in EMPLOYMENT_STATUSES:
            raise ValidationError(
                f"EmploymentStatus must be one of {EMPLOYMENT_STATUSES}, "
                f"got '{self.employment_status}'."
            )

    def apply_department_change(
        self,
        department_id: str,
        department_name: str | None = None,
    ) -> bool:
        """
        [CORE / IMPORTANT FUNCTION]
        Applies a department transfer to the in-memory employee model.

        Returns True if the department actually changed, or False if identical.
        Note: The caller in `EmployeeManager` is responsible for triggering
        `sp_apply_employee_change` to create the SCD Type 2 dimension version.
        """
        if department_id == self.department_id:
            return False
        self.department_id = department_id
        if department_name is not None:
            self.department_name = department_name
        return True

    def apply_salary_change(self, new_income: float) -> bool:
        """
        [CORE / IMPORTANT FUNCTION]
        Updates the employee's monthly salary after ensuring it is non-negative.
        Returns True if the salary value changed.
        """
        new_income = float(new_income)
        if new_income < 0:
            raise ValidationError("MonthlyIncome cannot be negative.")
        if new_income == self.monthly_income:
            return False
        self.monthly_income = new_income
        return True

    def terminate(self, on: date | None = None) -> bool:
        """
        [CORE / IMPORTANT FUNCTION]
        Marks the employee as terminated and records the effective termination date.
        Returns True if termination was successfully recorded.
        """
        if self.has_left:
            return False
        self.termination_date = on or date.today()
        self.employment_status = "Terminated"
        return True

    def __str__(self) -> str:
        """
        [HELPER / UTILITY FUNCTION]
        Returns readable string representation for logging and debugging.
        """
        return f"{self.employee_id} | {self.full_name} | {self.job_role}"


# ============================================================================
# [DATA PARSING HELPERS]
# ============================================================================

def _as_date(value: Any) -> date | None:
    """
    [HELPER / UTILITY FUNCTION]
    Coerces various database date representations into a python `datetime.date`.

    Handles:
    --------
    - None or pandas NaT -> None
    - datetime.datetime  -> value.date()
    - datetime.date      -> value
    - ISO string         -> parsed date (YYYY-MM-DD)

    Raises:
    -------
    ValidationError
        If the value is non-empty and cannot be parsed.
    """
    if value is None:
        return None
    if type(value).__name__ == "NaTType":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        if not value.strip():
            return None
        return datetime.strptime(value[:10], "%Y-%m-%d").date()
    raise ValidationError(f"Cannot interpret '{value}' as a date.")


__all__ = ["Employee", "EMPLOYMENT_STATUSES"]