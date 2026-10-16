"""
SkillFlow: Role-Based Access Control (RBAC) & Authentication Module
===================================================================
Defines security roles, system permissions, user accounts, and authentication
guards for the SkillFlow Enterprise Workforce Intelligence Platform.

Demo Accounts:
--------------
- admin@skillflow.com     (admin123)    ->  HR Administrator (Full Access)
- manager@skillflow.com   (manager123)  ->  Department Manager (Operations)
- auditor@skillflow.com   (auditor123)  ->  Data Analyst / Auditor (Read-Only)
- employee@skillflow.com  (employee123) ->  Standard Employee (Self-Service)
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Set


class UserRole(str, Enum):
    """Enumeration of system security roles."""
    HR_ADMIN = "HR Administrator"
    DEPT_MANAGER = "Department Manager"
    DATA_ANALYST = "Compliance Auditor"
    EMPLOYEE = "Standard Employee"


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


@dataclass
class User:
    """Represents an authenticated system user profile."""
    email: str
    password: str
    name: str
    title: str
    role: UserRole
    department: str
    badge_tag: str = "USER"
    avatar: str = ""


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
        Permission.VIEW_DASHBOARD,
        Permission.VIEW_EMPLOYEE_DIRECTORY,
        Permission.VIEW_PROJECTS,
    },
}

# Navigation pages accessible by role
ROLE_PAGES: dict[UserRole, list[str]] = {
    UserRole.HR_ADMIN: ["Dashboard", "Employees", "Projects", "Reviews", "Warehouse"],
    UserRole.DEPT_MANAGER: ["Dashboard", "Employees", "Projects", "Reviews"],
    UserRole.DATA_ANALYST: ["Dashboard", "Employees", "Projects", "Reviews", "Warehouse"],
    UserRole.EMPLOYEE: ["Dashboard", "Employees", "Projects"],
}

# Role display metadata
ROLE_METADATA = {
    UserRole.HR_ADMIN: {
        "title": "HR Administrator",
        "badge": "Full Access (Admin)",
        "badge_color": "#6366f1",
        "description": "Unrestricted administrative access to all analytics, employee onboarding, SCD2 transfers, appraisals, and warehouse ETL sync.",
        "can_write": True,
    },
    UserRole.DEPT_MANAGER: {
        "title": "Department Manager",
        "badge": "Operations / Staffing",
        "badge_color": "#3b82f6",
        "description": "Operational access to team allocations, staff reviews, and department transfers. Restricted from DW ETL sync and system administration.",
        "can_write": True,
    },
    UserRole.DATA_ANALYST: {
        "title": "Compliance Auditor",
        "badge": "Read-Only Audit Mode",
        "badge_color": "#06b6d4",
        "description": "Read-only access to executive dashboards, historical reviews, and warehouse logs. Write operations are disabled for audit compliance.",
        "can_write": False,
    },
    UserRole.EMPLOYEE: {
        "title": "Standard Employee",
        "badge": "Self-Service",
        "badge_color": "#10b981",
        "description": "Self-service access to employee directory, project rosters, and high-level dashboard. Management forms and warehouse internals are restricted.",
        "can_write": False,
    },
}

# Pre-Configured Demo Enterprise Accounts
DEMO_USERS: dict[str, User] = {
    "admin@skillflow.com": User(
        email="admin@skillflow.com",
        password="admin",
        name="Pratik Modi",
        title="Chief People Officer / HR Admin",
        role=UserRole.HR_ADMIN,
        department="Executive Leadership",
        badge_tag="ADMIN",
        avatar="ADM",
    ),
    "manager@skillflow.com": User(
        email="manager@skillflow.com",
        password="manager",
        name="Sarah Jenkins",
        title="Engineering Director",
        role=UserRole.DEPT_MANAGER,
        department="Research & Development",
        badge_tag="MANAGER",
        avatar="MGR",
    ),
    "auditor@skillflow.com": User(
        email="auditor@skillflow.com",
        password="audit",
        name="David Vance",
        title="Senior Compliance Auditor",
        role=UserRole.DATA_ANALYST,
        department="Governance & Risk",
        badge_tag="AUDITOR",
        avatar="AUD",
    ),
    "employee@skillflow.com": User(
        email="employee@skillflow.com",
        password="emp",
        name="Alex Chen",
        title="Senior Software Engineer",
        role=UserRole.EMPLOYEE,
        department="Engineering",
        badge_tag="EMPLOYEE",
        avatar="EMP",
    ),
}


class RBAC:
    """Helper methods for authentication and permission evaluation."""

    @staticmethod
    def authenticate(email: str, password: str) -> User | None:
        """Validate credentials against the enterprise user store."""
        clean_email = email.strip().lower()
        user = DEMO_USERS.get(clean_email)
        if user and user.password == password.strip():
            return user
        return None

    @staticmethod
    def get_user_by_role(role: UserRole) -> User:
        """Retrieve the primary demo user for a specific role."""
        for user in DEMO_USERS.values():
            if user.role == role:
                return user
        return list(DEMO_USERS.values())[0]

    @staticmethod
    def get_all_demo_users() -> list[User]:
        """Return all demo users for easy showcase login."""
        return list(DEMO_USERS.values())

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
