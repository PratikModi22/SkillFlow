"""
Enterprise Employee Analytics & Data Warehouse System
=====================================================
RECOMMENDED READING SEQUENCE FOR STUDENTS & NEW GRADUATES:

1. `src/exceptions.py`
   - Defines custom typed error hierarchy (`AppError`, `ValidationError`, `DataWarehouseError`).
   - Teaches how applications handle and isolate database errors without crashing.

2. `src/validation.py`
   - Defines regex patterns, column length constraints, and user input validation functions.
   - Teaches input sanitation, localization, and multi-field form accumulation (`FormValidator`).

3. `src/db_manager.py`
   - Singleton connection manager (`DatabaseConnection`) and base manager (`BaseDataManager`).
   - Teaches SQLAlchemy connection pooling, thread locks, decimal coercion, and atomic transactions.

4. Domain Models (`src/models/`):
   - `src/models/employee.py` : Employee dataclass, properties, and validation.
   - `src/models/project.py`  : Project dataclass and overload detection.
   - `src/models/review.py`   : Performance review dataclass, scoring bands, and promotion rules.

5. Data Access Layer (`src/dal/`):
   - `src/dal/employee_dal.py`  : OLTP CRUD, dual-write to DW, and SCD Type 2 department transfers.
   - `src/dal/project_dal.py`   : Project CRUD, employee allocations, and 100% capacity guard.
   - `src/dal/review_dal.py`    : Performance review submissions and 1-per-day duplicate rules.
   - `src/dal/analytics_dal.py` : Queries OLAP Star Schema views (LAG, DENSE_RANK), reconciliation, and ETL.

6. Notifications (`src/notifications.py`):
   - Non-blocking email notifications sent on project allocation via SMTP.

7. Streamlit Presentation Layer (`app.py`):
   - UI forms, interactive Plotly dashboards, drill-downs, and data warehouse health panels.
"""

from .exceptions import (
    AppError,
    ConnectionFailedError,
    DataAccessError,
    DataWarehouseError,
    DatabaseError,
    DuplicateRecordError,
    NotFoundError,
    QueryError,
    ValidationError,
)
from .validation import (
    FormValidator,
    validate_age,
    validate_digits,
    validate_email,
    validate_identifier,
    validate_name,
    validate_number,
    validate_phone,
    validate_role,
    validate_text,
)

from .rbac import DEMO_USERS, RBAC, Permission, User, UserRole

__version__ = "2.1.0"

__all__ = [
    "AppError",
    "DatabaseError",
    "ConnectionFailedError",
    "QueryError",
    "DataAccessError",
    "NotFoundError",
    "DuplicateRecordError",
    "ValidationError",
    "DataWarehouseError",
    "FormValidator",
    "validate_text",
    "validate_name",
    "validate_role",
    "validate_email",
    "validate_phone",
    "validate_digits",
    "validate_age",
    "validate_number",
    "validate_identifier",
    "UserRole",
    "Permission",
    "User",
    "DEMO_USERS",
    "RBAC",
    "__version__",
]