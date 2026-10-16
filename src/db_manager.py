"""
Database Connection Management & Engine Infrastructure
======================================================
READING SEQUENCE STEP 3: Understand how Python connects to MySQL.

This module provides the central database infrastructure for the entire project.
It implements the Singleton pattern so that Streamlit (which re-runs its script on
every user click) reuses exactly one connection pool instead of exhausting MySQL sockets.

Architectural Layering:
-----------------------
1. DatabaseConnection  : Low-level engine owner, connection pooler, transaction coordinator.
2. BaseDataManager     : Reusable abstract parent for all Data Access Layer (DAL) managers
                         (EmployeeManager, ProjectManager, ReviewManager, AnalyticsManager).
3. translate_db_error  : Translates low-level MySQL driver C-errors (1644, 1062) into readable
                         Python exceptions that can be displayed safely on the UI.
"""

from __future__ import annotations

import logging
import os
import re
import threading
import urllib.parse
from contextlib import contextmanager
from decimal import Decimal
from typing import Any, Iterator, Mapping, Sequence

import pandas as pd
import pymysql

try:
    pymysql.install_as_MySQLdb()
except Exception:
    pass

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from .exceptions import (
    AppError,
    ConnectionFailedError,
    DataWarehouseError,
    DuplicateRecordError,
    QueryError,
)

# Configure module-level logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger(__name__)

# Default connection strings
LOCAL_DB_URL_DEFAULT = os.getenv("LOCAL_DATABASE_URL", "mysql+pymysql://root:12345@127.0.0.1:3306/employee_analytics_dw")
DEFAULT_DB_URL = os.getenv("DATABASE_URL") or LOCAL_DB_URL_DEFAULT


def resolve_database_url(explicit_url: str | None = None) -> str:
    """
    [CORE / IMPORTANT FUNCTION]
    Resolves the MySQL connection string in strict priority order:
    1. Explicit URL parameter (if provided)
    2. Streamlit secrets (.streamlit/secrets.toml or Streamlit Community Cloud Secrets)
    3. OS Environment variable (DATABASE_URL)
    4. Project default fallback (LOCAL_DB_URL_DEFAULT)

    Returns:
    --------
    str
        Active MySQL database URL connection string.
    """
    if explicit_url and explicit_url.strip():
        return explicit_url.strip()

    # 1. Safely inspect Streamlit secrets if running inside Streamlit
    try:
        import streamlit as st
        if hasattr(st, "secrets") and "DATABASE_URL" in st.secrets:
            val = st.secrets["DATABASE_URL"]
            if val and str(val).strip():
                return str(val).strip()
    except Exception:
        pass

    # 2. Inspect OS environment variable
    env_url = os.getenv("DATABASE_URL")
    if env_url and env_url.strip():
        return env_url.strip()

    # 3. Default fallback
    return DEFAULT_DB_URL.strip() if DEFAULT_DB_URL else ""


