"""
Project & Assignment Data Access Layer (DAL)
============================================
READING SEQUENCE STEP 5b: Understand project lifecycle and employee allocation constraints.

Manages data operations for `projects` and `employee_project_assignments` in OLTP.
Provides allocation capacity guards to ensure no employee is booked beyond 100% capacity.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

import pandas as pd

from ..db_manager import BaseDataManager
from ..exceptions import (
    DuplicateRecordError,
    NotFoundError,
    QueryError,
    ValidationError,
)
from ..models.project import Project
from ..validation import validate_number, validate_role

# Configure module logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger(__name__)


# ============================================================================
# [PROJECT DATA MANAGER]
# ============================================================================

class ProjectManager(BaseDataManager):
    """
    [CORE DATA ACCESS LAYER CLASS]
    Encapsulates all database operations for projects and employee project assignments.
    """

    # ------------------------------------------------------------------
    # Project Queries
    # ------------------------------------------------------------------

    def list_projects(
        self,
        search: str | None = None,
        project_status: str | None = None,
        project_type: str | None = None,
        limit: int = 500,
    ) -> pd.DataFrame:
        """
        [CORE / IMPORTANT FUNCTION]
        Fetches filtered projects joined with project manager names and active assignment counts.

        Parameters:
        -----------
        search : str | None
            Search string matching project name or project ID.
        project_status : str | None
            Status filter ('Planned', 'In Progress', 'Completed', etc.).
        project_type : str | None
            Type filter ('Data Analytics', 'Cloud Migration', etc.).
        limit : int
            Result row limit.

        Returns:
        --------
        pd.DataFrame
            Tabular project dataset.
        """
        sql = """
            SELECT p.ProjectID, p.ProjectName, p.ProjectType, p.ProjectStartDate,
                   p.ProjectEndDate, p.ProjectStatus, p.ProjectManagerEmployeeID,
                   CONCAT(e.FirstName, ' ', e.LastName) AS ProjectManagerName,
                   (SELECT COUNT(*) FROM employee_project_assignments a
                     WHERE a.ProjectID = p.ProjectID) AS assignment_count
            FROM projects p
            LEFT JOIN employees e ON e.EmployeeID = p.ProjectManagerEmployeeID
            WHERE 1 = 1
        """
        params: dict[str, Any] = {}
        if search:
            sql += " AND (p.ProjectName LIKE :search OR p.ProjectID LIKE :search)"
            params["search"] = f"%{search.strip()}%"
        if project_status:
            sql += " AND p.ProjectStatus = :project_status"
            params["project_status"] = project_status
        if project_type:
            sql += " AND p.ProjectType = :project_type"
            params["project_type"] = project_type
        sql += " ORDER BY p.ProjectID LIMIT :limit"
        params["limit"] = int(limit)

        try:
            return self.fetch_frame(sql, params)
        except QueryError as exc:
            logger.error("list_projects failed: %s", exc)
            raise

    def get_project(self, project_id: str) -> Project:
        """
        [CORE / IMPORTANT FUNCTION]
        Fetches a single project by ProjectID and converts it into a `Project` domain entity.

        Raises:
        -------
        NotFoundError
            If the project ID is not found in the database.
        """
        row = self.fetch_one(
            """
            SELECT p.ProjectID, p.ProjectName, p.ProjectType, p.ProjectStartDate,
                   p.ProjectEndDate, p.ProjectStatus, p.ProjectManagerEmployeeID,
                   CONCAT(e.FirstName, ' ', e.LastName) AS ProjectManagerName
            FROM projects p
            LEFT JOIN employees e ON e.EmployeeID = p.ProjectManagerEmployeeID
            WHERE p.ProjectID = :project_id
            """,
            {"project_id": project_id},
        )
        if row is None:
            raise NotFoundError(f"Project '{project_id}' does not exist.")
        return Project.from_row(row)

    def next_project_id(self) -> str:
        """
        [CORE / IMPORTANT FUNCTION]
        Generates the next sequential Project identifier (format: PROJ-00001).
        """
        value = self.fetch_value(
            "SELECT COALESCE(MAX(CAST(SUBSTRING(ProjectID, 6) AS UNSIGNED)), 0) + 1 "
            "FROM projects",
            default=1,
        )
        return f"PROJ-{int(value):05d}"

    def distinct_types(self) -> list[str]:
        """
        [HELPER / UTILITY FUNCTION]
        Returns distinct project types for dropdown filtering in the UI.
        """
        rows = self.fetch_frame(
            "SELECT DISTINCT ProjectType FROM projects "
            "WHERE ProjectType IS NOT NULL ORDER BY ProjectType"
        )
        return [r["ProjectType"] for r in rows.to_dict("records")]

    def status_counts(self) -> pd.DataFrame:
        """
        [HELPER / UTILITY FUNCTION]
        Returns count of projects grouped by project status.
        """
        return self.fetch_frame(
            "SELECT ProjectStatus, COUNT(*) AS projects "
            "FROM projects GROUP BY ProjectStatus ORDER BY ProjectStatus"
        )

    # ------------------------------------------------------------------
    # Project Write Operations
    # ------------------------------------------------------------------

    def add_project(self, project: Project) -> int:
        """
        [CORE / IMPORTANT FUNCTION]
        Inserts a new project into the OLTP `projects` table after validating business rules.
        """
        try:
            project.validate()
        except ValidationError as exc:
            logger.warning("Rejected project %s: %s", project.project_id, exc)
            raise

        sql = """
            INSERT INTO projects
                (ProjectID, ProjectName, ProjectType, ProjectStartDate,
                 ProjectEndDate, ProjectStatus, ProjectManagerEmployeeID)
            VALUES
                (:project_id, :project_name, :project_type, :project_start_date,
                 :project_end_date, :project_status, :project_manager_employee_id)
        """
        params = {
            "project_id": project.project_id,
            "project_name": project.project_name,
            "project_type": project.project_type,
            "project_start_date": project.project_start_date,
            "project_end_date": project.project_end_date,
            "project_status": project.project_status,
            "project_manager_employee_id": project.project_manager_employee_id,
        }
        try:
            self.run(sql, params)
            logger.info("Added project %s (%s)", project.project_id, project.project_name)
            return 1
        except QueryError as exc:
            logger.error("add_project failed for %s: %s", project.project_id, exc)
            raise

    def update_project_status(self, project_id: str, project_status: str) -> int:
        """
        [CORE / IMPORTANT FUNCTION]
        Updates the lifecycle status of an existing project (e.g. 'In Progress' -> 'Completed').
        """
        try:
            return self.run(
                "UPDATE projects SET ProjectStatus = :project_status "
                "WHERE ProjectID = :project_id",
                {"project_status": project_status, "project_id": project_id},
            )
        except QueryError as exc:
            logger.error("update_project_status failed for %s: %s", project_id, exc)
            raise

    def delete_project(self, project_id: str) -> int:
        """
        [CORE / IMPORTANT FUNCTION]
        Deletes a project from the OLTP database after ensuring no active allocations exist.
        """
        if self.assignment_count(project_id) > 0:
            raise ValidationError(
                f"Project '{project_id}' still has employee assignments."
            )
        try:
            return self.run(
                "DELETE FROM projects WHERE ProjectID = :project_id",
                {"project_id": project_id},
            )
        except QueryError as exc:
            logger.error("delete_project failed for %s: %s", project_id, exc)
            raise

    # ------------------------------------------------------------------
    # Project Assignments & Workload Capacity Guards
    # ------------------------------------------------------------------

    def list_assignments(
        self,
        project_id: str | None = None,
        employee_id: str | None = None,
        assignment_status: str | None = None,
        limit: int = 500,
    ) -> pd.DataFrame:
        """
        [CORE / IMPORTANT FUNCTION]
        Lists employee project allocations joined with employee, department, and project details.
        """
        sql = """
            SELECT a.AssignmentID, a.EmployeeID,
                   CONCAT(e.FirstName, ' ', e.LastName) AS employee_name,
                   d.DepartmentName,
                   a.ProjectID, p.ProjectName, a.AssignmentStartDate,
                   a.AssignmentEndDate, a.AssignmentRole,
                   a.AllocationPercentage, a.AssignmentStatus
            FROM employee_project_assignments a
            JOIN employees   e ON e.EmployeeID   = a.EmployeeID
            JOIN departments d ON d.DepartmentID = e.DepartmentID
            JOIN projects    p ON p.ProjectID    = a.ProjectID
            WHERE 1 = 1
        """
        params: dict[str, Any] = {}
        if project_id:
            sql += " AND a.ProjectID = :project_id"
            params["project_id"] = project_id
        if employee_id:
            sql += " AND a.EmployeeID = :employee_id"
            params["employee_id"] = employee_id
        if assignment_status:
            sql += " AND a.AssignmentStatus = :assignment_status"
            params["assignment_status"] = assignment_status
        sql += " ORDER BY a.AssignmentID LIMIT :limit"
        params["limit"] = int(limit)

        try:
            return self.fetch_frame(sql, params)
        except QueryError as exc:
            logger.error("list_assignments failed: %s", exc)
            raise

    def find_assignment(
        self,
        employee_id: str,
        project_id: str,
        start_date: date | None = None,
    ) -> dict[str, Any] | None:
        """
        [HELPER / UTILITY FUNCTION]
        Checks for an existing assignment record of an employee to a project.
        Prevents duplicate assignment crashes against unique key `uq_assignments_emp_proj`.
        """
        sql = """
            SELECT AssignmentID, EmployeeID, ProjectID, AssignmentStartDate,
                   AssignmentEndDate, AssignmentRole, AllocationPercentage,
                   AssignmentStatus
            FROM employee_project_assignments
            WHERE EmployeeID = :employee_id
              AND ProjectID  = :project_id
        """
        params: dict[str, Any] = {
            "employee_id": employee_id,
            "project_id": project_id,
        }
        if start_date is not None:
            sql += " AND AssignmentStartDate = :start_date"
            params["start_date"] = start_date
        sql += " ORDER BY AssignmentStartDate, AssignmentID LIMIT 1"

        return self.fetch_one(sql, params)

    def add_assignment(
        self,
        employee_id: str,
        project_id: str,
        assignment_role: str,
        allocation_percentage: float,
        start_date: date | None = None,
    ) -> int:
        """
        [CORE / IMPORTANT FUNCTION]
        Allocates an employee to a project with strict capacity and collision guards.

        Critical Business Rules Enforced:
        ---------------------------------
        1. Role Validation: Checks format and max length.
        2. Single Allocation Limit: 0.01% to 100.0%.
        3. Collision Guard: Ensures employee isn't already assigned to the same project on that date.
        4. Cumulative Capacity Guard:
           Computes sum of active allocations for this employee (`SUM(AllocationPercentage)`).
           If (current_total + allocation > 100%), rejects the booking with ValidationError.
           Ensures no employee is overbooked past 100% bandwidth.
        5. Atomic Insert: Inserts row with newly generated `ASG-XXXXXXX` identifier.

        Returns:
        --------
        int
            1 if booking succeeds.
        """
        role = validate_role(assignment_role, "Assignment role")

        allocation = validate_number(
            allocation_percentage,
            "Allocation %",
            minimum=0.01,
            maximum=100,
        )

        start = start_date or date.today()

        # Check for assignment clash on the same start date
        clash = self.find_assignment(employee_id, project_id, start)
        if clash is not None:
            raise DuplicateRecordError(
                f"{employee_id} is already assigned to {project_id} from "
                f"{start:%d %b %Y} ({clash['AssignmentID']}, "
                f"{clash['AssignmentStatus']}, started "
                f"{clash['AssignmentStartDate']:%d %b %Y}). Choose another "
                "project, pick another start date, or end that assignment "
                "first."
            )

        # Enforce cumulative employee capacity ceiling (<= 100%)
        current_total = self.fetch_value(
            """
            SELECT COALESCE(SUM(AllocationPercentage), 0)
            FROM employee_project_assignments
            WHERE EmployeeID = :employee_id
              AND AssignmentStatus = 'Active'
              AND (AssignmentEndDate IS NULL OR AssignmentEndDate >= :start)
            """,
            {"employee_id": employee_id, "start": start},
            default=0,
        )
        if float(current_total or 0) + allocation > 100:
            raise ValidationError(
                f"{employee_id} is already allocated "
                f"{float(current_total or 0):.0f}%; this booking would exceed 100%."
            )

        # Generate next sequential AssignmentID
        next_id = self.fetch_value(
            "SELECT COALESCE(MAX(CAST(SUBSTRING(AssignmentID, 5) AS UNSIGNED)), 0) + 1 "
            "FROM employee_project_assignments",
            default=1,
        )
        assignment_id = f"ASG-{int(next_id):07d}"

        sql = """
            INSERT INTO employee_project_assignments
                (AssignmentID, EmployeeID, ProjectID, AssignmentStartDate,
                 AssignmentEndDate, AssignmentRole, AllocationPercentage,
                 AssignmentStatus)
            VALUES
                (:assignment_id, :employee_id, :project_id, :start_date,
                 NULL, :assignment_role, :allocation, 'Active')
        """
        params = {
            "assignment_id": assignment_id,
            "employee_id": employee_id,
            "project_id": project_id,
            "start_date": start,
            "assignment_role": role,
            "allocation": allocation,
        }
        try:
            self.run(sql, params)
            logger.info(
                "Assigned %s to %s at %.0f%%", employee_id, project_id, allocation
            )
            return 1
        except QueryError as exc:
            logger.error("add_assignment failed for %s/%s: %s",
                         employee_id, project_id, exc)
            raise

    def end_assignment(self, assignment_id: str, end_date: date | None = None) -> int:
        """
        [CORE / IMPORTANT FUNCTION]
        Concludes a project assignment, setting status to 'Completed' and recording end date.
        Frees up employee capacity for new projects.
        """
        try:
            return self.run(
                """
                UPDATE employee_project_assignments
                   SET AssignmentStatus  = 'Completed',
                       AssignmentEndDate = :end_date
                 WHERE AssignmentID = :assignment_id
                """,
                {"end_date": end_date or date.today(), "assignment_id": assignment_id},
            )
        except QueryError as exc:
            logger.error("end_assignment failed for %s: %s", assignment_id, exc)
            raise

    def assignment_count(self, project_id: str) -> int:
        """
        [HELPER / UTILITY FUNCTION]
        Counts the total number of employee assignments linked to a project.
        """
        return int(
            self.fetch_value(
                "SELECT COUNT(*) FROM employee_project_assignments "
                "WHERE ProjectID = :project_id",
                {"project_id": project_id},
                default=0,
            )
        )

    def current_allocation(self, employee_id: str) -> float:
        """
        [HELPER / UTILITY FUNCTION]
        Calculates total active percentage allocation currently held by an employee.
        """
        value = self.fetch_value(
            """
            SELECT COALESCE(SUM(AllocationPercentage), 0)
            FROM employee_project_assignments
            WHERE EmployeeID = :employee_id AND AssignmentStatus = 'Active'
            """,
            {"employee_id": employee_id},
            default=0,
        )
        return float(value or 0)


__all__ = ["ProjectManager"]