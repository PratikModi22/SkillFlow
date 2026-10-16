"""
Performance Review Domain Entity Model
======================================
READING SEQUENCE STEP 4c: Understand employee appraisal evaluations & promotion rules.

Encapsulates an employee performance review record plus the rating and promotion
classification logic used by the Streamlit forms, attrition risk model, and dashboards.

Persisted into the OLTP `performance_reviews` table via `src/dal/review_dal.py`.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Any, Mapping

from ..exceptions import ValidationError

# System rating limits
MAX_RATING = 5
MAX_SCORE = 100

# Promotion qualification criteria:
# An employee is considered promotable if Rating >= 4 AND Score >= 75
PROMOTION_RATING_THRESHOLD = 4
PROMOTION_SCORE_THRESHOLD = 75

# Low-performance / Attrition risk criteria:
# Score below 50 or Rating <= 2 marks an employee as at-risk.
LOW_SCORE_THRESHOLD = 50
LOW_RATING_THRESHOLD = 2


# ============================================================================
# [PERFORMANCE REVIEW DOMAIN ENTITY]
# ============================================================================

@dataclass
class Review:
    """
    [CORE DOMAIN ENTITY CLASS]
    Represents an individual performance appraisal record within the OLTP system.

    Tracks appraisal rating (1 to 5), numeric performance score (0 to 100),
    evaluator, and promotion recommendations.
    """

    # Primary identifier (format: REV-00000001)
    review_id: str
    employee_id: str
    review_date: date
    rating: float
    performance_score: float
    promotion_recommendation: str = "No"
    reviewer_employee_id: str | None = None

    # Analytical fields populated by analytical joins (never persisted to OLTP)
    department_name: str | None = field(default=None, repr=False)
    employee_name: str | None = field(default=None, repr=False)
    tenure_years: float | None = field(default=None, repr=False)

    # ------------------------------------------------------------------
    # Construction & Serialization
    # ------------------------------------------------------------------

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "Review":
        """
        [CORE / IMPORTANT FUNCTION]
        Factory constructor that builds a Review entity from a database row dictionary.

        Parameters:
        -----------
        row : Mapping[str, Any]
            Database row dictionary from `performance_reviews`.

        Returns:
        --------
        Review
            Strongly typed Review domain entity.
        """
        return cls(
            review_id=str(row["ReviewID"]),
            employee_id=str(row["EmployeeID"]),
            review_date=_as_date(row["ReviewDate"]),
            rating=float(row["Rating"]),
            performance_score=float(row["PerformanceScore"]),
            promotion_recommendation=row.get("PromotionRecommendation") or "No",
            reviewer_employee_id=row.get("ReviewerEmployeeID"),
            department_name=row.get("DepartmentName"),
            employee_name=row.get("employee_name"),
            tenure_years=(
                float(row["TenureYears"]) if row.get("TenureYears") is not None else None
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        """
        [HELPER / UTILITY FUNCTION]
        Serializes the Review entity into a standard Python dictionary.
        """
        return asdict(self)

    # ------------------------------------------------------------------
    # Derived Properties & Evaluation Logic
    # ------------------------------------------------------------------

    @property
    def is_promotion(self) -> bool:
        """
        [HELPER / UTILITY FUNCTION]
        Returns True if the review has an explicit promotion recommendation ('Yes').
        """
        return self.promotion_recommendation == "Yes"

    @property
    def is_low_performer(self) -> bool:
        """
        [HELPER / UTILITY FUNCTION]
        Identifies whether this review indicates performance distress.
        Used by the attrition risk model on the Executive Dashboard.
        """
        return (
            self.performance_score < LOW_SCORE_THRESHOLD
            or self.rating <= LOW_RATING_THRESHOLD
        )

    @property
    def quality_band(self) -> str:
        """
        [HELPER / UTILITY FUNCTION]
        Categorizes performance score into standard HR evaluation bands:
        - 80+: Excellent
        - 65-79: Good
        - 50-64: Satisfactory
        - <50: Needs Improvement
        """
        if self.performance_score >= 80:
            return "Excellent"
        if self.performance_score >= 65:
            return "Good"
        if self.performance_score >= 50:
            return "Satisfactory"
        return "Needs Improvement"

    def suggest_promotion(self) -> bool:
        """
        [CORE / IMPORTANT FUNCTION]
        Evaluates promotion criteria based on standardized company benchmarks:
        Requires Rating >= 4 AND Score >= 75.

        Provides real-time feedback in the Streamlit review submission form.
        """
        return (
            self.rating >= PROMOTION_RATING_THRESHOLD
            and self.performance_score >= PROMOTION_SCORE_THRESHOLD
        )

    # ------------------------------------------------------------------
    # Business Invariants & Validation
    # ------------------------------------------------------------------

    def validate(self) -> None:
        """
        [CORE / IMPORTANT FUNCTION]
        Enforces business rules before persisting a review into MySQL.

        Rules Enforced:
        ---------------
        1. ReviewID and EmployeeID must be present.
        2. ReviewDate must be valid.
        3. Rating must be between 1 and 5.
        4. PerformanceScore must be between 0 and 100.
        5. PromotionRecommendation must be strictly 'Yes' or 'No'.

        Raises:
        -------
        ValidationError
            If any appraisal constraint is violated.
        """
        if not self.review_id or not self.review_id.strip():
            raise ValidationError("ReviewID is required.")
        if not self.employee_id or not self.employee_id.strip():
            raise ValidationError("EmployeeID is required.")
        if self.review_date is None:
            raise ValidationError("ReviewDate is required.")
        if not 1 <= float(self.rating) <= MAX_RATING:
            raise ValidationError(f"Rating must be between 1 and {MAX_RATING}.")
        if not 0 <= float(self.performance_score) <= MAX_SCORE:
            raise ValidationError(f"PerformanceScore must be between 0 and {MAX_SCORE}.")
        if self.promotion_recommendation not in ("Yes", "No"):
            raise ValidationError(
                "PromotionRecommendation must be 'Yes' or 'No'."
            )

    def __str__(self) -> str:
        """
        [HELPER / UTILITY FUNCTION]
        Returns readable string representation for logging and review cards.
        """
        return (
            f"{self.review_id} | {self.employee_id} | "
            f"{self.review_date} | score={self.performance_score} "
            f"| rating={self.rating}"
        )


# ============================================================================
# [DATA PARSING HELPERS]
# ============================================================================

def _as_date(value: Any) -> date | None:
    """
    [HELPER / UTILITY FUNCTION]
    Coerces database date values into a native python `datetime.date`.
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


__all__ = [
    "Review",
    "MAX_RATING",
    "MAX_SCORE",
    "LOW_SCORE_THRESHOLD",
    "LOW_RATING_THRESHOLD",
    "PROMOTION_RATING_THRESHOLD",
    "PROMOTION_SCORE_THRESHOLD",
]