def _prepare_engine_url_and_args(raw_url: str) -> tuple[str, dict[str, Any]]:
    """
    [HELPER / UTILITY FUNCTION]
    Normalizes database URL and configures SSL connect_args for PyMySQL compatibility.

    Why this is needed:
    -------------------
    PyMySQL's `connect()` does not accept `ssl_mode` or `ssl-mode` query parameters
    (common in cloud MySQL URIs such as Aiven, AWS RDS, DigitalOcean).
    Passing them causes PyMySQL to raise:
        TypeError: Connection.__init__() got an unexpected keyword argument 'ssl_mode'
    This helper strips those driver-incompatible URL query parameters and translates
    them into PyMySQL's expected `connect_args={"ssl": {"check_hostname": False}}`
    for secure cloud database connectivity.
    """
    if not raw_url:
        return raw_url, {}

    # Automatically map mysql:// to mysql+pymysql:// to prevent No module named 'MySQLdb'
    if raw_url.startswith("mysql://"):
        raw_url = "mysql+pymysql://" + raw_url[len("mysql://") :]

    parsed = urllib.parse.urlparse(raw_url)
    query_params = urllib.parse.parse_qs(parsed.query)

    # Detect if connecting to a remote cloud host or if SSL was requested
    needs_ssl = (
        any(k in query_params for k in ("ssl", "ssl_mode", "ssl-mode"))
        or (parsed.hostname and parsed.hostname not in ("localhost", "127.0.0.1"))
    )

    # Strip query parameters not directly accepted by pymysql
    for k in ("ssl_mode", "ssl-mode"):
        query_params.pop(k, None)

    # Reconstruct query string
    clean_query = urllib.parse.urlencode(
        [(k, val) for k, vals in query_params.items() for val in vals]
    )
    clean_url = urllib.parse.urlunparse(parsed._replace(query=clean_query))

    connect_args: dict[str, Any] = {}
    if needs_ssl:
        connect_args["ssl"] = {"check_hostname": False}

    return clean_url, connect_args


# ============================================================================
# [DATA TYPE CONVERSION HELPERS]
# ============================================================================

def _coerce_decimals(frame: pd.DataFrame) -> pd.DataFrame:
    """
    [HELPER / UTILITY FUNCTION]
    Converts MySQL DECIMAL fields from Python `Decimal` objects to native float64.

    Why this is necessary:
    ----------------------
    The MySQL database driver (PyMySQL) returns SQL DECIMAL columns as Python
    `decimal.Decimal` objects. When loaded into a pandas DataFrame, pandas marks the
    column type as generic `object`. This breaks numeric sorting, nsmallest/nlargest,
    and Plotly interactive charts. This helper inspects columns and coerces pure Decimal
    columns into floats once, at the lowest data-fetching layer.

    Parameters:
    -----------
    frame : pd.DataFrame
        Raw DataFrame returned by SQLAlchemy execution.

    Returns:
    --------
    pd.DataFrame
        DataFrame with DECIMAL columns coerced to standard float64.
    """
    for column in frame.columns:
        series = frame[column]
        if series.dtype != object or series.empty:
            continue
        non_null = series.dropna()
        if len(non_null) and all(isinstance(v, Decimal) for v in non_null):
            frame[column] = series.astype(float)
    return frame


# ============================================================================
# [DATABASE DRIVER ERROR TRANSLATION]
# ============================================================================

# Regular expression to extract the numeric MySQL error code and inner error message
# Handles both single and double quotes returned by PyMySQL / SQLAlchemy:
# Example: (1062, "Duplicate entry 'EMP-1' for key 'PRIMARY'")
# Example: (1644, 'Change date 2026-10-03 precedes the current version')
_DRIVER_ERROR = re.compile(r"\((\d+),\s*(['\"])(.*?)\2\)", re.S)


def translate_db_error(exc: SQLAlchemyError) -> AppError:
    """
    [CORE / IMPORTANT FUNCTION]
    Translates raw MySQL / SQLAlchemy database errors into user-friendly typed exceptions.

    Why this is critical for the application:
    -----------------------------------------
    When a database stored procedure signals a business rule violation using:
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Change date precedes current version';
    MySQL returns error code 1644. If left unhandled, SQLAlchemy would show a giant,
    ugly traceback with raw SQL on the Streamlit screen.
    This function intercepts the error, extracts the exact business error string,
    and maps it to a clean `DataWarehouseError` or `DuplicateRecordError`.

    Error Code Mapping:
    -------------------
    - Code 1644 / 45000 -> DataWarehouseError (Business rule rejected by stored procedure)
    - Code 1062         -> DuplicateRecordError (Unique key / primary key violation)
    - Any other code    -> QueryError (Generic SQL execution failure)

    Parameters:
    -----------
    exc : SQLAlchemyError
        The raw exception caught from SQLAlchemy.

    Returns:
    --------
    AppError
        The corresponding custom application exception ready for UI display.
    """
    raw = str(getattr(exc, "orig", None) or exc)
    match = _DRIVER_ERROR.search(raw)
    code, message = (match.group(1), match.group(3)) if match else ("", raw)
    message = message.strip() or raw

    if code in ("1644", "45000"):
        return DataWarehouseError(message)
    if code == "1062":
        return DuplicateRecordError(message)
    return QueryError(f"Statement failed: {raw}")


