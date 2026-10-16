"""
Enterprise Employee Analytics & Data Warehouse System
=====================================================

Synthetic data generator for a relational OLTP + dimensional OLAP project.

Output datasets
---------------
1. departments.csv
   Grain: one row per department.

2. employees.csv
   Grain: one row per unique employee/current employee profile.

3. employee_history.csv
   Grain: one row per SCD Type 2 employee version.

4. projects.csv
   Grain: one row per project.

5. employee_project_assignments.csv
   Grain: one row per employee-project assignment.

6. performance_reviews.csv
   Grain: one row per performance review event.

Important
---------
The IBM HR Analytics dataset is used only as a statistical/reference seed.
Actual names, emails, phone numbers and employee identities are generated
synthetically with Faker.

The generated data is intended for MySQL STAGING -> OLTP -> OLAP loading.

Requirements
------------
Python 3.11+
pandas
numpy
Faker
kagglehub
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import kagglehub
import numpy as np
import pandas as pd
from faker import Faker


# ============================================================================
# LOGGING
# ============================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger(__name__)


# ============================================================================
# CONFIGURATION
# ============================================================================

@dataclass(frozen=True)
class GeneratorConfig:
    random_seed: int = 42

    # Base employees.
    num_employees: int = 15_000

    # Percentage of employees receiving SCD2 history.
    historical_employee_percentage: float = 0.35

    min_history_versions: int = 2
    max_history_versions: int = 4

    num_projects: int = 1_500

    min_projects_per_employee: int = 1
    max_projects_per_employee: int = 5

    min_reviews_per_employee: int = 2
    max_reviews_per_employee: int = 5

    organization_start_date: date = date(2022, 1, 1)
    reference_date: date = date(2026, 10, 1)

    output_directory: str = "synthetic_data"

    ibm_dataset: str = (
        "pavansubhasht/ibm-hr-analytics-attrition-dataset"
    )


# ============================================================================
# MASTER DATA
# ============================================================================

DEPARTMENTS = {
    "DEPT-001": {
        "DepartmentName": "Sales",
        "roles": [
            "Sales Executive",
            "Sales Representative",
            "Account Manager",
            "Sales Manager",
        ],
    },
    "DEPT-002": {
        "DepartmentName": "Research & Development",
        "roles": [
            "Research Scientist",
            "Data Scientist",
            "Research Engineer",
            "ML Engineer",
            "Research Manager",
        ],
    },
    "DEPT-003": {
        "DepartmentName": "Human Resources",
        "roles": [
            "HR Specialist",
            "HR Analyst",
            "Talent Acquisition Specialist",
            "HR Manager",
        ],
    },
    "DEPT-004": {
        "DepartmentName": "Finance",
        "roles": [
            "Financial Analyst",
            "Senior Financial Analyst",
            "Accountant",
            "Finance Manager",
        ],
    },
    "DEPT-005": {
        "DepartmentName": "Information Technology",
        "roles": [
            "Software Engineer",
            "Data Engineer",
            "Data Scientist",
            "ML Engineer",
            "DevOps Engineer",
            "Solution Architect",
            "IT Manager",
        ],
    },
    "DEPT-006": {
        "DepartmentName": "Operations",
        "roles": [
            "Operations Analyst",
            "Process Analyst",
            "Operations Specialist",
            "Operations Manager",
        ],
    },
    "DEPT-007": {
        "DepartmentName": "Marketing",
        "roles": [
            "Marketing Analyst",
            "Marketing Specialist",
            "Digital Marketing Specialist",
            "Marketing Manager",
        ],
    },
    "DEPT-008": {
        "DepartmentName": "Product Management",
        "roles": [
            "Product Analyst",
            "Product Owner",
            "Product Manager",
            "Senior Product Manager",
        ],
    },
    "DEPT-009": {
        "DepartmentName": "Legal & Compliance",
        "roles": [
            "Compliance Analyst",
            "Legal Analyst",
            "Risk Analyst",
            "Compliance Manager",
        ],
    },
    "DEPT-010": {
        "DepartmentName": "Customer Success",
        "roles": [
            "Customer Success Executive",
            "Customer Success Analyst",
            "Customer Success Manager",
        ],
    },
    "DEPT-011": {
        "DepartmentName": "Procurement",
        "roles": [
            "Procurement Analyst",
            "Procurement Specialist",
            "Procurement Manager",
        ],
    },
    "DEPT-012": {
        "DepartmentName": "Supply Chain",
        "roles": [
            "Supply Chain Analyst",
            "Supply Chain Specialist",
            "Supply Chain Manager",
        ],
    },
}

PROJECT_TYPES = [
    "Digital Transformation",
    "Data Analytics",
    "Machine Learning",
    "Cloud Migration",
    "Enterprise Application",
    "Business Intelligence",
    "Cybersecurity",
    "Process Automation",
    "Data Governance",
    "Customer Experience",
]

PROJECT_NAME_PREFIXES = [
    "Enterprise",
    "Customer",
    "Workforce",
    "Cloud",
    "Intelligent",
    "Digital",
    "Strategic",
    "Advanced",
    "Modern",
    "Integrated",
]

PROJECT_NAME_SUFFIXES = [
    "Modernization",
    "Transformation",
    "Platform",
    "Implementation",
    "Migration",
    "Optimization",
    "Enablement",
    "Analytics",
    "Initiative",
    "Program",
]


# ============================================================================
# GENERATOR
# ============================================================================

class SyntheticDataGenerator:
    def __init__(self, config: GeneratorConfig):
        self.config = config

        np.random.seed(config.random_seed)
        self.rng = np.random.default_rng(config.random_seed)

        self.fake = Faker("en_IN")
        Faker.seed(config.random_seed)

        self.output_dir = Path(config.output_directory)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.ibm_data: pd.DataFrame | None = None

        self.departments: pd.DataFrame | None = None
        self.employees: pd.DataFrame | None = None
        self.employee_history: pd.DataFrame | None = None
        self.projects: pd.DataFrame | None = None
        self.assignments: pd.DataFrame | None = None
        self.performance_reviews: pd.DataFrame | None = None

        self.used_phones: set[str] = set()
        self.used_emails: set[str] = set()

    # ------------------------------------------------------------------------
    # IBM DATASET
    # ------------------------------------------------------------------------

    def load_ibm_dataset(self) -> None:
        logger.info("Downloading IBM HR dataset using kagglehub...")

        path = kagglehub.dataset_download(self.config.ibm_dataset)
        dataset_path = Path(path)

        csv_files = list(dataset_path.glob("*.csv"))

        if not csv_files:
            raise FileNotFoundError(
                f"No CSV file found in Kaggle dataset directory: {dataset_path}"
            )

        # Prefer the standard IBM HR filename when present.
        preferred = [
            p for p in csv_files
            if "WA_Fn-UseC_-HR-Employee-Attrition" in p.name
        ]

        source_csv = preferred[0] if preferred else csv_files[0]

        logger.info("Dataset downloaded to: %s", dataset_path)
        logger.info("Using IBM source CSV: %s", source_csv)
        logger.info("Reading IBM source dataset...")

        self.ibm_data = pd.read_csv(source_csv)

        logger.info(
            "IBM dataset loaded: %d rows x %d columns",
            len(self.ibm_data),
            len(self.ibm_data.columns),
        )

        required = {
            "Department",
            "JobRole",
            "JobLevel",
            "MonthlyIncome",
            "Attrition",
            "Gender",
            "MaritalStatus",
            "EducationField",
            "PerformanceRating",
        }

        missing = required - set(self.ibm_data.columns)

        if missing:
            raise ValueError(
                f"IBM dataset is missing required columns: {sorted(missing)}"
            )

    # ------------------------------------------------------------------------
    # DATE HELPERS
    # ------------------------------------------------------------------------

    def random_date(self, start: date, end: date) -> date:
        if end < start:
            return start

        days = (end - start).days

        if days == 0:
            return start

        return start + timedelta(
            days=int(self.rng.integers(0, days + 1))
        )

    # ------------------------------------------------------------------------
    # PHONE / EMAIL
    # ------------------------------------------------------------------------

    def generate_phone(self) -> str:
        while True:
            phone = (
                str(self.rng.choice(["6", "7", "8", "9"]))
                + "".join(
                    self.rng.choice(
                        list("0123456789"),
                        size=9,
                    )
                )
            )

            if phone not in self.used_phones:
                self.used_phones.add(phone)
                return phone

    def generate_email(self, first_name: str, last_name: str, employee_no: int) -> str:
        base = (
            f"{first_name.lower()}.{last_name.lower()}"
            f"{employee_no}@company.com"
        )

        email = base
        counter = 1

        while email in self.used_emails:
            email = (
                f"{first_name.lower()}.{last_name.lower()}"
                f"{employee_no}{counter}@company.com"
            )
            counter += 1

        self.used_emails.add(email)
        return email

    # ------------------------------------------------------------------------
    # DEPARTMENTS
    # ------------------------------------------------------------------------

    def generate_departments(self) -> None:
        rows = []

        for department_id, data in DEPARTMENTS.items():
            rows.append(
                {
                    "DepartmentID": department_id,
                    "DepartmentName": data["DepartmentName"],
                    "DepartmentStatus": "Active",
                }
            )

        self.departments = pd.DataFrame(rows)

    # ------------------------------------------------------------------------
    # JOB ROLE MASTER
    # ------------------------------------------------------------------------

    def generate_job_roles(self) -> pd.DataFrame:
        rows = []
        role_id = 1

        for department_id, data in DEPARTMENTS.items():
            for role in data["roles"]:
                rows.append(
                    {
                        "JobRoleID": f"ROLE-{role_id:04d}",
                        "DepartmentID": department_id,
                        "JobRole": role,
                    }
                )
                role_id += 1

        return pd.DataFrame(rows)

    # ------------------------------------------------------------------------
    # ROLE / LEVEL / SALARY
    # ------------------------------------------------------------------------

    def role_level(self, role: str, seed_level: int | None = None) -> int:
        if "Manager" in role or "Architect" in role:
            return int(self.rng.choice([4, 5]))

        if "Senior" in role or "Lead" in role:
            return int(self.rng.choice([3, 4]))

        if seed_level is not None:
            return max(1, min(3, int(seed_level)))

        return int(self.rng.choice([1, 2, 3], p=[0.25, 0.45, 0.30]))

    def monthly_salary(self, level: int, ibm_income: int) -> int:
        # IBM MonthlyIncome is used as a distribution seed, but the generated
        # salary is deliberately adjusted into a synthetic Indian salary range.
        base = {
            1: 35_000,
            2: 55_000,
            3: 80_000,
            4: 115_000,
            5: 160_000,
        }[level]

        seed_factor = np.clip(ibm_income / 7000.0, 0.70, 1.30)
        noise = self.rng.uniform(0.90, 1.10)

        return int(base * seed_factor * noise)

    # ------------------------------------------------------------------------
    # EMPLOYEES
    # ------------------------------------------------------------------------

    def generate_employees(self) -> None:
        assert self.ibm_data is not None

        logger.info(
            "Generating %s employees...",
            f"{self.config.num_employees:,}",
        )

        ibm = self.ibm_data

        # Use IBM distributions for categorical seed attributes.
        department_probs = ibm["Department"].value_counts(normalize=True)

        department_name_to_id = {
            value["DepartmentName"]: key
            for key, value in DEPARTMENTS.items()
        }

        # Map IBM's three source departments into the larger synthetic master.
        source_to_synthetic = {
            "Sales": "Sales",
            "Research & Development": "Research & Development",
            "Human Resources": "Human Resources",
        }

        rows: list[dict[str, Any]] = []

        for employee_no in range(1, self.config.num_employees + 1):
            source_row = ibm.iloc[
                int(self.rng.integers(0, len(ibm)))
            ]

            source_department = str(
                self.rng.choice(
                    department_probs.index.to_numpy(),
                    p=department_probs.to_numpy(),
                )
            )

            if source_department in source_to_synthetic:
                department_name = source_to_synthetic[source_department]
            else:
                department_name = str(
                    self.rng.choice(
                        [
                            d["DepartmentName"]
                            for d in DEPARTMENTS.values()
                        ]
                    )
                )

            department_id = department_name_to_id[department_name]

            roles = DEPARTMENTS[department_id]["roles"]

            # Prefer IBM role information where a logically similar role exists.
            ibm_role = str(source_row["JobRole"])
            matching_roles = [
                role for role in roles
                if role.lower() in ibm_role.lower()
                or ibm_role.lower() in role.lower()
            ]

            role = (
                matching_roles[0]
                if matching_roles
                else str(self.rng.choice(roles))
            )

            level = self.role_level(
                role,
                int(source_row["JobLevel"]),
            )

            first_name = self.fake.first_name()
            last_name = self.fake.last_name()

            employee_id = f"EMP-{employee_no:07d}"

            hire_date = self.random_date(
                date(2018, 1, 1),
                self.config.reference_date - timedelta(days=30),
            )

            attrition_probability = (
                0.14
                if str(source_row["Attrition"]) == "Yes"
                else 0.08
            )

            attrition = (
                "Yes"
                if self.rng.random() < attrition_probability
                else "No"
            )

            termination_date = None

            if attrition == "Yes":
                earliest = hire_date + timedelta(days=180)

                if earliest <= self.config.reference_date:
                    termination_date = self.random_date(
                        earliest,
                        self.config.reference_date,
                    )
                else:
                    attrition = "No"

            employment_status = (
                "Terminated"
                if termination_date is not None
                else "Active"
            )

            rows.append(
                {
                    "EmployeeID": employee_id,
                    "EmployeeNumber": employee_no,
                    "FirstName": first_name,
                    "LastName": last_name,
                    "Email": self.generate_email(
                        first_name,
                        last_name,
                        employee_no,
                    ),
                    "PhoneNumber": self.generate_phone(),
                    "Gender": str(source_row["Gender"]),
                    "MaritalStatus": str(source_row["MaritalStatus"]),
                    "HireDate": hire_date,
                    "TerminationDate": termination_date,
                    "EmploymentStatus": employment_status,
                    "DepartmentID": department_id,
                    "Department": department_name,
                    "JobRole": role,
                    "JobLevel": level,
                    "MonthlyIncome": self.monthly_salary(
                        level,
                        int(source_row["MonthlyIncome"]),
                    ),
                    "ManagerEmployeeID": None,
                    "Attrition": attrition,
                    "Age": int(source_row["Age"]),
                    "EducationField": str(source_row["EducationField"]),
                    "PerformanceRating": int(
                        source_row["PerformanceRating"]
                    ),
                    "OverTime": str(source_row["OverTime"]),
                    "JobSatisfaction": int(source_row["JobSatisfaction"]),
                    "EnvironmentSatisfaction": int(
                        source_row["EnvironmentSatisfaction"]
                    ),
                    "WorkLifeBalance": int(source_row["WorkLifeBalance"]),
                    "TotalWorkingYears": int(source_row["TotalWorkingYears"]),
                }
            )

            if employee_no % 5_000 == 0:
                logger.info(
                    "Generated %s employees...",
                    f"{employee_no:,}",
                )

        self.employees = pd.DataFrame(rows)

        self.assign_managers()

    # ------------------------------------------------------------------------
    # MANAGERS
    # ------------------------------------------------------------------------

    def assign_managers(self) -> None:
        assert self.employees is not None

        employee_ids = self.employees["EmployeeID"].tolist()

        # Managers are selected from level 4/5 employees.
        manager_candidates = self.employees.loc[
            self.employees["JobLevel"] >= 4,
            "EmployeeID",
        ].tolist()

        if not manager_candidates:
            raise ValueError("No manager candidates were generated.")

        manager_set = set(manager_candidates)

        for index in self.employees.index:
            employee_id = self.employees.at[index, "EmployeeID"]

            if employee_id in manager_set:
                self.employees.at[index, "ManagerEmployeeID"] = None
                continue

            employee_department = self.employees.at[index, "DepartmentID"]

            same_department_managers = self.employees.loc[
                (self.employees["DepartmentID"] == employee_department)
                & (self.employees["JobLevel"] >= 4)
                & (self.employees["EmployeeID"] != employee_id),
                "EmployeeID",
            ].tolist()

            candidates = (
                same_department_managers
                if same_department_managers
                else manager_candidates
            )

            self.employees.at[index, "ManagerEmployeeID"] = str(
                self.rng.choice(candidates)
            )

    # ------------------------------------------------------------------------
    # SCD TYPE 2
    # ------------------------------------------------------------------------

    def generate_employee_history(self) -> None:
        assert self.employees is not None

        logger.info("Generating employee SCD Type 2 history...")

        history_rows: list[dict[str, Any]] = []

        employee_ids = self.employees["EmployeeID"].to_numpy()

        historical_count = int(
            len(employee_ids)
            * self.config.historical_employee_percentage
        )

        historical_ids = set(
            self.rng.choice(
                employee_ids,
                size=historical_count,
                replace=False,
            )
        )

        next_employee_key = 1

        tracked_columns = [
            "DepartmentID",
            "Department",
            "JobRole",
            "JobLevel",
            "MonthlyIncome",
            "ManagerEmployeeID",
        ]

        for _, employee_row in self.employees.iterrows():
            base = employee_row.to_dict()
            employee_id = str(base["EmployeeID"])
            hire_date = base["HireDate"]

            if employee_id not in historical_ids:
                version_states = [(hire_date, base.copy(), "Initial Employment")]
            else:
                available_days = (
                    self.config.reference_date - hire_date
                ).days

                possible_versions = min(
                    self.config.max_history_versions,
                    max(2, self.config.min_history_versions),
                )

                if available_days < 365:
                    version_count = 2
                else:
                    version_count = int(
                        self.rng.integers(
                            self.config.min_history_versions,
                            possible_versions + 1,
                        )
                    )

                # A change needs enough time before the reference date.
                latest_change = self.config.reference_date - timedelta(days=30)

                if hire_date >= latest_change:
                    version_count = 1

                change_dates: list[date] = []

                if version_count > 1:
                    low = hire_date + timedelta(days=180)

                    if low < latest_change:
                        sampled = self.rng.choice(
                            np.arange(
                                (low - hire_date).days,
                                (latest_change - hire_date).days + 1,
                            ),
                            size=min(
                                version_count - 1,
                                max(
                                    1,
                                    (latest_change - low).days,
                                ),
                            ),
                            replace=False,
                        )

                        change_dates = sorted(
                            [
                                hire_date + timedelta(days=int(x))
                                for x in sampled
                            ]
                        )

                dates = [hire_date] + change_dates

                # Build cumulative states. Each version starts from the
                # previous version, not from the original employee state.
                current_state = base.copy()
                version_states = [
                    (dates[0], current_state.copy(), "Initial Employment")
                ]

                for change_date in dates[1:]:
                    current_state = current_state.copy()

                    change_type = str(
                        self.rng.choice(
                            [
                                "Promotion",
                                "Department Transfer",
                                "Salary Revision",
                                "Role Change",
                            ],
                            p=[0.35, 0.20, 0.30, 0.15],
                        )
                    )

                    if change_type == "Promotion":
                        current_level = int(current_state["JobLevel"])

                        if current_level >= 5:
                            change_type = "Salary Revision"
                        else:
                            new_level = current_level + 1
                            current_state["JobLevel"] = new_level
                            current_state["MonthlyIncome"] = int(
                                current_state["MonthlyIncome"]
                                * self.rng.uniform(1.08, 1.20)
                            )

                    if change_type == "Salary Revision":
                        current_state["MonthlyIncome"] = int(
                            current_state["MonthlyIncome"]
                            * self.rng.uniform(1.04, 1.12)
                        )

                    elif change_type == "Department Transfer":
                        current_department = current_state["DepartmentID"]

                        alternatives = [
                            department_id
                            for department_id in DEPARTMENTS
                            if department_id != current_department
                        ]

                        new_department = str(
                            self.rng.choice(alternatives)
                        )

                        current_state["DepartmentID"] = new_department
                        current_state["Department"] = DEPARTMENTS[
                            new_department
                        ]["DepartmentName"]

                        current_state["JobRole"] = str(
                            self.rng.choice(
                                DEPARTMENTS[new_department]["roles"]
                            )
                        )

                        current_state["JobLevel"] = min(
                            int(current_state["JobLevel"]),
                            5,
                        )

                    elif change_type == "Role Change":
                        current_department = current_state["DepartmentID"]

                        current_state["JobRole"] = str(
                            self.rng.choice(
                                DEPARTMENTS[current_department]["roles"]
                            )
                        )

                    version_states.append(
                        (
                            change_date,
                            current_state.copy(),
                            change_type,
                        )
                    )

            for version_index, (
                effective_start,
                state,
                change_reason,
            ) in enumerate(version_states):
                if version_index < len(version_states) - 1:
                    effective_end = (
                        version_states[version_index + 1][0]
                        - timedelta(days=1)
                    )
                    is_current = 0
                else:
                    effective_end = None
                    is_current = 1

                record = state.copy()
                record["EmployeeKey"] = next_employee_key
                record["EffectiveStartDate"] = effective_start
                record["EffectiveEndDate"] = effective_end
                record["IsCurrent"] = is_current
                record["ChangeReason"] = change_reason

                history_rows.append(record)
                next_employee_key += 1

        self.employee_history = pd.DataFrame(history_rows)

    # ------------------------------------------------------------------------
    # PROJECTS
    # ------------------------------------------------------------------------

    def generate_projects(self) -> None:
        assert self.employees is not None

        logger.info(
            "Generating %s projects...",
            f"{self.config.num_projects:,}",
        )

        manager_candidates = self.employees.loc[
            self.employees["JobLevel"] >= 4,
            "EmployeeID",
        ].tolist()

        rows = []

        for project_number in range(1, self.config.num_projects + 1):
            project_id = f"PROJ-{project_number:05d}"

            project_type = str(
                self.rng.choice(PROJECT_TYPES)
            )

            project_name = (
                f"{self.rng.choice(PROJECT_NAME_PREFIXES)} "
                f"{project_type} "
                f"{self.rng.choice(PROJECT_NAME_SUFFIXES)} "
                f"{project_number:04d}"
            )

            start_date = self.random_date(
                self.config.organization_start_date,
                self.config.reference_date,
            )

            duration_days = int(
                self.rng.integers(90, 730)
            )

            planned_end = start_date + timedelta(days=duration_days)

            if planned_end < self.config.reference_date:
                status = "Completed"
                end_date = planned_end
            elif start_date <= self.config.reference_date:
                status = "In Progress"
                end_date = None
            else:
                status = "Planned"
                end_date = planned_end

            project_manager = str(
                self.rng.choice(manager_candidates)
            )

            rows.append(
                {
                    "ProjectID": project_id,
                    "ProjectName": project_name,
                    "ProjectType": project_type,
                    "ProjectStartDate": start_date,
                    "ProjectEndDate": end_date,
                    "ProjectStatus": status,
                    "ProjectManagerEmployeeID": project_manager,
                }
            )

        self.projects = pd.DataFrame(rows)

    # ------------------------------------------------------------------------
    # ASSIGNMENTS
    # ------------------------------------------------------------------------

    def generate_assignments(self) -> None:
        assert self.employees is not None
        assert self.projects is not None

        logger.info("Generating employee-project assignments...")

        project_records = self.projects.to_dict("records")
        rows = []
        assignment_id = 1

        employee_records = self.employees.to_dict("records")

        for employee in employee_records:
            project_count = int(
                self.rng.integers(
                    self.config.min_projects_per_employee,
                    self.config.max_projects_per_employee + 1,
                )
            )

            selected_projects = self.rng.choice(
                project_records,
                size=min(project_count, len(project_records)),
                replace=False,
            )

            employee_hire = employee["HireDate"]
            employee_termination = employee["TerminationDate"]

            for project in selected_projects:
                project_start = project["ProjectStartDate"]
                project_end = project["ProjectEndDate"]

                earliest = max(employee_hire, project_start)

                if project["ProjectStatus"] == "Planned":
                    # Do not create assignments that have already started
                    # before the project itself starts.
                    assignment_start = project_start
                else:
                    latest_start = (
                        project_end - timedelta(days=30)
                        if project_end is not None
                        else self.config.reference_date
                    )

                    if latest_start < earliest:
                        continue

                    assignment_start = self.random_date(
                        earliest,
                        latest_start,
                    )

                if employee_termination is not None:
                    if assignment_start > employee_termination:
                        continue

                    assignment_end_limit = employee_termination
                else:
                    assignment_end_limit = self.config.reference_date

                if project_end is not None:
                    assignment_end_limit = min(
                        assignment_end_limit,
                        project_end,
                    )

                if assignment_end_limit < assignment_start:
                    continue

                if project["ProjectStatus"] == "Completed":
                    assignment_end = self.random_date(
                        assignment_start,
                        assignment_end_limit,
                    )
                    status = "Completed"
                elif project["ProjectStatus"] == "Planned":
                    assignment_end = None
                    status = "Planned"
                else:
                    assignment_end = None
                    status = "Active"

                assignment_role = str(
                    self.rng.choice(
                        [
                            "Contributor",
                            "Technical Lead",
                            "Business Analyst",
                            "Project Analyst",
                            "Subject Matter Expert",
                        ]
                    )
                )

                allocation = int(
                    self.rng.choice(
                        [25, 50, 75, 100],
                        p=[0.10, 0.30, 0.40, 0.20],
                    )
                )

                rows.append(
                    {
                        "AssignmentID": f"ASG-{assignment_id:07d}",
                        "EmployeeID": employee["EmployeeID"],
                        "ProjectID": project["ProjectID"],
                        "AssignmentStartDate": assignment_start,
                        "AssignmentEndDate": assignment_end,
                        "AssignmentRole": assignment_role,
                        "AllocationPercentage": allocation,
                        "AssignmentStatus": status,
                    }
                )

                assignment_id += 1

        self.assignments = pd.DataFrame(rows)

    # ------------------------------------------------------------------------
    # PERFORMANCE REVIEWS
    # ------------------------------------------------------------------------

    def generate_performance_reviews(self) -> None:
        assert self.employees is not None

        logger.info("Generating performance reviews...")

        rows = []
        review_id = 1

        for employee in self.employees.to_dict("records"):
            employee_hire = employee["HireDate"]
            employee_end = (
                employee["TerminationDate"]
                if employee["TerminationDate"] is not None
                else self.config.reference_date
            )

            available_years = max(
                1,
                (employee_end - employee_hire).days // 365,
            )

            review_count = min(
                int(
                    self.rng.integers(
                        self.config.min_reviews_per_employee,
                        self.config.max_reviews_per_employee + 1,
                    )
                ),
                max(1, available_years),
            )

            if employee_hire > employee_end:
                continue

            # Review dates are chronologically ordered and independent of SCD2.
            review_dates = []

            if review_count == 1:
                review_dates = [
                    self.random_date(employee_hire, employee_end)
                ]
            else:
                total_days = (employee_end - employee_hire).days

                if total_days <= 0:
                    review_dates = [employee_hire]
                else:
                    offsets = np.linspace(
                        90,
                        max(90, total_days - 30),
                        review_count,
                    )

                    review_dates = [
                        employee_hire + timedelta(days=int(x))
                        for x in offsets
                    ]

                    review_dates = sorted(
                        set(review_dates)
                    )

            for review_date in review_dates:
                rating = int(
                    self.rng.choice(
                        [1, 2, 3, 4, 5],
                        p=[0.04, 0.12, 0.36, 0.36, 0.12],
                    )
                )

                performance_score = int(
                    np.clip(
                        self.rng.normal(
                            loc=rating * 18,
                            scale=7,
                        ),
                        20,
                        100,
                    )
                )

                promotion_recommended = (
                    "Yes"
                    if rating >= 4 and performance_score >= 75
                    else "No"
                )

                reviewer = employee["ManagerEmployeeID"]

                if reviewer is None:
                    reviewer = employee["EmployeeID"]

                rows.append(
                    {
                        "ReviewID": f"REV-{review_id:08d}",
                        "EmployeeID": employee["EmployeeID"],
                        "ReviewDate": review_date,
                        "Rating": rating,
                        "PerformanceScore": performance_score,
                        "PromotionRecommendation": promotion_recommended,
                        "ReviewerEmployeeID": reviewer,
                    }
                )

                review_id += 1

        self.performance_reviews = pd.DataFrame(rows)

    # ------------------------------------------------------------------------
    # VALIDATION
    # ------------------------------------------------------------------------

    def validate(self) -> None:
        assert self.employees is not None
        assert self.employee_history is not None
        assert self.projects is not None
        assert self.assignments is not None
        assert self.performance_reviews is not None

        logger.info("Running synthetic data validation...")

        employees = self.employees
        history = self.employee_history
        projects = self.projects
        assignments = self.assignments
        reviews = self.performance_reviews

        errors: list[str] = []

        # Employees
        if employees["EmployeeID"].duplicated().any():
            errors.append("EmployeeID is not unique.")

        if employees["Email"].duplicated().any():
            errors.append("Email is not unique.")

        if employees["PhoneNumber"].duplicated().any():
            errors.append("PhoneNumber is not unique.")

        phone_valid = employees["PhoneNumber"].astype(str).str.fullmatch(
            r"[6-9][0-9]{9}"
        )

        if not phone_valid.all():
            errors.append("One or more phone numbers are invalid.")

        if (
            employees["HireDate"] > self.config.reference_date
        ).any():
            errors.append("HireDate contains future dates.")

        invalid_termination = (
            employees["TerminationDate"].notna()
            & (
                (employees["TerminationDate"] <= employees["HireDate"])
                | (
                    employees["TerminationDate"]
                    > self.config.reference_date
                )
            )
        )

        if invalid_termination.any():
            errors.append("Invalid termination dates.")

        # Manager references
        employee_ids = set(employees["EmployeeID"])

        invalid_managers = (
            employees["ManagerEmployeeID"].dropna()
            .astype(str)
            .loc[
                lambda s: ~s.isin(employee_ids)
            ]
        )

        if len(invalid_managers) > 0:
            errors.append("Orphan ManagerEmployeeID references exist.")

        if (
            employees["EmployeeID"]
            == employees["ManagerEmployeeID"]
        ).any():
            errors.append("Employee manages themselves.")

        # SCD2
        current_counts = (
            history.groupby("EmployeeID")["IsCurrent"].sum()
        )

        if not (current_counts == 1).all():
            errors.append(
                "Every EmployeeID must have exactly one current SCD2 version."
            )

        history_employee_ids = set(history["EmployeeID"])

        if history_employee_ids != employee_ids:
            errors.append(
                "Employee history does not contain exactly the employee population."
            )

        for employee_id, group in history.groupby("EmployeeID"):
            group = group.sort_values("EffectiveStartDate")

            starts = group["EffectiveStartDate"].tolist()
            ends = group["EffectiveEndDate"].tolist()

            employee_hire = employees.loc[
                employees["EmployeeID"] == employee_id,
                "HireDate",
            ].iloc[0]

            if starts[0] != employee_hire:
                errors.append(
                    f"{employee_id}: first SCD2 version does not start on HireDate."
                )

            for i in range(len(group) - 1):
                if ends[i] is None or pd.isna(ends[i]):
                    errors.append(
                        f"{employee_id}: historical version has NULL end date."
                    )
                    break

                if ends[i] >= starts[i + 1]:
                    errors.append(
                        f"{employee_id}: overlapping SCD2 versions."
                    )
                    break

            current = group[group["IsCurrent"] == 1]

            if len(current) != 1:
                errors.append(
                    f"{employee_id}: invalid current-version count."
                )

            elif pd.notna(current.iloc[0]["EffectiveEndDate"]):
                errors.append(
                    f"{employee_id}: current version has an end date."
                )

        # Projects
        if projects["ProjectID"].duplicated().any():
            errors.append("ProjectID is not unique.")

        valid_statuses = {"Planned", "In Progress", "Completed"}

        if not set(projects["ProjectStatus"]).issubset(valid_statuses):
            errors.append("Invalid project status found.")

        invalid_project_dates = (
            projects["ProjectEndDate"].notna()
            & (
                projects["ProjectEndDate"]
                < projects["ProjectStartDate"]
            )
        )

        if invalid_project_dates.any():
            errors.append("ProjectEndDate occurs before ProjectStartDate.")

        completed_null_end = (
            (projects["ProjectStatus"] == "Completed")
            & projects["ProjectEndDate"].isna()
        )

        if completed_null_end.any():
            errors.append("Completed projects have NULL end dates.")

        # Assignments
        project_ids = set(projects["ProjectID"])

        if not set(assignments["EmployeeID"]).issubset(employee_ids):
            errors.append("Assignments contain orphan EmployeeID values.")

        if not set(assignments["ProjectID"]).issubset(project_ids):
            errors.append("Assignments contain orphan ProjectID values.")

        invalid_allocation = ~assignments[
            "AllocationPercentage"
        ].isin([25, 50, 75, 100])

        if invalid_allocation.any():
            errors.append("Invalid allocation percentage.")

        invalid_assignment_dates = (
            assignments["AssignmentEndDate"].notna()
            & (
                assignments["AssignmentEndDate"]
                < assignments["AssignmentStartDate"]
            )
        )

        if invalid_assignment_dates.any():
            errors.append("Invalid assignment dates.")

        # Reviews
        if reviews["ReviewID"].duplicated().any():
            errors.append("ReviewID is not unique.")

        if not set(reviews["EmployeeID"]).issubset(employee_ids):
            errors.append("Reviews contain orphan EmployeeID values.")

        if not set(reviews["ReviewerEmployeeID"]).issubset(employee_ids):
            errors.append("Reviews contain orphan ReviewerEmployeeID values.")

        if not reviews["Rating"].between(1, 5).all():
            errors.append("Review rating must be between 1 and 5.")

        if not reviews["PerformanceScore"].between(0, 100).all():
            errors.append("PerformanceScore must be between 0 and 100.")

        # Total generated data volume.
        total_rows = (
            len(employees)
            + len(history)
            + len(projects)
            + len(assignments)
            + len(reviews)
            + len(self.departments)
        )

        if total_rows < 100_000:
            errors.append(
                f"Total generated records are below 100,000: {total_rows:,}."
            )

        if errors:
            logger.error("DATA QUALITY VALIDATION FAILED:")

            for error in errors:
                logger.error("  - %s", error)

            raise ValueError(
                f"Validation failed with {len(errors)} error(s)."
            )

        logger.info("Validation successful.")

    # ------------------------------------------------------------------------
    # EXPORT
    # ------------------------------------------------------------------------

    def export(self) -> None:
        assert self.departments is not None
        assert self.employees is not None
        assert self.employee_history is not None
        assert self.projects is not None
        assert self.assignments is not None
        assert self.performance_reviews is not None

        logger.info("Saving synthetic datasets...")

        datasets = {
            "departments.csv": self.departments,
            "employees.csv": self.employees,
            "employee_history.csv": self.employee_history,
            "projects.csv": self.projects,
            "employee_project_assignments.csv": self.assignments,
            "performance_reviews.csv": self.performance_reviews,
        }

        for filename, dataframe in datasets.items():
            dataframe.to_csv(
                self.output_dir / filename,
                index=False,
                encoding="utf-8",
            )

        # Data dictionary is useful for the staging layer and project review.
        data_dictionary = []

        for filename, dataframe in datasets.items():
            for column in dataframe.columns:
                data_dictionary.append(
                    {
                        "FileName": filename,
                        "ColumnName": column,
                        "DataType": str(dataframe[column].dtype),
                        "Nullable": bool(dataframe[column].isna().any()),
                    }
                )

        pd.DataFrame(data_dictionary).to_csv(
            self.output_dir / "synthetic_data_dictionary.csv",
            index=False,
            encoding="utf-8",
        )

        logger.info("Files saved to: %s", self.output_dir.resolve())

    # ------------------------------------------------------------------------
    # SUMMARY
    # ------------------------------------------------------------------------

    def print_summary(self) -> None:
        assert self.departments is not None
        assert self.employees is not None
        assert self.employee_history is not None
        assert self.projects is not None
        assert self.assignments is not None
        assert self.performance_reviews is not None

        logger.info("")
        logger.info("=" * 70)
        logger.info("SYNTHETIC DATA GENERATION SUMMARY")
        logger.info("=" * 70)

        datasets = [
            ("departments.csv", len(self.departments)),
            ("employees.csv", len(self.employees)),
            ("employee_history.csv", len(self.employee_history)),
            ("projects.csv", len(self.projects)),
            (
                "employee_project_assignments.csv",
                len(self.assignments),
            ),
            (
                "performance_reviews.csv",
                len(self.performance_reviews),
            ),
        ]

        # IMPORTANT:
        # Do not use "%,d" with logger.info().
        # Python logging uses %-formatting internally and "%,d" is invalid.
        # Format the comma-separated number before passing it to logging.
        for filename, row_count in datasets:
            logger.info(
                f"{filename:<40} {row_count:>12,} rows"
            )

        total = sum(row_count for _, row_count in datasets)

        logger.info("-" * 70)
        logger.info(
            f"{'TOTAL':<40} {total:>12,} rows"
        )
        logger.info("-" * 70)

        logger.info(
            f"Employees generated: {len(self.employees):,}"
        )
        logger.info(
            f"Historical employee versions: "
            f"{len(self.employee_history):,}"
        )
        logger.info(
            f"Projects generated: {len(self.projects):,}"
        )
        logger.info(
            f"Assignments generated: {len(self.assignments):,}"
        )
        logger.info(
            f"Performance reviews generated: "
            f"{len(self.performance_reviews):,}"
        )

        logger.info("=" * 70)

    # ------------------------------------------------------------------------
    # PIPELINE
    # ------------------------------------------------------------------------

    def run(self) -> None:
        logger.info("=" * 70)
        logger.info("ENTERPRISE SYNTHETIC DATA GENERATION STARTED")
        logger.info("=" * 70)

        self.load_ibm_dataset()
        self.generate_departments()
        self.generate_employees()
        self.generate_employee_history()
        self.generate_projects()
        self.generate_assignments()
        self.generate_performance_reviews()

        self.validate()
        self.export()
        self.print_summary()

        logger.info("=" * 70)
        logger.info("SYNTHETIC DATA GENERATION COMPLETED")
        logger.info("=" * 70)


# ============================================================================
# EXECUTION
# ============================================================================

if __name__ == "__main__":
    config = GeneratorConfig(
        random_seed=42,
        num_employees=15_000,
        historical_employee_percentage=0.35,
        min_history_versions=2,
        max_history_versions=4,
        num_projects=1_500,
        min_projects_per_employee=1,
        max_projects_per_employee=5,
        min_reviews_per_employee=2,
        max_reviews_per_employee=5,
        organization_start_date=date(2022, 1, 1),
        reference_date=date(2026, 10, 1),
        output_directory="synthetic_data",
    )

    generator = SyntheticDataGenerator(config)
    generator.run()
