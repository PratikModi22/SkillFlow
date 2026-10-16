"""
Performance Review Data Access Layer (DAL)
==========================================
READING SEQUENCE STEP 5c: Understand employee performance appraisal persistence.

Manages CRUD operations for the OLTP `performance_reviews` table.
Applies business validation rules (such as enforcing a limit of one review per
employee per calendar day) and persists records to the operational database.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

import pandas as pd

from ..db_manager import BaseDataManager
from ..exceptions import NotFoundError, QueryError, ValidationError
from ..models.review import Review

# Configure module logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger(__name__)


# ============================================================================
# [REVIEW DATA MANAGER]
# ============================================================================

class ReviewManager(BaseDataManager):
    """
    [CORE DATA ACCESS LAYER CLASS]
    Encapsulates all database operations for employee performance reviews.
    """

    # ------------------------------------------------------------------
    # Read Operations
    # ------------------------------------------------------------------

    def list_reviews(
        self,
        employee_id: str | None = None,
        department_id: str | None = None,
        min_rating: float | None = None,
        limit: int = 500,
    ) -> pd.DataFrame:
        """
        [CORE / IMPORTANT FUNCTION]
        Fetches performance reviews joined with employee names and department names.

        Parameters:
        -----------
        employee_id : str | None
            Filter by specific employee.
        department_id : str | None
            Filter by employee department.
        min_rating : float | None
            Threshold filter (e.g. only ratings >= 4.0).
        limit : int
            Row cap.

        Returns:
        --------
        pd.DataFrame
            DataFrame of appraisal records.
        """
        sql = """
            SELECT r.ReviewID, r.EmployeeID,
                   CONCAT(e.FirstName, ' ', e.LastName) AS employee_name,
                   d.DepartmentName, r.ReviewDate, r.Rating, r.PerformanceScore,
                   r.PromotionRecommendation, r.ReviewerEmployeeID
            FROM performance_reviews r
            JOIN employees   e ON e.EmployeeID   = r.EmployeeID
            JOIN departments d ON d.DepartmentID = e.DepartmentID
            WHERE 1 = 1
        """
        params: dict[str, Any] = {}
        if employee_id:
            sql += " AND r.EmployeeID = :employee_id"
            params["employee_id"] = employee_id
        if department_id:
            sql += " AND e.DepartmentID = :department_id"
            params["department_id"] = department_id
        if min_rating is not None:
            sql += " AND r.Rating >= :min_rating"
            params["min_rating"] = float(min_rating)
        sql += " ORDER BY r.ReviewDate DESC, r.ReviewID LIMIT :limit"
        params["limit"] = int(limit)

        try:
            return self.fetch_frame(sql, params)
        except QueryError as exc:
            logger.error("list_reviews failed: %s", exc)
            raise

    def get_review(self, review_id: str) -> Review:
        """
        [CORE / IMPORTANT FUNCTION]
        Fetches an individual review by ReviewID and instantiates a `Review` domain entity.

        Raises:
        -------
        NotFoundError
            If the review ID does not exist in MySQL.
        """
        row = self.fetch_one(
            """
            SELECT r.ReviewID, r.EmployeeID, r.ReviewDate, r.Rating,
                   r.PerformanceScore, r.PromotionRecommendation,
                   r.ReviewerEmployeeID, d.DepartmentName
            FROM performance_reviews r
            JOIN employees   e ON e.EmployeeID   = r.EmployeeID
            JOIN departments d ON d.DepartmentID = e.DepartmentID
            WHERE r.ReviewID = :review_id
            """,
            {"review_id": review_id},
        )
        if row is None:
            raise NotFoundError(f"Review '{review_id}' does not exist.")
        return Review.from_row(row)

    def next_review_id(self) -> str:
        """
        [CORE / IMPORTANT FUNCTION]
        Generates the next sequential Review identifier (format: REV-00000001).
        """
        value = self.fetch_value(
            "SELECT COALESCE(MAX(CAST(SUBSTRING(ReviewID, 5) AS UNSIGNED)), 0) + 1 "
            "FROM performance_reviews",
            default=1,
        )
        return f"REV-{int(value):08d}"

    def employee_review_history(self, employee_id: str) -> pd.DataFrame:
        """
        [CORE / IMPORTANT FUNCTION]
        Fetches the chronological appraisal history for a specific employee.
        """
        return self.fetch_frame(
            """
            SELECT ReviewID, ReviewDate, Rating, PerformanceScore,
                   PromotionRecommendation
            FROM performance_reviews
            WHERE EmployeeID = :employee_id
            ORDER BY ReviewDate
            """,
            {"employee_id": employee_id},
        )

    # ------------------------------------------------------------------
    # Write Operations
    # ------------------------------------------------------------------

    def add_review(self, review: Review) -> int:
        """
        [CORE / IMPORTANT FUNCTION]
        Validates and inserts a new appraisal record into the OLTP database.

        Business Invariant Enforced:
        ----------------------------
        Enforces uniqueness on `(EmployeeID, ReviewDate)`: An employee can have
        at most ONE performance review on any given calendar date.
        Rejects multiple reviews on the same day with a friendly ValidationError.
        """
        try:
            review.validate()
        except ValidationError as exc:
            logger.warning("Rejected review %s: %s", review.review_id, exc)
            raise

        # Check for date collision for this employee
        clash = self.fetch_value(
            "SELECT COUNT(*) FROM performance_reviews "
            "WHERE EmployeeID = :employee_id AND ReviewDate = :review_date",
            {"employee_id": review.employee_id, "review_date": review.review_date},
            default=0,
        )
        if clash:
            raise ValidationError(
                f"{review.employee_id} already has a review on {review.review_date}."
            )

        sql = """
            INSERT INTO performance_reviews
                (ReviewID, EmployeeID, ReviewDate, Rating, PerformanceScore,
                 PromotionRecommendation, ReviewerEmployeeID)
            VALUES
                (:review_id, :employee_id, :review_date, :rating,
                 :performance_score, :promotion_recommendation, :reviewer_employee_id)
        """
        params = {
            "review_id": review.review_id,
            "employee_id": review.employee_id,
            "review_date": review.review_date,
            "rating": review.rating,
            "performance_score": review.performance_score,
            "promotion_recommendation": review.promotion_recommendation,
            "reviewer_employee_id": review.reviewer_employee_id,
        }
        try:
            self.run(sql, params)
            logger.info("Added review %s for %s", review.review_id, review.employee_id)
            return 1
        except QueryError as exc:
            logger.error("add_review failed for %s: %s", review.review_id, exc)
            raise

    def delete_review(self, review_id: str) -> int:
        """
        [CORE / IMPORTANT FUNCTION]
        Deletes a performance review from the database by ReviewID.
        """
        try:
            affected = self.run(
                "DELETE FROM performance_reviews WHERE ReviewID = :review_id",
                {"review_id": review_id},
            )
            logger.info("Deleted review %s", review_id)
            return affected
        except QueryError as exc:
            logger.error("delete_review failed for %s: %s", review_id, exc)
            raise

    def recent(self, days: int = 365, limit: int = 20) -> pd.DataFrame:
        """
        [HELPER / UTILITY FUNCTION]
        Fetches the most recent performance reviews submitted within the last N days.
        """
        return self.fetch_frame(
            """
            SELECT r.ReviewID, r.ReviewDate,
                   CONCAT(e.FirstName, ' ', e.LastName) AS employee_name,
                   d.DepartmentName, r.Rating, r.PerformanceScore,
                   r.PromotionRecommendation
            FROM performance_reviews r
            JOIN employees   e ON e.EmployeeID   = r.EmployeeID
            JOIN departments d ON d.DepartmentID = e.DepartmentID
            WHERE r.ReviewDate >= DATE_SUB(CURDATE(), INTERVAL :days DAY)
            ORDER BY r.ReviewDate DESC
            LIMIT :limit
            """,
            {"days": int(days), "limit": int(limit)},
        )

    @staticmethod
    def today() -> date:
        """
        [HELPER / UTILITY FUNCTION]
        Returns the current system date.
        """
        return date.today()


__all__ = ["ReviewManager"]