# ============================================================================
# [SINGLETON CONNECTION POOL MANAGER]
# ============================================================================

class DatabaseConnection:
    """
    [CORE INFRASTRUCTURE CLASS - THREAD-SAFE SINGLETON]
    Central manager of SQLAlchemy database engines and connection pools.

    Design Pattern:
    ---------------
    Guarantees that exactly ONE engine and connection pool exists across the
    entire application lifetime, avoiding socket exhaustion during Streamlit reruns.
    """

    _instance: "DatabaseConnection" | None = None
    _lock: threading.Lock = threading.Lock()

    def __new__(
        cls, db_url: str | None = None, *args: Any, **kwargs: Any
    ) -> "DatabaseConnection":
        """
        [CORE / IMPORTANT FUNCTION]
        Guarantees that only a single DatabaseConnection instance exists.
        Uses double-checked locking with a thread lock to prevent race conditions.
        """
        with cls._lock:
            if cls._instance is None:
                inst = super().__new__(cls)
                inst._initialised = False
                cls._instance = inst
        return cls._instance

    def __init__(
        self, db_url: str | None = None, *args: Any, **kwargs: Any
    ) -> None:
        """
        [CORE / IMPORTANT FUNCTION]
        Initializes the connection configuration once for the singleton.

        Parameters:
        -----------
        db_url : str | None
            Optional explicit database URI override.
        """
        target_url = resolve_database_url(db_url)
        if getattr(self, "_initialised", False):
            # If a new, non-empty db_url is supplied that differs from the active one, re-bind
            if target_url and target_url != self.db_url:
                if self._engine is not None:
                    try:
                        self._engine.dispose()
                    except Exception:
                        pass
                    self._engine = None
                self.db_url = target_url
                self._last_error = None
            return

        self.db_url: str = target_url
        self._engine: Engine | None = None
        self._last_error: str | None = None
        self._initialised = True

        logger.info("DatabaseConnection singleton ready")

    @classmethod
    def for_environment(
        cls, profile: str = "default", db_url: str | None = None
    ) -> "DatabaseConnection":
        """Backward-compatible helper returning the singleton instance."""
        return cls(db_url=db_url)

    @classmethod
    def get_local(cls, db_url: str | None = None) -> "DatabaseConnection":
        """Backward-compatible helper returning the singleton instance."""
        return cls(db_url=db_url)

    @classmethod
    def get_hosted(cls, db_url: str | None = None) -> "DatabaseConnection":
        """Backward-compatible helper returning the singleton instance."""
        return cls(db_url=db_url)

    @classmethod
    def reset(cls, profile: str | None = None) -> None:
        """Disposes and removes singleton instance."""
        with cls._lock:
            if cls._instance is not None:
                if cls._instance._engine is not None:
                    try:
                        cls._instance._engine.dispose()
                    except Exception:
                        pass
                cls._instance = None

    # ------------------------------------------------------------------
    # Engine Lifecycle & Diagnostics
    # ------------------------------------------------------------------

    @property
    def engine(self) -> Engine:
        """
        [CORE / IMPORTANT FUNCTION]
        Lazily creates and returns the pooled SQLAlchemy Engine on first access.

        Connection Pool Configuration:
        -----------------------------
        - pool_pre_ping=True : Tests connection liveness with a "ping" before handing it
                               to the caller; auto-reconnects if MySQL restarted.
        - pool_recycle=3600  : Recycles connections older than 1 hour to avoid MySQL timeout drops.
        - pool_size=5        : Keeps 5 steady connections warm in memory.
        - max_overflow=10    : Allows up to 10 additional temporary spike connections.
        - future=True        : Uses modern SQLAlchemy 2.0 transaction execution semantics.
        """
        if self._engine is None:
            if not self.db_url:
                msg = (
                    "No database connection URL configured. Set DATABASE_URL in "
                    ".streamlit/secrets.toml or as an environment variable."
                )
                self._last_error = msg
                raise ConnectionFailedError(msg)
            try:
                clean_url, connect_args = _prepare_engine_url_and_args(self.db_url)
                self._engine = create_engine(
                    clean_url,
                    connect_args=connect_args,
                    pool_pre_ping=True,
                    pool_recycle=3600,
                    pool_size=5,
                    max_overflow=10,
                    future=True,
                )
                logger.info("MySQL engine created")
            except Exception as exc:
                self._last_error = str(exc)
                if isinstance(exc, SQLAlchemyError):
                    raise ConnectionFailedError(
                        f"Could not create the MySQL engine: {exc}"
                    ) from exc
                raise
        return self._engine

    def ping(self) -> bool:
        """
        [HELPER / UTILITY FUNCTION]
        Lightweight health check probe executed by the Streamlit sidebar.

        Runs `SELECT 1 AS ok` against MySQL. Never throws an exception;
        returns True if alive, False if unreachable.
        """
        try:
            self.query_frame("SELECT 1 AS ok")
            self._last_error = None
            return True
        except Exception as exc:  # noqa: BLE001 - probe must never crash
            self._last_error = str(exc)
            logger.warning("Ping failed: %s", exc)
            return False

    def get_last_error(self) -> str | None:
        """Returns the last recorded connection or query error message."""
        return getattr(self, "_last_error", None)

    # ------------------------------------------------------------------
    # Query Execution Methods
    # ------------------------------------------------------------------

    def query_frame(
        self,
        sql: str,
        params: Mapping[str, Any] | None = None,
    ) -> pd.DataFrame:
        """
        [CORE / IMPORTANT FUNCTION]
        Executes a SELECT SQL query and returns the results directly as a pandas DataFrame.

        Parameters:
        -----------
        sql : str
            Parameterized SQL statement (e.g. `SELECT * FROM employees WHERE DepartmentID = :dept`).
        params : Mapping[str, Any] | None
            Named parameter bindings dictionary to prevent SQL injection.

        Returns:
        --------
        pd.DataFrame
            The tabular query result set with numeric Decimal columns converted to floats.
        """
        try:
            with self.engine.connect() as conn:
                result = conn.execute(text(sql), params or {})
                frame = pd.DataFrame(result.fetchall(), columns=result.keys())
        except SQLAlchemyError as exc:
            logger.error("Query failed: %s", exc)
            raise QueryError(f"Query failed: {exc}") from exc
        return _coerce_decimals(frame)

    def query_scalar(
        self,
        sql: str,
        params: Mapping[str, Any] | None = None,
        default: Any = None,
    ) -> Any:
        """
        [HELPER / UTILITY FUNCTION]
        Executes a query and returns the single value in the first row, first column.

        Ideal for aggregate queries like `SELECT COUNT(*)`, `SELECT MAX(id)`, etc.
        Returns `default` if the query returns 0 rows.
        """
        frame = self.query_frame(sql, params)
        if frame.empty:
            return default
        return frame.iloc[0, 0]

    def execute(
        self,
        sql: str,
        params: Mapping[str, Any] | None = None,
    ) -> int:
        """
        [CORE / IMPORTANT FUNCTION]
        Executes a single write query (INSERT, UPDATE, DELETE) inside an atomic transaction.

        Features:
        ---------
        - Uses `engine.begin()` so the transaction auto-commits on success or rolls back on error.
        - Translates driver exceptions using `translate_db_error` for user-safe feedback.

        Returns:
        --------
        int
            Number of database rows affected by the statement.
        """
        try:
            with self.engine.begin() as conn:
                result = conn.execute(text(sql), params or {})
                return result.rowcount
        except SQLAlchemyError as exc:
            logger.error("Statement failed: %s", exc)
            raise translate_db_error(exc) from exc

    def _call(self, procedure: str, *args: Any) -> tuple[list[str], list[tuple]]:
        """
        [HELPER / UTILITY FUNCTION]
        Low-level driver mechanism to execute stored procedures with multiple result sets.

        Why this is needed:
        -------------------
        Procedures like `sp_load_oltp_master` perform internal SELECTs before the final summary.
        MySQL drivers buffer each result set. This helper iterates through `cursor.nextset()`,
        draining intermediate outputs so the socket stays clean, and extracts the last
        meaningful non-empty result set containing the procedure's output report.
        """
        placeholders = ", ".join(["%s"] * len(args))
        try:
            with self.engine.begin() as conn:
                cursor = conn.exec_driver_sql(
                    f"CALL {procedure}({placeholders})", tuple(args)
                ).cursor
                columns: list[str] = []
                rows: list[tuple] = []
                while True:
                    description = cursor.description
                    current = cursor.fetchall()
                    if current:
                        columns = [d[0] for d in description or []]
                        rows = current
                    if not cursor.nextset():
                        break
                return columns, rows
        except SQLAlchemyError as exc:
            logger.error("Procedure %s failed: %s", procedure, exc)
            raise QueryError(f"Procedure {procedure} failed: {exc}") from exc

    def call_procedure(self, procedure: str, *args: Any) -> list[tuple]:
        """
        [CORE / IMPORTANT FUNCTION]
        Calls a MySQL stored procedure and returns its final report rows as raw tuples.

        Parameters:
        -----------
        procedure : str
            Name of the stored procedure (e.g. 'sp_load_oltp_master', 'sp_load_dw_master').
        *args : Any
            Positional input arguments required by the procedure.

        Returns:
        --------
        list[tuple]
            List of result row tuples from the final report query.
        """
        return self._call(procedure, *args)[1]

    def call_procedure_dict(self, procedure: str, *args: Any) -> dict:
        """
        [CORE / IMPORTANT FUNCTION]
        Calls a MySQL stored procedure and returns its final report row as a key-value dictionary.

        Enables callers to access summary metrics cleanly:
        `report.get('employees_versioned', 0)`.
        """
        columns, rows = self._call(procedure, *args)
        if not rows or not columns:
            return {}
        return dict(zip(columns, rows[0]))

    @contextmanager
    def transaction(self) -> Iterator[Any]:
        """
        [CORE / IMPORTANT FUNCTION]
        Context manager for multi-statement atomic transactions.

        Usage Pattern:
        --------------
            with db.transaction() as conn:
                conn.execute(text("UPDATE employees ..."))
                conn.execute(text("CALL sp_apply_employee_change(...)"))

        If any step throws an error, the entire transaction is rolled back,
        preventing desynchronization between OLTP and Data Warehouse dimensions.
        """
        conn = self.engine.connect()
        trans = conn.begin()
        try:
            yield conn
            trans.commit()
        except Exception:
            trans.rollback()
            raise
        finally:
            conn.close()

    def dispose(self) -> None:
        """
        [HELPER / UTILITY FUNCTION]
        Gracefully closes all open connections in the connection pool.
        Used during application shutdown and unit testing.
        """
        if self._engine is not None:
            self._engine.dispose()
            self._engine = None
            logger.info("MySQL engine disposed")

    @classmethod
    def reset(cls) -> None:
        """
        [HELPER / UTILITY FUNCTION]
        Resets the singleton instance reference so that it can be cleanly rebuilt.
        """
        with cls._lock:
            if cls._instance is not None:
                cls._instance.dispose()
                cls._instance = None


