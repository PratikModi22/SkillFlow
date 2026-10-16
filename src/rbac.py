"""
SkillFlow: Role-Based Access Control (RBAC) Architecture
=========================================================
Defines roles, capabilities, and permission guards for the SkillFlow
Enterprise Workforce Intelligence & Data Warehouse platform.

Roles:
------
1. HR Administrator (Full Read / Write / DW Maintenance)
2. Department Manager (Operations / Reviews / Project Assignments)
3. Data Analyst / Auditor (Read-Only Analytics / Audit Trails)
4. Standard Employee (Self-Service Directory & Team Projects)
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Set


class UserRole(str, Enum):
    """Enumeration of system security roles."""
    HR_ADMIN = "👑 HR Administrator"
    DEPT_MANAGER = "👥 Department Manager"
    DATA_ANALYST = "📊 Data Analyst (Auditor)"
    EMPLOYEE = "👤 Employee (Self-Service)"


class Permission(str, Enum):
    """Granular system permissions."""
    VIEW_DASHBOARD = "view_dashboard"
    VIEW_EMPLOYEE_DIRECTORY = "view_employee_directory"
    ONBOARD_EMPLOYEE = "onboard_employee"
    TRANSFER_EMPLOYEE_SCD2 = "transfer_employee_scd2"
    VIEW_PROJECTS = "view_projects"
    CREATE_PROJECT = "create_project"
    ASSIGN_EMPLOYEE = "assign_employee"
    VIEW_REVIEWS = "view_reviews"
    SUBMIT_REVIEW = "submit_review"
    VIEW_WAREHOUSE = "view_warehouse"
    TRIGGER_DW_SYNC = "trigger_dw_sync"


# Role-to-Permissions Mapping
ROLE_PERMISSIONS: dict[UserRole, Set[Permission]] = {
    UserRole.HR_ADMIN: {
        Permission.VIEW_DASHBOARD,
        Permission.VIEW_EMPLOYEE_DIRECTORY,
        Permission.ONBOARD_EMPLOYEE,
        Permission.TRANSFER_EMPLOYEE_SCD2,
        Permission.VIEW_PROJECTS,
        Permission.CREATE_PROJECT,
        Permission.ASSIGN_EMPLOYEE,
        Permission.VIEW_REVIEWS,
        Permission.SUBMIT_REVIEW,
        Permission.VIEW_WAREHOUSE,
        Permission.TRIGGER_DW_SYNC,
    },
    UserRole.DEPT_MANAGER: {
        Permission.VIEW_DASHBOARD,
        Permission.VIEW_EMPLOYEE_DIRECTORY,
        Permission.TRANSFER_EMPLOYEE_SCD2,
        Permission.VIEW_PROJECTS,
        Permission.ASSIGN_EMPLOYEE,
        Permission.VIEW_REVIEWS,
        Permission.SUBMIT_REVIEW,
    },
    UserRole.DATA_ANALYST: {
        Permission.VIEW_DASHBOARD,
        Permission.VIEW_EMPLOYEE_DIRECTORY,
        Permission.VIEW_PROJECTS,
        Permission.VIEW_REVIEWS,
        Permission.VIEW_WAREHOUSE,
    },
    UserRole.EMPLOYEE: {
        Permission.VIEW_EMPLOYEE_DIRECTORY,
        Permission.VIEW_PROJECTS,
    },
}

# Navigation pages accessible by role
ROLE_PAGES: dict[UserRole, list[str]] = {
    UserRole.HR_ADMIN: ["Dashboard", "Employees", "Projects", "Reviews", "Warehouse"],
    UserRole.DEPT_MANAGER: ["Dashboard", "Employees", "Projects", "Reviews"],
    UserRole.DATA_ANALYST: ["Dashboard", "Employees", "Projects", "Reviews", "Warehouse"],
    UserRole.EMPLOYEE: ["Employees", "Projects"],
}

# Role metadata for display
ROLE_METADATA = {
    UserRole.HR_ADMIN: {
        "title": "HR Administrator",
        "badge": "Full Access (Admin)",
        "badge_color": "#8338ec",
        "description": "Unrestricted administrative access to all analytics, employee onboarding, SCD2 transfers, appraisals, and warehouse ETL sync.",
        "can_write": True,
    },
    UserRole.DEPT_MANAGER: {
        "title": "Department Manager",
        "badge": "Operations / Staffing",
        "badge_color": "#3a86ff",
        "description": "Operational access to team allocations, staff reviews, and department transfers. Restricted from DW ETL sync and system administration.",
        "can_write": True,
    },
    UserRole.DATA_ANALYST: {
        "title": "Data Analyst / Auditor",
        "badge": "Read-Only Audit Mode",
        "badge_color": "#fb5607",
        "description": "Read-only access to executive dashboards, historical reviews, and warehouse logs. Write operations are disabled for audit compliance.",
        "can_write": False,
    },
    UserRole.EMPLOYEE: {
        "title": "Standard Employee",
        "badge": "Self-Service",
        "badge_color": "#2a9d8f",
        "description": "Limited self-service access to employee directory and project rosters. Executive metrics and data warehouse internals are hidden.",
        "can_write": False,
    },
}


class RBAC:
    """Helper methods for evaluating permissions."""

    @staticmethod
    def has_permission(role: UserRole | str, permission: Permission) -> bool:
        """Check if a given role possesses a specific permission."""
        if isinstance(role, str):
            try:
                role = UserRole(role)
            except ValueError:
                return False
        return permission in ROLE_PERMISSIONS.get(role, set())

    @staticmethod
    def get_allowed_pages(role: UserRole | str) -> list[str]:
        """Return the list of permitted navigation pages for the role."""
        if isinstance(role, str):
            try:
                role = UserRole(role)
            except ValueError:
                return ["Employees"]
        return ROLE_PAGES.get(role, ["Employees"])

    @staticmethod
    def get_role_info(role: UserRole | str) -> dict:
        """Get display metadata for the specified role."""
        if isinstance(role, str):
            try:
                role = UserRole(role)
            except ValueError:
                role = UserRole.EMPLOYEE
        return ROLE_METADATA.get(role, ROLE_METADATA[UserRole.EMPLOYEE])
