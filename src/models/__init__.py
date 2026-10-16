"""
Domain Entity Models Package
============================
READING SEQUENCE STEP 4 (Index): Domain Entities.

Re-exports the core business models and categorical constants so callers can use:
    from src.models import Employee, Project, Review

Models Summary:
---------------
- Employee : OLTP Employee entity, derived properties (tenure, salary), validation.
- Project  : OLTP Project entity, capacity overload detection, status tracking.
- Review   : OLTP Performance Review entity, promotion rules, rating bands.
"""

from .employee import EMPLOYMENT_STATUSES, Employee
from .project import PROJECT_STATUSES, PROJECT_TYPES, Project
from .review import (
    LOW_RATING_THRESHOLD,
    LOW_SCORE_THRESHOLD,
    MAX_RATING,
    MAX_SCORE,
    PROMOTION_RATING_THRESHOLD,
    PROMOTION_SCORE_THRESHOLD,
    Review,
)

__all__ = [
    "Employee",
    "Project",
    "Review",
    "EMPLOYMENT_STATUSES",
    "PROJECT_STATUSES",
    "PROJECT_TYPES",
    "MAX_RATING",
    "MAX_SCORE",
    "LOW_SCORE_THRESHOLD",
    "LOW_RATING_THRESHOLD",
    "PROMOTION_RATING_THRESHOLD",
    "PROMOTION_SCORE_THRESHOLD",
]