# ============================================================================
# [BASE DATA ACCESS LAYER (DAL) CLASS]
# ============================================================================

class BaseDataManager:
    """
    [CORE ARCHITECTURAL BASE CLASS - DATA ACCESS LAYER FOUNDATION]
    Common base class inherited by all entity managers:
    - EmployeeManager
    - ProjectManager
    - ReviewManager
    - AnalyticsManager

    Provides unified access to the Singleton database connection and standard
    CRUD helper methods (fetch_frame, fetch_one, fetch_value, run, run_many).
    """

    def __init__(self, connection: DatabaseConnection | None = None) -> None:
        """
        [HELPER / UTILITY FUNCTION]
        Initializes the manager with the shared Singleton DatabaseConnection.
        """
        self.db: DatabaseConnection = connection or DatabaseConnection()

    # -- Read Query Helpers ---------------------------------------------

    def fetch_frame(self, sql: str, params: Mapping[str, Any] | None = None) -> pd.DataFrame:
        """
        [CORE / IMPORTANT FUNCTION]
        Executes a SQL SELECT statement and returns a pandas DataFrame.
        """
        return self.db.query_frame(sql, params)

    def fetch_one(self, sql: str, params: Mapping[str, Any] | None = None) -> dict | None:
        """
        [HELPER / UTILITY FUNCTION]
        Fetches the first matching record as a Python dictionary, or None if empty.
        """
        frame = self.db.query_frame(sql, params)
        if frame.empty:
            return None
        return frame.iloc[0].to_dict()

    def fetch_value(self, sql: str, params: Mapping[str, Any] | None = None,
                    default: Any = None) -> Any:
        """
        [HELPER / UTILITY FUNCTION]
        Fetches a single scalar value from the first row and first column.
        """
        return self.db.query_scalar(sql, params, default)

    def fetch_dicts(self, sql: str, params: Mapping[str, Any] | None = None) -> list[dict]:
        """
        [HELPER / UTILITY FUNCTION]
        Fetches all matching records as a list of dictionaries (one dict per row).
        """
        return self.fetch_frame(sql, params).to_dict("records")

    # -- Write Query Helpers --------------------------------------------

    def run(self, sql: str, params: Mapping[str, Any] | None = None) -> int:
        """
        [CORE / IMPORTANT FUNCTION]
        Executes a single write statement (INSERT/UPDATE/DELETE) and returns affected row count.
        """
        return self.db.execute(sql, params)

    def run_many(self, statements: Sequence[tuple[str, Mapping[str, Any]]]) -> int:
        """
        [CORE / IMPORTANT FUNCTION]
        Executes a sequence of SQL statements inside a single atomic transaction.
        All statements succeed together or roll back together.
        """
        affected = 0
        with self.db.transaction() as conn:
            for sql, params in statements:
                affected += conn.execute(text(sql), params).rowcount
        return affected

    # -- Diagnostics & Health -------------------------------------------

    def is_alive(self) -> bool:
        """
        [HELPER / UTILITY FUNCTION]
        Performs a rapid health probe (`SELECT 1`) to verify database connectivity.
        """
        try:
            val = bool(self.fetch_value("SELECT 1", default=0))
            if val:
                self.db._last_error = None
            return val
        except Exception as exc:
            self.db._last_error = str(exc)
            logger.warning("Database probe failed: %s", exc)
            return False

    def get_last_error(self) -> str | None:
        """
        [HELPER / UTILITY FUNCTION]
        Returns the last recorded database connection or query error message.
        """
        return getattr(self.db, "_last_error", None)


__all__ = [
    "DatabaseConnection",
    "BaseDataManager",
    "DEFAULT_DB_URL",
    "resolve_database_url",
    "translate_db_error",
    "_prepare_engine_url_and_args",
]