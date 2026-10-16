"""
Staging Data Loader
===================
Loads the synthesized CSV files from /synthetic_data into the `stg_*`
staging tables of the `employee_analytics_dw` MySQL database.

Design notes
------------
* Only columns that actually exist on the target table are inserted. The
  synthesizer emits descriptive extras (EmployeeNumber, Age, Attrition,
  ChangeReason ...) that are not part of the staging contract.
* Dates arrive in mixed formats (DD-MM-YYYY in most files, ISO YYYY-MM-DD in
  performance_reviews.csv). They are normalised before hitting MySQL.
* Every run is wrapped in a control batch row in stg_load_batches.
"""

import os
import sys
from datetime import datetime

import pandas as pd
from sqlalchemy import create_engine, text
import os

# --------------------------------------------------------------------------
# Configuration (override with environment variables)
# --------------------------------------------------------------------------

DB_URL = os.getenv("DATABASE_URL")

DATA_DIR = os.getenv(
    "SYNTHETIC_DATA_DIR",
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "synthetic_data",
    ),
)

CHUNK_SIZE = 1000
SOURCE_SYSTEM = "Synthetic Data Generator"

engine = create_engine(DB_URL, pool_pre_ping=True, pool_recycle=3600)

# file -> (staging table, columns that must be parsed as dates)
FILES = {
    "departments.csv": ("stg_departments", []),
    "employees.csv": (
        "stg_employees",
        ["HireDate", "TerminationDate"],
    ),
    "employee_history.csv": (
        "stg_employee_history",
        [
            "HireDate",
            "TerminationDate",
            "EffectiveStartDate",
            "EffectiveEndDate",
        ],
    ),
    "projects.csv": (
        "stg_projects",
        ["ProjectStartDate", "ProjectEndDate"],
    ),
    "employee_project_assignments.csv": (
        "stg_employee_project_assignments",
        ["AssignmentStartDate", "AssignmentEndDate"],
    ),
    "performance_reviews.csv": (
        "stg_performance_reviews",
        ["ReviewDate"],
    ),
}


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def get_table_columns(table_name):
    """Return the list of real column names on a staging table."""
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT COLUMN_NAME
                FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME = :table_name
                ORDER BY ORDINAL_POSITION
                """
            ),
            {"table_name": table_name},
        ).fetchall()
    return [r[0] for r in rows]


def normalise_dates(series):
    """Parse mixed DD-MM-YYYY / YYYY-MM-DD strings into datetime."""
    cleaned = series.astype("string").str.strip()
    cleaned = cleaned.mask(cleaned.isin(["", "nan", "NaT", "None"]), pd.NA)

    day_first = pd.to_datetime(cleaned, format="%d-%m-%Y", errors="coerce")
    iso = pd.to_datetime(cleaned, format="%Y-%m-%d", errors="coerce")

    parsed = day_first.fillna(iso)
    return parsed.dt.date.astype("object").where(parsed.notna(), None)


def create_batch():
    """Open a control-batch row and return its surrogate id."""
    with engine.begin() as connection:
        result = connection.execute(
            text(
                """
                INSERT INTO stg_load_batches
                    (SourceSystem, BatchStatus)
                VALUES (:source, 'RUNNING')
                """
            ),
            {"source": SOURCE_SYSTEM},
        )
        return result.lastrowid


def close_batch(batch_id, status, error=None):
    """Stamp the control-batch row with its final outcome."""
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                UPDATE stg_load_batches
                SET BatchEndTime = CURRENT_TIMESTAMP,
                    BatchStatus  = :status,
                    ErrorMessage = :error
                WHERE LoadBatchID = :batch_id
                """
            ),
            {"batch_id": batch_id, "status": status, "error": error},
        )


def load_file(file_name, table_name, date_columns, batch_id):
    """Truncate a staging table and reload it from one CSV file."""
    file_path = os.path.join(DATA_DIR, file_name)

    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Source file not found: {file_path}")

    target_columns = get_table_columns(table_name)
    if not target_columns:
        raise RuntimeError(
            f"Table '{table_name}' does not exist. "
            "Run database/01_create_database.sql and "
            "database/02_create_staging.sql first."
        )

    df = pd.read_csv(file_path, low_memory=False)

    # Keep only columns the staging table actually stores.
    keep = [c for c in target_columns if c in df.columns]
    dropped = [c for c in df.columns if c not in target_columns]
    df = df[keep]

    for column in date_columns:
        if column in df.columns:
            df[column] = normalise_dates(df[column])

    df["LoadBatchID"] = batch_id
    df["SourceFileName"] = file_name
    df["LoadedAt"] = datetime.now()

    # NULL instead of NaN, and proper Python types for the driver.
    df = df.astype(object).where(pd.notna(df), None)

    with engine.begin() as connection:
        connection.execute(text(f"TRUNCATE TABLE {table_name}"))

    df.to_sql(
        table_name,
        engine,
        if_exists="append",
        index=False,
        chunksize=CHUNK_SIZE,
        method="multi",
    )

    note = f"  (skipped columns: {', '.join(dropped)})" if dropped else ""
    return len(df), note


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------


def main():
    missing = [f for f in FILES if not os.path.exists(os.path.join(DATA_DIR, f))]
    if missing:
        print(f"ERROR: missing source files in {DATA_DIR}: {missing}")
        return 1

    batch_id = create_batch()
    print(f"Starting staging batch: {batch_id}\n")

    try:
        total_rows = 0

        for file_name, (table_name, date_columns) in FILES.items():
            print(f"  {file_name:<38} -> {table_name}")
            count, note = load_file(file_name, table_name, date_columns, batch_id)
            print(f"    {count:,} rows loaded{note}\n")
            total_rows += count

        close_batch(batch_id, "SUCCESS")
        print(f"Staging load SUCCESSFUL. Batch {batch_id}, {total_rows:,} rows.")

    except Exception as exc:
        close_batch(batch_id, "FAILED", str(exc))
        print(f"\nStaging load FAILED. Batch {batch_id}\n{exc}")
        raise

    return 0


if __name__ == "__main__":
    sys.exit(main())
