# SkillFlow: Enterprise Workforce Intelligence & Data Warehouse

<div align="center">

[![GitHub Repository](https://img.shields.io/badge/GitHub-PratikModi22%2FSkillFlow-181717?style=for-the-badge&logo=github)](https://github.com/PratikModi22/SkillFlow)
[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![MySQL](https://img.shields.io/badge/MySQL-8.0-4479A1?style=for-the-badge&logo=mysql&logoColor=white)](https://www.mysql.com/)
[![Streamlit](https://img.shields.io/badge/Streamlit-1.64.0-FF4B4B?style=for-the-badge&logo=streamlit&logoColor=white)](https://streamlit.io/)
[![Architecture](https://img.shields.io/badge/Architecture-Kimball%20Star%20Schema%20SCD2-2A9D8F?style=for-the-badge)](https://github.com/PratikModi22/SkillFlow)
[![Security](https://img.shields.io/badge/Security-RBAC%20Enabled-6A4C93?style=for-the-badge)](https://github.com/PratikModi22/SkillFlow)

<p align="center">
  <strong>A production-grade, three-tier enterprise workforce intelligence platform unifying operational HR transactions (OLTP) with analytical business intelligence (OLAP) via Kimball Star Schema and SCD Type 2 tracking.</strong>
</p>

[Key Features](#-key-features) • [Architecture](#-system-architecture) • [RBAC Security](#-role-based-access-control-rbac) • [Database Model](#-database-schema--dimensional-model) • [Getting Started](#-getting-started--installation)

</div>

---

## 📌 Executive Summary

**SkillFlow** answers a fundamental enterprise challenge: **"What did the organization look like at any historical point in time, and what does it look like today?"**

Traditional operational HR systems overwrite past state whenever an employee is promoted, transferred, or receives a pay raise. SkillFlow solves this by implementing an end-to-end analytical data pipeline:
1. **Operational OLTP Engine:** Enforces normalized 3NF relational integrity, foreign key constraints, and atomic dual-write operations.
2. **Kimball Star Schema (OLAP):** Employs **Slowly Changing Dimensions (SCD Type 2)** on the employee dimension (`dim_employee`), preserving full career version history across department transfers and salary adjustments.
3. **Point-in-Time Fact Attribution:** Connects historical performance appraisals and project allocations to the exact dimension version valid on that review date.
4. **Role-Based Access Control (RBAC):** Restricts interface navigation, administrative tools, and write operations across 4 distinct organizational roles.

---

## 🏗️ System Architecture

<!-- ================================================================= -->
<!-- ARCHITECTURE DIAGRAM PLACEHOLDER:                                 -->
<!-- Replace the image path below with your uploaded architecture diagram -->
<!-- e.g., docs/architecture.png or your hosted image URL               -->
<!-- ================================================================= -->

<div align="center">
  <img width="1171" height="492" alt="Screenshot 2026-10-07 102224" src="https://github.com/user-attachments/assets/bb82ef42-a025-4cf2-b27b-4eefb4a83567" />

  <p><em>Figure 1: SkillFlow End-to-End Enterprise Architecture & Pipeline</em></p>
</div>

<!-- ================================================================= -->
<!-- END ARCHITECTURE DIAGRAM PLACEHOLDER                              -->
<!-- ================================================================= -->

### Architectural Pipeline Breakdown:

* **1. Data Synthesis & Benchmark Seeding:** Generates deterministic HR distributions seeded from the public IBM HR Analytics dataset, producing 15,000 employees, 19,900+ SCD2 versions, and 38,000+ appraisals.
* **2. Tracked Staging Layer:** Ingests raw data in chunked batches stamped with unique `BatchID` tags for full ETL traceability and idempotency.
* **3. Normalized Relational OLTP Core:** Ingests clean staging entities into 3NF tables (`employees`, `departments`, `projects`, `performance_reviews`) with strict database constraints and business validation rules.
* **4. Analytical Star Schema (Data Warehouse):** Transforms normalized entities into 4 conformed dimensions (`dim_employee`, `dim_department`, `dim_project`, `dim_date`) linked to a central fact table (`fact_performance_reviews`).
* **5. Application & Presentation Layer:** A responsive Streamlit dashboard powered by Plotly charts, utilizing an object-oriented Python Data Access Layer (DAL) and connection pooling via `@st.cache_resource`.

---

## 🚀 Key Features

* **Real-Time Dual-Writeback:** Adding an employee or executing a department transfer through the UI updates the OLTP table and appends a versioned `dim_employee` record atomically within the same database transaction.
* **Point-in-Time SCD Type 2 Versioning:** When an employee transfers departments or roles, their active version is closed (`IsCurrent = 0`, sets `EffectiveEndDate`) and a new open record (`IsCurrent = 1`) is appended.
* **Interactive Executive Dashboard:**
  * **YoY Performance Trend:** Uses SQL `LAG()` window functions to evaluate company-wide appraisal trajectory.
  * **Department Leaderboard:** Computes top performers using SQL `DENSE_RANK()`.
  * **Attrition Risk Analysis:** Multi-factor risk scoring identifying organizational turnover flight risks.
  * **Project Bottleneck Detection:** Real-time capacity overload identification for overallocated teams.
* **Workload Capacity Guard (100% Ceiling):** Prevents project managers from allocating more than 100% cumulative bandwidth to any employee across concurrent projects.
* **Automated SMTP Notifications:** Dispatches non-blocking email confirmations to team members upon project assignment.
* **Automated Delta Sync & Reconciliation:** Pre-built stored procedures (`sp_reconcile_dw`) audit database integrity and resolve data drift without requiring full warehouse rebuilds.

---

## 🔐 Role-Based Access Control (RBAC)

SkillFlow enforces a comprehensive four-tier security architecture implemented in `src/rbac.py` and guarded at both the UI and Data Access Layer:

| Security Role | Allowed Navigation Scope | Permitted Operations |
| :--- | :--- | :--- |
| **👑 HR Administrator** | `Dashboard`, `Employees`, `Projects`, `Reviews`, `Warehouse` | **Full Read & Write**: Onboard employees, execute SCD2 department transfers, create projects, assign staff, submit reviews, and trigger warehouse delta syncs. |
| **👥 Department Manager** | `Dashboard`, `Employees`, `Projects`, `Reviews` | **Operational Write**: Transfer employees (SCD2), assign project personnel, and submit reviews. Protected from DW ETL rebuilds. |
| **📊 Data Analyst (Auditor)** | Full read access across all 5 sections | **Strict Read-Only**: Access to all dashboards, drill-down tables, audit logs, and SCD2 timelines. Write forms are locked with security cards. |
| **👤 Standard Employee** | `Employees` (Directory) & `Projects` (Rosters) | **Self-Service Only**: Sensitive executive attrition modeling, compensation distributions, and warehouse internal health tables are hidden. |

> **Interactive Role Switcher:** The sidebar features a live role selector and user profile badge (`👤 Pratik Modi`) enabling evaluators to test permission enforcement dynamically.

---

## 🗄️ Database Schema & Dimensional Model

### Dimensions & Facts:
* **`dim_employee` (SCD Type 2):** Tracks employee versions across time.
  * Key columns: `EmployeeSK` (Surrogate Primary Key), `EmployeeID` (Natural Key), `EffectiveStartDate`, `EffectiveEndDate`, `IsCurrent`, `DepartmentID`, `JobRole`, `MonthlyIncome`.
* **`dim_department` (SCD Type 1):** Department master table with in-place updates.
* **`dim_project` (SCD Type 1):** Organizational project registry and statuses.
* **`dim_date`:** Conformed date dimension spanning 3,000+ calendar days with year, quarter, month, week, and weekend flags.
* **`fact_performance_reviews` (Additive Grain: 1 Review):** Central fact table containing `ReviewSK`, foreign keys to all 4 dimensions, `Rating`, `PerformanceScore`, `PromotionRecommended`, and snapshot metrics (`AttritionFlag`, `TenureYears`).

### Master Stored Procedures:
* **`sp_load_dw_master()`**: Master orchestrator running full ETL pipeline from OLTP to Star Schema.
* **`sp_apply_employee_change()`**: Real-time stored procedure executing incremental SCD2 version closing and insertion upon department/role/salary edits.
* **`sp_load_fact_performance_reviews()`**: Point-in-time join matching each historical review to the specific `dim_employee` version active on that review date.
* **`sp_reconcile_dw()`**: Performs automated mathematical reconciliation between OLTP rows and OLAP dimension counts.

---

## 💻 Tech Stack

| Layer | Component | Version | Description |
| :--- | :--- | :--- | :--- |
| **Database** | MySQL Server | 8.0+ | Relational engine utilizing window functions, recursive CTEs, and stored procedures |
| **Driver** | PyMySQL | 1.2.3 | Pure-Python client communication driver |
| **Database Tooling** | SQLAlchemy | 2.1.2 | Engine connection pooling (`QueuePool`), raw SQL execution, and parameterized queries |
| **Web Framework** | Streamlit | 1.64.0 | Python web dashboard with reactive state management and cached resources |
| **Visualization** | Plotly Express | 7.1.0 | Interactive WebGL/SVG analytical charts |
| **Data Processing** | Pandas / NumPy | 2.2.3 / 2.4.6 | Tabular transformations and numeric aggregations |
| **Synthesis & Benchmark** | Faker / kagglehub | 37.6.0 / 1.0.2 | Synthetic HR data generator and IBM benchmark loader |
| **Environment** | python-dotenv | 1.2.4 | Secure environment configuration management |

---

## 📁 Project Structure

```text
SkillFlow/
├── .devcontainer/                 # Pre-configured GitHub Codespaces environment
├── .github/                       # GitHub Actions CI workflow & PR template
│   ├── workflows/ci.yml           # Automated linting and Python compilation CI
│   └── pull_request_template.md   # Standard pull request checklist
├── database/                      # SQL DDL, ETL Procedures, Views & Indexes
│   ├── 01_create_database.sql     # Database instantiation
│   ├── 02_create_staging.sql      # Staging tables DDL
│   ├── 03_create_oltp.sql         # 3NF Operational schema
│   ├── 04_create_dw.sql           # Kimball Star Schema dimensional DDL
│   ├── 05_etl_staging_to_oltp.sql # Staging to OLTP transformation
│   ├── 06_etl_oltp_to_dw.sql      # OLTP to OLAP warehouse ETL procedures
│   ├── 07_dashboard_queries.sql   # Analytical SQL views (LAG, DENSE_RANK)
│   ├── 08_create_indexes.sql      # B-tree performance indexing
│   ├── 09_alter_fixed_columns.sql # Fixed CHAR column alignments
│   └── 10_incremental_loads.sql   # Delta sync and reconciliation views
├── diagrams/                      # System architecture and ER models
│   ├── olap_star_schema.drawio    # Star Schema diagram file
│   └── oltp_er_diagram.drawio     # OLTP Relational schema diagram
├── etl/                           # Python ETL orchestration scripts
│   ├── load_staging.py            # Chunked CSV staging loader
│   └── run_pipeline.py            # Automated end-to-end database pipeline runner
├── src/                           # Core Application Backend & DAL
│   ├── dal/                       # Data Access Layer (DAL)
│   │   ├── analytics_dal.py       # Analytical view queries & reconciliation
│   │   ├── employee_dal.py        # Employee CRUD & SCD2 transfer invocations
│   │   ├── project_dal.py         # Project management & allocation guard
│   │   └── review_dal.py          # Appraisal submission & duplicate checks
│   ├── models/                    # Domain entity data classes
│   ├── db_manager.py              # Singleton connection pool & Base Manager
│   ├── exceptions.py              # Custom typed application exception hierarchy
│   ├── notifications.py           # SMTP mailer for project allocations
│   ├── rbac.py                    # Role-Based Access Control logic & permissions
│   └── validation.py              # Field validators & input sanitizers
├── app.py                         # Streamlit presentation layer & UI
├── requirements.txt               # Pinned project dependencies
└── synthetic_data_generator.py    # Deterministic HR dataset synthesizer
```

---

## 🛠️ Getting Started & Installation

### Prerequisites:
* **MySQL Server 8.0+** running locally (or remote instance)
* **Python 3.11+**
* An active MySQL user with privileges to create databases

### 1. Clone the Repository
```bash
git clone https://github.com/PratikModi22/SkillFlow.git
cd SkillFlow
```

### 2. Set Up Virtual Environment
```bash
python -m venv env

# Windows PowerShell:
.\env\Scripts\Activate.ps1

# macOS / Linux:
# source env/bin/activate
```

### 3. Install Dependencies
```bash
pip install -r requirements.txt
```

### 4. Configure Environment Variables
Create a `.env` file in the project root:
```ini
DB_HOST=localhost
DB_PORT=3306
DB_USER=root
DB_PASS=your_mysql_password
DB_NAME=employee_analytics_dw
DATABASE_URL=mysql+pymysql://root:your_mysql_password@localhost:3306/employee_analytics_dw
```

### 5. Run Database Migration & Pipeline
Generate synthetic data and populate the database:
```bash
# Generate synthetic dataset:
python synthetic_data_generator.py

# Execute full pipeline (staging -> OLTP -> Star Schema):
python etl/run_pipeline.py --reset
```

### 6. Launch Application
```bash
streamlit run app.py
```
Open your browser at `http://localhost:8501` to access the dashboard.

---

## 🔄 Git Flow & Collaboration

This repository follows standard **Git Flow** conventions:

* **`main`**: Production-ready, stable releases.
* **`develop`**: Integration branch for active development.
* **`feature/*`**: Individual features or bug fixes.

```bash
# Branching for new contributions
git checkout develop
git checkout -b feature/custom-attrition-metric

# Commit and push
git add .
git commit -m "feat: implement attrition predictive risk model"
git push -u origin feature/custom-attrition-metric
```

---

## 👤 Author & Maintainer

* **Developer:** [Pratik Modi](https://github.com/PratikModi22)
* **Repository:** [https://github.com/PratikModi22/SkillFlow](https://github.com/PratikModi22/SkillFlow)
* **Project:** SkillFlow — Enterprise Workforce Intelligence & Data Warehouse

---

## 📄 License

This project is licensed under the MIT License.
