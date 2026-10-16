"""
Project Domain Entity Model
===========================
READING SEQUENCE STEP 4b: Understand project entities and workload rules.

Encapsulates an enterprise project and its allocation/resource-load rules.
Persisted into the OLTP `projects` table via `src/dal/project_dal.py`.

Design Highlights:
------------------
- Dataclass mapping directly to OLTP `projects` table.
- Analytical fields (`people_assigned`, `total_allocation_pct`, `average_score`)
  populated dynamically when evaluating project bottlenecks.
- Workload metrics (`is_overallocated`, `average_allocation`) indicating project health.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Any, Mapping

from ..exceptions import ValidationError
from ..validation import validate_text

# Allowable project lifecycle statuses
PROJECT_STATUSES = ("Planned", "In Progress", "Completed", "On Hold", "Cancelled")

# Recognized project categories
PROJECT_TYPES = (
    "Data Analytics",
    "Machine Learning",
    "Cloud Migration",
    "Enterprise Application",
    "Digital Transformation",
    "Data Governance",
    "Cybersecurity",
    "Process Automation",
)


# ============================================================================
# [PROJECT DOMAIN ENTITY]
# ============================================================================

@dataclass
class Project:
    """
    [CORE DOMAIN ENTITY CLASS]
    Represents an organizational project within the OLTP system.

    Tracks project schedule, status, assigned project manager, and team workload.
    """

    # Primary identifier (format: PROJ-00001)
    project_id: str
    project_name: str
    project_start_date: date
    project_type: str | None = None
    project_end_date: date | None = None
    project_status: str = "Planned"
    project_manager_employee_id: str | None = None

    # Analytical fields populated by warehouse views / queries (never persisted to OLTP)
    project_manager_name: str | None = field(default=None, repr=False)
    people_assigned: int | None = field(default=None, repr=False)
    total_allocation_pct: float | None = field(default=None, repr=False)
    average_score: float | None = field(default=None, repr=False)

    # ------------------------------------------------------------------
    # Construction & Serialization
    # ------------------------------------------------------------------

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "Project":
        """
        [CORE / IMPORTANT FUNCTION]
        Factory constructor that creates a Project instance from a database row dictionary.

        Parameters:
        -----------
        row : Mapping[str, Any]
            Raw dictionary returned from the SQL query.

        Returns:
        --------
        Project
            Populated Project domain entity.
        """
        return cls(
            project_id=str(row["ProjectID"]),
            project_name=row["ProjectName"],
            project_start_date=_as_date(row["ProjectStartDate"]),
            project_type=row.get("ProjectType"),
            project_end_date=_as_date(row.get("ProjectEndDate")),
            project_status=row.get("ProjectStatus") or "Planned",
            project_manager_employee_id=row.get("ProjectManagerEmployeeID"),
            project_manager_name=row.get("ProjectManagerName"),
            people_assigned=(
                int(row["people_assigned"]) if row.get("people_assigned") is not None else None
            ),
            total_allocation_pct=(
                float(row["total_allocation_pct"])
                if row.get("total_allocation_pct") is not None
                else None
            ),
            average_score=(
                float(row["avg_score"]) if row.get("avg_score") is not None else None
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        """
        [HELPER / UTILITY FUNCTION]
        Serializes the Project entity into a standard dictionary.
        """
        return asdict(self)

    # ------------------------------------------------------------------
    # Derived Properties & Health Metrics
    # ------------------------------------------------------------------

    @property
    def is_active(self) -> bool:
        """
        [HELPER / UTILITY FUNCTION]
        Returns True if the project is currently in the active "In Progress" execution state.
        """
        return self.project_status == "In Progress"

    @property
    def is_overallocated(self) -> bool:
        """
        [HELPER / UTILITY FUNCTION]
        Indicates if total project allocation exceeds a reasonable threshold (> 1000%).
        Flags potential bottleneck risks on executive dashboards.
        """
        if self.total_allocation_pct is None:
            return False
        return self.total_allocation_pct > 1000

    @property
    def duration_days(self) -> int | None:
        """
        [HELPER / UTILITY FUNCTION]
        Returns the duration of the project in days if end date is set; otherwise None.
        """
        if not self.project_end_date:
            return None
        return (self.project_end_date - self.project_start_date).days

    @property
    def average_allocation(self) -> float | None:
        """
        [HELPER / UTILITY FUNCTION]
        Returns the mean allocation percentage per assigned team member.
        """
        if not self.people_assigned or self.total_allocation_pct is None:
            return None
        return round(self.total_allocation_pct / self.people_assigned, 2)

    # ------------------------------------------------------------------
    # Business Invariants & Lifecycle
    # ------------------------------------------------------------------

    def validate(self) -> None:
        """
        [CORE / IMPORTANT FUNCTION]
        Validates business invariants for a Project before persisting to MySQL.

        Rules Enforced:
        ---------------
        1. ProjectID must be provided.
        2. ProjectName must not be empty and <= 255 characters.
        3. ProjectType (if present) must be <= 100 characters.
        4. ProjectStartDate is required.
        5. ProjectEndDate cannot precede ProjectStartDate.
        6. ProjectStatus must be one of PROJECT_STATUSES.

        Raises:
        -------
        ValidationError
            If any business rule is violated.
        """
        if not self.project_id or not self.project_id.strip():
            raise ValidationError("ProjectID is required.")

        validate_text(self.project_name, "Project name", max_length=255)
        if self.project_type:
            validate_text(self.project_type, "Project type", max_length=100)

        if self.project_start_date is None:
            raise ValidationError("ProjectStartDate is required.")

        if self.project_end_date and self.project_end_date < self.project_start_date:
            raise ValidationError("ProjectEndDate cannot precede ProjectStartDate.")

        if self.project_status not in PROJECT_STATUSES:
            raise ValidationError(
                f"ProjectStatus must be one of {PROJECT_STATUSES}, "
                f"got '{self.project_status}'."
            )

    def complete(self, on: date | None = None) -> bool:
        """
        [CORE / IMPORTANT FUNCTION]
        Transitions the project to 'Completed' status and records completion date.
        Returns True if the status changed.
        """
        if self.project_status == "Completed":
            return False
        self.project_status = "Completed"
        self.project_end_date = on or date.today()
        return True

    def __str__(self) -> str:
        """
        [HELPER / UTILITY FUNCTION]
        Returns a readable string representation of the project.
        """
        return f"{self.project_id} | {self.project_name} | {self.project_status}"


# ============================================================================
# [DATA COERCION HELPERS]
# ============================================================================

def _as_date(value: Any) -> date | None:
    """
    [HELPER / UTILITY FUNCTION]
    Coerces database date values (date, datetime, ISO string, NaT) into a `datetime.date`.
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


__all__ = ["Project", "PROJECT_STATUSES", "PROJECT_TYPES"]