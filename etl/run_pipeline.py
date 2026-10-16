"""
One-command pipeline runner.

Why this exists
---------------
Running the .sql files only *defines* tables, procedures and views. The
`CALL sp_load_*` statements at the bottom of 05 and 06 sit INSIDE the
procedure bodies, so nothing moves from staging to OLTP, or from OLTP to
OLAP, until those procedures are actually invoked. It is very easy to run
every script, see staging filled up, and end up with an empty warehouse.

This script does the whole thing in the right order, including the CALLs.

Usage
-----
    python etl/run_pipeline.py
        Runs scripts 02-07 and 10, loads staging if it is empty, then runs
        sp_load_oltp_master() and sp_load_dw_master(), converts the
        fixed-width keys, then tunes the OLAP indexes.

    python etl/run_pipeline.py --reset
        Same, but runs 01_create_database.sql first.
        WARNING: that script DROPs the whole database.

    python etl/run_pipeline.py --load
        Force a fresh staging load even if staging already has rows.

    python etl/run_pipeline.py --no-indexes
        Skip 08_create_indexes.sql, leaving the default 04 indexes.

Safety
------
This whole pipeline is idempotent. Running it twice will not duplicate data.

The OLTP loaders and the fact table reload still truncate before inserting,
but Dim_Employee, Dim_Department and Dim_Project are UPSERTED, so a version
written through the app (a transfer, a hire) survives a full refresh
instead of being wiped. What staging cannot see is reloaded from the OLTP
tables by the delta loaders, and sp_repair_dim_employee_versions() puts the
"exactly one open version, and it is the newest" invariant back.

Every DDL statement in 08 is guarded, so index tuning is idempotent too.

    python etl/run_pipeline.py
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

import pymysql
from sqlalchemy.engine import make_url

REPO_ROOT = Path(__file__).resolve().parent.parent
SQL_DIR = REPO_ROOT / "database"
ETL_DIR = REPO_ROOT / "etl"

DEFAULT_DB_URL = os.getenv("DATABASE_URL")
DATABASE_NAME = "employee_analytics_dw"

RESET_SCRIPT = "01_create_database.sql"

# Order matters: staging, OLTP, DW, then the procedures that move data
# between them, then the views that read the DW.
#
# 10 comes after 06 because sp_load_dw_master() calls the delta loaders it
# defines; it has to exist in the database by the time STEP 3 runs the
# masters, not at the moment 06 is compiled.
SCRIPTS = [
    "02_create_staging.sql",
    "03_create_oltp.sql",
    "04_create_dw.sql",
    "05_etl_staging_to_oltp.sql",
    "06_etl_oltp_to_dw.sql",
    "07_dashboard_queries.sql",
    "10_incremental_loads.sql",
]

# Run separately, AFTER the data load. sp_load_dw_master() truncates the
# fact table and bulk-inserts every row, so tuning the indexes before the
# load would mean maintaining them across 38k inserts and then analysing
# an empty table. Afterwards they are built once over final data and the
# statistics are meaningful.
INDEX_SCRIPT = "08_create_indexes.sql"

# Fixed-width key migration. A database built with --reset already has
# these columns as CHAR because 02/03/04 declare them that way, so this
# is a no-op there; it converts a database that predates the change.
TYPE_SCRIPT = "09_alter_fixed_length_columns.sql"

# Expected row counts once the pipeline has completed.
EXPECTED = {
    "staging": [
        ("stg_departments", 12),
        ("stg_employees", 15000),
        ("stg_employee_history", 19948),
        ("stg_projects", 1500),
        ("stg_employee_project_assignments", 36007),
        ("stg_performance_reviews", 38814),
    ],
    "oltp": [
        ("departments", 12),
        ("employees", 15000),
        ("projects", 1500),
        ("employee_project_assignments", 36007),
        ("performance_reviews", 38814),
    ],
    "olap": [
        ("dim_date", 3287),
        ("dim_department", 12),
        ("dim_project", 1500),
        ("dim_employee", 19948),
        ("fact_performance_reviews", 38814),
    ],
}


# --------------------------------------------------------------------------
# SQL execution
# --------------------------------------------------------------------------


def split_statements(text: str) -> list[str]:
    """
    Split a SQL script into individual statements.

    `DELIMITER` is a mysql client directive, not SQL, so it has to be
    interpreted here rather than sent to the server. Without this, every
    stored procedure body would be shredded at its first semicolon.
    """
    statements: list[str] = []
    delimiter = ";"
    buffer: list[str] = []

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()

        if not stripped or stripped.startswith("--"):
            continue

        if stripped.upper().startswith("DELIMITER "):
            if buffer:
                statements.append("\n".join(buffer))
                buffer = []
            delimiter = stripped[len("DELIMITER "):].strip()
            continue

        buffer.append(line)

        # A statement ends when the code part of this line closes the
        # current delimiter. Anything after "--" is a trailing comment and
        # must not be able to trigger a split.
        code = line.split("--", 1)[0]
        if code.rstrip().endswith(delimiter):
            body = "\n".join(buffer)
            statements.append(body[: body.rfind(delimiter)].rstrip())
            buffer = []

    if buffer:
        statements.append("\n".join(buffer))

    return [s for s in statements if s.strip()]


def execute_script(connection, path: Path) -> int:
    """Run one .sql file. Returns the number of statements executed."""
    statements = split_statements(path.read_text(encoding="utf-8"))
    with connection.cursor() as cursor:
        for statement in statements:
            cursor.execute(statement)
            # Drain any result set so the next statement is not refused
            # with "Commands out of sync".
            while cursor.nextset():
                pass
    return len(statements)


def call_procedure(connection, name: str) -> list:
    """
    CALL a procedure and drain every result set it produces.

    Stored procedures here return two SELECTs each. Any unread result set
    would leave the connection with unread rows and break the next command.
    """
    with connection.cursor() as cursor:
        cursor.execute(f"CALL {name}();")
        results = []
        while True:
            results.append(cursor.fetchall())
            if not cursor.nextset():
                break
    return results


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------


def count_rows(connection, table: str) -> int:
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT COUNT(*) FROM {table}")
        return cursor.fetchone()[0]


def staging_is_empty(connection) -> bool:
    return count_rows(connection, "stg_employees") == 0


def query_scalar(connection, sql: str) -> int:
    with connection.cursor() as cursor:
        cursor.execute(sql)
        return cursor.fetchone()[0]


def query_row(connection, sql: str) -> dict:
    with connection.cursor() as cursor:
        cursor.execute(sql)
        columns = [d[0] for d in cursor.description]
        return dict(zip(columns, cursor.fetchone()))


def print_reconciliation(connection) -> bool:
    """
    Read vw_oltp_dw_reconciliation and fail on any broken invariant.

    Row counts prove the tables are the right SIZE. This proves they agree
    with each other: no employee without a version, no review without a
    fact row, no employee with two open versions to make the point-in-time
    fact join ambiguous.
    """
    try:
        report = query_row(connection, "SELECT * FROM vw_oltp_dw_reconciliation")
    except pymysql.MySQLError as exc:
        print(f"  MISSING  vw_oltp_dw_reconciliation: {exc}")
        return False

    print(f"  employees   OLTP {report['oltp_employees']:>7,}"
          f"   warehouse {report['dw_employees']:>7,}"
          f"   open versions {report['dw_open_versions']:>7,}")

    all_ok = True
    for name in MUST_BE_ZERO:
        value = report[name]
        print(f"  {'ok      ' if value == 0 else 'BROKEN  '}{name:<32} {value:>9,}")
        if value != 0:
            all_ok = False

    if report["drifted_employees"]:
        # Not a failure: it means an app-created employee was edited in
        # the OLTP and not published yet. Say how to fix it.
        print(f"  pending  {report['drifted_employees']:>7,} unpublished change(s)"
              f" - run sp_load_dim_employee_delta() or press Sync in the app")
    else:
        print("  pending       0 unpublished change(s)")

    print(f"  note     {report['employees_history_newer_than_oltp']:>7,} employees have "
          f"staging history that runs ahead of the OLTP snapshot (by design)")
    return all_ok


# Tables the Streamlit app writes to. Those rows have no staging source,
# so the pipeline legitimately carries more rows than the staging count:
# the loads upsert instead of truncating, sp_load_dw_master() seeds a
# Dim_Employee version for each employee the rebuild cannot see, and a
# transfer applied through the app adds a version that staging never
# produced.
#
# Each entry counts the rows that are absent from staging, which lets the
# report below accept exactly those extra rows and nothing else. Tolerating
# ">= expected" instead would hide a partial load.
APP_EXTRA_SQL = {
    "employees":
        "SELECT COUNT(*) FROM employees e WHERE NOT EXISTS ("
        "SELECT 1 FROM stg_employees s WHERE s.EmployeeID = e.EmployeeID)",
    "projects":
        "SELECT COUNT(*) FROM projects p WHERE NOT EXISTS ("
        "SELECT 1 FROM stg_projects s WHERE s.ProjectID = p.ProjectID)",
    "employee_project_assignments":
        "SELECT COUNT(*) FROM employee_project_assignments a WHERE NOT EXISTS ("
        "SELECT 1 FROM stg_employee_project_assignments s "
        "WHERE s.AssignmentID = a.AssignmentID)",
    "performance_reviews":
        "SELECT COUNT(*) FROM performance_reviews r WHERE NOT EXISTS ("
        "SELECT 1 FROM stg_performance_reviews s WHERE s.ReviewID = r.ReviewID)",
    # Compared at VERSION level, not employee level: an employee who has
    # staging history but also had a transfer applied through the app owns
    # one dim_employee row that no staging row accounts for, and that row
    # must survive the refresh.
    "dim_employee":
        "SELECT COUNT(*) FROM dim_employee d WHERE NOT EXISTS ("
        "SELECT 1 FROM stg_employee_history s WHERE s.EmployeeID = d.EmployeeID "
        "AND s.EffectiveStartDate = d.EffectiveStartDate)",
    "dim_project":
        "SELECT COUNT(*) FROM dim_project d WHERE NOT EXISTS ("
        "SELECT 1 FROM stg_projects s WHERE s.ProjectID = d.ProjectID)",
}

# Invariants that must hold once the pipeline has finished. A count check
# cannot see these: a dim table can have the right number of rows and still
# be internally inconsistent.
#
# drifted_employees is deliberately NOT in this list. It is non-zero
# whenever someone has edited an app-created employee in the OLTP without
# publishing it yet, which is a state to report, not a build failure.
MUST_BE_ZERO = (
    "employees_missing_from_dim",
    "dim_rows_without_employee",
    "reviews_missing_from_fact",
    "facts_without_review",
    "reviews_outside_calendar",
    "employees_with_two_open_versions",
    "stale_open_versions",
)


def print_report(connection) -> bool:
    """Compare every table against its expected count. Returns all-ok.

    For app-writable tables the target is the staging baseline plus the
    rows that exist only because the app created them.
    """
    all_ok = True
    app_rows_total = 0

    for layer, tables in EXPECTED.items():
        print(f"\n{layer.upper()}")
        print("-" * 52)
        for table, expected in tables:
            try:
                actual = count_rows(connection, table)
            except pymysql.MySQLError as exc:
                print(f"  MISSING  {table:<36} {exc}")
                all_ok = False
                continue

            extra = 0
            if table in APP_EXTRA_SQL:
                try:
                    extra = query_scalar(connection, APP_EXTRA_SQL[table])
                except pymysql.MySQLError as exc:
                    print(f"  MISSING  {table:<36} cannot count app rows: {exc}")
                    all_ok = False
                    continue

            target = expected + extra
            app_rows_total += extra

            if actual == target:
                if extra:
                    print(f"  ok       {table:<36} {actual:>9,}"
                          f"  (+{extra:,} added via the app)")
                else:
                    print(f"  ok       {table:<36} {actual:>9,}")
            elif actual == 0:
                print(f"  EMPTY    {table:<36} {0:>9,}  (expected {target:,})")
                all_ok = False
            else:
                print(f"  differs  {table:<36} {actual:>9,}  (expected {target:,})")
                all_ok = False

    if app_rows_total:
        print(f"\n  {app_rows_total:,} app-created row(s) carried through "
              f"the refresh without being overwritten.")

    return all_ok


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build the whole database in the correct order.",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="run 01_create_database.sql first (DROPS the database)",
    )
    parser.add_argument(
        "--load",
        action="store_true",
        help="force a staging reload even if staging already has rows",
    )
    parser.add_argument(
        "--no-indexes",
        action="store_true",
        help=f"skip {INDEX_SCRIPT} and keep the default 04 indexes",
    )
    args = parser.parse_args()

    url = make_url(os.getenv("DATABASE_URL", DEFAULT_DB_URL))

    # The .sql files all begin with "USE employee_analytics_dw", so the
    # schema name is fixed by the scripts themselves; only the connection
    # details come from DATABASE_URL.
    database_name = DATABASE_NAME

    # Connect without selecting a schema: with --reset the database is
    # dropped and recreated by 01 itself, otherwise it must already exist.
    connection = pymysql.connect(
        host=url.host or "localhost",
        port=url.port or 3306,
        user=url.username,
        password=url.password or "",
        autocommit=True,
    )

    try:
        if args.reset:
            print("=" * 60)
            print("RESET MODE - this DROPS and recreates the database")
            print("=" * 60)
            count = execute_script(connection, SQL_DIR / RESET_SCRIPT)
            print(f"\n  {RESET_SCRIPT}: {count} statements")
        else:
            connection.select_db(database_name)
            print(f"Using existing database '{database_name}'")

        print("\n" + "=" * 60)
        print("STEP 1/5 - schema, procedures and views")
        print("=" * 60)
        for name in SCRIPTS:
            path = SQL_DIR / name
            if not path.exists():
                print(f"  MISSING  {name}")
                return 1
            count = execute_script(connection, path)
            print(f"  ok       {name:<34} {count:>3} statements")

        print("\n" + "=" * 60)
        print("STEP 2/5 - staging data")
        print("=" * 60)
        need_load = args.load or args.reset or staging_is_empty(connection)
        if need_load:
            reason = "--load forced" if args.load else (
                "--reset" if args.reset else "empty")
            data_dir = REPO_ROOT / "synthetic_data"
            print(f"  staging needs loading ({reason}) -> reading {data_dir}")
            sys.path.insert(0, str(ETL_DIR))
            import load_staging
            if load_staging.main() != 0:
                print("  staging load FAILED")
                return 1
        else:
            print("  staging already populated -> skipping (use --load to force)")

        print("\n" + "=" * 60)
        print("STEP 3/5 - loading OLTP and OLAP (the part that is easy to forget)")
        print("=" * 60)
        for procedure in ("sp_load_oltp_master", "sp_load_dw_master"):
            print(f"\n  CALL {procedure}()")
            call_procedure(connection, procedure)

        print("\n" + "=" * 60)
        print("STEP 4/5 - fixed-width key migration")
        print("=" * 60)
        # Must run before the ANALYZE in 08, because converting a column
        # rewrites its indexes and invalidates the statistics.
        path = SQL_DIR / TYPE_SCRIPT
        if not path.exists():
            print(f"  MISSING  {TYPE_SCRIPT}")
            return 1
        count = execute_script(connection, path)
        print(f"  ok       {TYPE_SCRIPT:<34} {count:>3} statements")

        print("\n" + "=" * 60)
        print("STEP 5/5 - OLAP index tuning")
        print("=" * 60)
        if args.no_indexes:
            print(f"  skipped ({INDEX_SCRIPT}, --no-indexes)")
        else:
            # Runs after the load on purpose: the indexes are then built
            # once over final data, and ANALYZE TABLE refreshes real
            # statistics instead of statistics for an empty table.
            path = SQL_DIR / INDEX_SCRIPT
            if not path.exists():
                print(f"  MISSING  {INDEX_SCRIPT}")
                return 1
            count = execute_script(connection, path)
            print(f"  ok       {INDEX_SCRIPT:<34} {count:>3} statements")

        print("\n" + "=" * 60)
        print("VERIFICATION")
        print("=" * 60)
        all_ok = print_report(connection)
        print()
        all_ok = print_reconciliation(connection) and all_ok

        print()
        if all_ok:
            print("Pipeline complete. Every table matches its expected count and")
            print("the OLTP and the warehouse agree with each other.")
            print("Next:  streamlit run app.py")
            return 0

        print("Pipeline finished but verification did not pass - see above.")
        return 1

    except pymysql.MySQLError as exc:
        print(f"\nDATABASE ERROR: {exc}")
        if not args.reset and "Unknown database" in str(exc):
            print("The schema does not exist yet. Run with --reset to create it.")
        return 1
    finally:
        connection.close()


if __name__ == "__main__":
    sys.exit(main())