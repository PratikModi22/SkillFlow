"""
Data Access Layer (DAL) Package
===============================
READING SEQUENCE STEP 5 (Index): Database Managers & Business Logic.

Re-exports the specialized manager classes wrapping the OLTP tables and OLAP Star Schema.

Managers Summary:
-----------------
- EmployeeManager   : OLTP Employee CRUD, atomic dual-write, and SCD Type 2 department transfers.
- ProjectManager    : Project CRUD, allocation bookings, and capacity-ceiling guards (<= 100%).
- ReviewManager     : Appraisal reviews CRUD and 1-per-day collision validation.
- AnalyticsManager  : OLAP Star Schema querying, window functions (LAG, DENSE_RANK),
                      SCD2 auditing, reconciliation health, and ETL triggers.
"""

from .analytics_dal import AnalyticsManager
from .employee_dal import EmployeeManager
from .project_dal import ProjectManager
from .review_dal import ReviewManager

__all__ = [
    "EmployeeManager",
    "ProjectManager",
    "ReviewManager",
    "AnalyticsManager",
]