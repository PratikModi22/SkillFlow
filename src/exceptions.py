"""
Custom Application Exceptions
=============================
READING SEQUENCE STEP 1: Understand what can fail.

A clean typed exception hierarchy so callers (especially the Data Access Layer and Streamlit UI)
can distinguish between different failure modes instead of catching bare Exception.
"""

from __future__ import annotations


# ============================================================================
# [BASE ROOT ERROR]
# ============================================================================

class AppError(Exception):
    """
    [BASE ROOT EXCEPTION]
    Base class for every custom exception raised by this package.
    Catching `AppError` in `app.py` allows the UI to catch any internal application
    failure safely without crashing the Streamlit session.
    """


# ============================================================================
# [DATABASE INFRASTRUCTURE ERRORS] - Database & Connection Failures
# ============================================================================

class DatabaseError(AppError):
    """
    [DATABASE ERROR]
    Parent error for any failure occurring during communication with the MySQL database.
    """


class ConnectionFailedError(DatabaseError):
    """
    [CRITICAL INFRASTRUCTURE ERROR]
    Raised when the MySQL server cannot be reached, the port is closed,
    or authentication credentials (user/password/host) are rejected.
    """


class QueryError(DatabaseError):
    """
    [EXECUTION ERROR]
    Raised when a specific SQL statement or stored procedure execution fails
    (e.g., syntax error, constraint failure, or driver execution error).
    """


# ============================================================================
# [DATA ACCESS LAYER (DAL) ERRORS] - Business & Entity Failures
# ============================================================================

class DataAccessError(AppError):
    """
    [DAL BASE ERROR]
    Parent class for errors raised by Manager classes (EmployeeManager, ProjectManager, etc.).
    """


class NotFoundError(DataAccessError):
    """
    [LOOKUP ERROR - IMPORTANT]
    Raised when an operation requests an entity that does not exist in the database
    (e.g., querying Employee 'EMP-9999999' which has no record).
    """


class DuplicateRecordError(DataAccessError):
    """
    [CONSTRAINT ERROR - IMPORTANT]
    Raised when an INSERT would violate a unique key constraint
    (e.g., duplicate email address, duplicate EmployeeID, or duplicate daily review).
    """


class ValidationError(AppError):
    """
    [BUSINESS RULE ERROR - IMPORTANT]
    Raised when an input value fails validation BEFORE hitting the database
    (e.g., invalid email regex, negative salary, score > 100, or allocation > 100%).
    """


class DataWarehouseError(AppError):
    """
    [ANALYTICAL REJECTION ERROR - IMPORTANT]
    Raised when the OLAP data warehouse rejects an operation.
    Specifically raised when a stored procedure issues SIGNAL SQLSTATE '45000'
    (e.g., attempting a department transfer backdated before an existing SCD2 version).
    """


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
]