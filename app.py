"""
Enterprise Employee Analytics & Data Warehouse System
=====================================================
READING SEQUENCE STEP 7: Streamlit Presentation Layer & User Experience.

This is the central web dashboard and user interface application.
Connects the user-facing forms and interactive charts to the Data Access Layer (DAL)
and database stored procedures.

Pages Structure:
----------------
1. Dashboard   : Executive KPI cards + 4 core OLAP visualisations (YoY LAG, DENSE_RANK leaderboard,
                 attrition risk model, and project bottlenecks).
2. Employees   : Employee directory grid, onboarding form (atomic dual-write), and SCD Type 2
                 department transfer form (sp_apply_employee_change).
3. Projects    : Project catalog, project creation, and employee assignment bookings (with 100%
                 capacity ceiling guard and SMTP email notifications).
4. Reviews     : Performance appraisal submission (with 1-per-day validation and promotion suggestion)
                 and review history exploration.
5. Warehouse   : SCD2 history explorer, Star Schema row counts, ETL procedure logs (`dw_load_log`),
                 and the automated reconciliation and delta sync panel.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Callable
from datetime import date

import pandas as pd
import plotly.express as px
import streamlit as st

from src.dal import AnalyticsManager, EmployeeManager, ProjectManager, ReviewManager
from src.db_manager import DatabaseConnection
from src.models import Employee, Project, Review
from src.notifications import AssignmentNotifier, MailSettings
from src.rbac import DEMO_USERS, RBAC, Permission, User, UserRole
from src.validation import (
    MAX_EMAIL,
    MAX_NAME,
    MAX_PROJECT_NAME,
    MAX_ROLE,
    PHONE_DIGITS,
    FormValidator,
    clean,
    validate_email,
    validate_name,
    validate_phone,
    validate_role,
    validate_text,
)

# Streamlit swallows stdout but the log is where a traceback belongs: the
# user gets a readable message, the file gets the stack.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Page configuration
# --------------------------------------------------------------------------

st.set_page_config(
    page_title="SkillFlow | Enterprise Workforce Intelligence",
    layout="wide",
    initial_sidebar_state="expanded",
)

def get_secret(key: str, default: str = "") -> str:
    """Safely retrieves a configuration key from st.secrets without throwing FileNotFoundError or KeyError."""
    try:
        if hasattr(st, "secrets"):
            val = st.secrets.get(key, default)
            return str(val).strip() if val is not None else default
    except BaseException:
        pass
    return default


DEFAULT_DB_URL = get_secret("DATABASE_URL", os.getenv("DATABASE_URL", ""))

PALETTE = ["#2E5EAA", "#E4572E", "#17BEBB", "#FFC914", "#6A4C93", "#2A9D8F"]


# --------------------------------------------------------------------------
# Styling & Design System
# --------------------------------------------------------------------------

st.markdown(
    """
    <style>
        .block-container { padding-top: 1.8rem; }

        /* ---------------- Global & Typography ---------------- */
        h1, h2, h3, h4 {
            letter-spacing: -0.4px;
            font-weight: 700;
        }

        /* ---------------- Brand Header ---------------- */
        .brand-container {
            display: flex;
            align-items: center;
            gap: 12px;
            padding: 8px 0 16px 0;
            border-bottom: 1px solid color-mix(in srgb, currentColor 15%, transparent);
            margin-bottom: 14px;
        }
        .brand-badge {
            width: 36px;
            height: 36px;
            border-radius: 9px;
            background: linear-gradient(135deg, #6366f1 0%, #3b82f6 100%);
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: 800;
            font-size: 0.95rem;
            color: #ffffff;
            letter-spacing: -0.5px;
            box-shadow: 0 4px 12px rgba(99, 102, 241, 0.35);
            flex-shrink: 0;
        }
        .brand-title {
            font-size: 1.35rem;
            font-weight: 800;
            letter-spacing: -0.5px;
            margin: 0;
            background: linear-gradient(120deg, #6366f1, #3b82f6, #06b6d4);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }
        .brand-subtitle {
            font-size: 0.72rem;
            text-transform: uppercase;
            letter-spacing: 0.8px;
            opacity: 0.7;
            margin: 0;
        }

        /* ---------------- User Profile Card in Sidebar ---------------- */
        .user-card {
            background: color-mix(in srgb, currentColor 4%, transparent);
            border: 1px solid color-mix(in srgb, currentColor 14%, transparent);
            border-radius: 12px;
            padding: 12px 14px;
            margin-bottom: 12px;
            display: flex;
            flex-direction: column;
            gap: 4px;
            box-shadow: 0 2px 8px rgba(0, 0, 0, 0.04);
        }
        .user-card-header {
            display: flex;
            align-items: center;
            justify-content: space-between;
        }
        .user-name {
            font-size: 0.92rem;
            font-weight: 700;
        }

        /* ---------------- Modern KPI Metric Cards ---------------- */
        [data-testid="stMetric"] {
            background: color-mix(in srgb, currentColor 4%, transparent);
            border: 1px solid color-mix(in srgb, currentColor 14%, transparent);
            border-top: 3px solid #6366f1;
            border-radius: 14px;
            padding: 16px 18px;
            transition: transform 180ms cubic-bezier(0.4, 0, 0.2, 1), box-shadow 180ms cubic-bezier(0.4, 0, 0.2, 1);
            box-shadow: 0 4px 12px rgba(0, 0, 0, 0.04);
        }
        [data-testid="stMetric"]:hover {
            transform: translateY(-3px);
            box-shadow: 0 8px 24px rgba(99, 102, 241, 0.12);
            border-color: color-mix(in srgb, #6366f1 40%, transparent);
        }
        [data-testid="stMetricLabel"] {
            color: color-mix(in srgb, currentColor 80%, transparent);
            font-size: 0.82rem;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.6px;
        }
        [data-testid="stMetricValue"] {
            font-weight: 800;
            font-size: 1.85rem;
            letter-spacing: -0.5px;
        }

        /* ---------------- Modern Segmented Tabs ---------------- */
        button[data-baseweb="tab"] {
            font-weight: 600;
            border-radius: 8px;
            padding: 8px 18px;
            transition: all 160ms ease;
        }
        button[data-baseweb="tab"][aria-selected="true"] {
            background: color-mix(in srgb, #6366f1 15%, transparent);
            color: #6366f1;
        }

        /* ---------------- Modern Buttons ---------------- */
        .stButton > button {
            border-radius: 10px;
            font-weight: 600;
            letter-spacing: 0.2px;
            transition: all 160ms ease;
        }
        .stButton > button:hover {
            transform: translateY(-1px);
        }
        .stButton > button[kind="primary"] {
            background: linear-gradient(135deg, #4f46e5 0%, #7c3aed 100%);
            border: none;
            color: #ffffff;
            box-shadow: 0 4px 14px rgba(79, 70, 229, 0.35);
        }
        .stButton > button[kind="primary"]:hover {
            box-shadow: 0 6px 20px rgba(79, 70, 229, 0.45);
        }

        /* ---------------- Sidebar Navigation ---------------- */
        [data-testid="stSidebar"] div:has([data-testid="stRadioGroup"]) {
            width: 100%;
        }
        [data-testid="stSidebar"] [data-testid="stRadioGroup"] {
            display: flex;
            flex-direction: column;
            align-items: stretch;
            width: 100%;
            gap: 4px;
        }
        [data-testid="stSidebar"] [data-testid="stRadioGroup"] > div {
            display: flex;
            width: 100%;
        }
        [data-testid="stSidebar"] label[data-testid="stRadioOption"] {
            display: flex;
            align-items: center;
            width: 100%;
            padding: 10px 14px;
            border-radius: 8px;
            cursor: pointer;
            transition: background 120ms ease, transform 120ms ease;
        }
        [data-testid="stSidebar"] label[data-testid="stRadioOption"] > div {
            display: flex;
            align-items: center;
            width: 100%;
            min-width: 0;
        }
        [data-testid="stSidebar"]
        label[data-testid="stRadioOption"] > div > div:first-child {
            display: none;
        }
        [data-testid="stSidebar"] label[data-testid="stRadioOption"] p {
            margin: 0;
            font-size: 0.94rem;
            white-space: nowrap;
        }
        [data-testid="stSidebar"] label[data-testid="stRadioOption"]:hover {
            background: color-mix(in srgb, currentColor 9%, transparent);
        }
        [data-testid="stSidebar"]
        label[data-testid="stRadioOption"][data-selected="true"] {
            background: color-mix(in srgb, #6366f1 20%, transparent);
            box-shadow: inset 3px 0 0 #6366f1;
        }
        [data-testid="stSidebar"]
        label[data-testid="stRadioOption"][data-selected="true"] p {
            font-weight: 700;
            color: #6366f1;
        }

        /* ---------------- Security Warning Card ---------------- */
        .rbac-restricted-box {
            background: color-mix(in srgb, #ef4444 10%, transparent);
            border: 1px solid color-mix(in srgb, #ef4444 35%, transparent);
            border-radius: 12px;
            padding: 14px 18px;
            margin: 12px 0 20px 0;
        }
        .rbac-restricted-title {
            color: #ef4444;
            font-weight: 700;
            font-size: 0.95rem;
            margin-bottom: 4px;
            display: flex;
            align-items: center;
            gap: 8px;
        }
        .rbac-restricted-desc {
            font-size: 0.85rem;
            opacity: 0.88;
            margin: 0;
        }

        /* ---------------- Login & Authentication Portal ---------------- */
        .login-hero {
            text-align: center;
            padding: 24px 12px 20px 12px;
            max-width: 820px;
            margin: 0 auto;
        }
        .login-hero-badge {
            display: inline-block;
            padding: 5px 14px;
            border-radius: 20px;
            font-size: 0.78rem;
            font-weight: 700;
            letter-spacing: 0.8px;
            text-transform: uppercase;
            background: color-mix(in srgb, #6366f1 18%, transparent);
            color: #818cf8;
            border: 1px solid color-mix(in srgb, #6366f1 35%, transparent);
            margin-bottom: 12px;
        }
        .login-hero-title {
            font-size: 2.25rem;
            font-weight: 800;
            letter-spacing: -0.8px;
            line-height: 1.25;
            margin: 0 0 10px 0;
            background: linear-gradient(135deg, #6366f1 0%, #3b82f6 50%, #10b981 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }
        .login-hero-sub {
            font-size: 1.0rem;
            opacity: 0.8;
            margin-bottom: 24px;
            line-height: 1.5;
        }
        .persona-card {
            background: color-mix(in srgb, currentColor 3.5%, transparent);
            border: 1px solid color-mix(in srgb, currentColor 12%, transparent);
            border-radius: 14px;
            padding: 16px;
            transition: all 180ms ease;
            display: flex;
            flex-direction: column;
            min-height: 250px;
            margin-bottom: 10px;
        }
        .persona-card:hover {
            border-color: color-mix(in srgb, #6366f1 45%, transparent);
            transform: translateY(-3px);
            box-shadow: 0 8px 24px rgba(99, 102, 241, 0.12);
        }
        .persona-header {
            display: flex;
            align-items: center;
            gap: 10px;
            margin-bottom: 8px;
        }
        .persona-avatar {
            font-size: 1.8rem;
            line-height: 1;
        }
        .persona-name {
            font-size: 0.98rem;
            font-weight: 700;
            margin: 0;
        }
        .persona-title {
            font-size: 0.76rem;
            opacity: 0.7;
            margin: 0;
        }
        .persona-badge {
            display: inline-block;
            font-size: 0.72rem;
            font-weight: 700;
            padding: 2px 8px;
            border-radius: 6px;
            margin: 6px 0 8px 0;
        }
        .persona-desc {
            font-size: 0.80rem;
            opacity: 0.82;
            line-height: 1.4;
            margin-bottom: 10px;
            flex-grow: 1;
        }
        .persona-cred {
            font-family: monospace;
            font-size: 0.74rem;
            background: color-mix(in srgb, currentColor 6%, transparent);
            padding: 4px 8px;
            border-radius: 6px;
            margin-bottom: 4px;
            color: #818cf8;
        }
    </style>
    """,
    unsafe_allow_html=True,
)


# --------------------------------------------------------------------------
# Connection + cached managers
# --------------------------------------------------------------------------


def resolve_database_url() -> str:
    """
    [CORE / IMPORTANT FUNCTION]
    Resolves MySQL connection string in strict priority order:
    1. Streamlit secrets (.streamlit/secrets.toml or Streamlit Community Cloud Secrets)
    2. OS Environment variable (DATABASE_URL)
    3. Project fallback default

    Returns:
    --------
    str
        Active MySQL database URL connection string.
    """
    try:
        url = st.secrets.get("DATABASE_URL")
        if url:
            return str(url).strip()
    except Exception:
        pass
    url = os.getenv("DATABASE_URL")
    if url:
        return str(url).strip()
    return DEFAULT_DB_URL.strip() if DEFAULT_DB_URL else ""


@st.cache_resource(show_spinner=False)
def get_managers(profile: str = "local") -> tuple:
    """
    [CORE / IMPORTANT FUNCTION]
    Cached resource factory that instantiates the 4 Data Access Layer managers
    backed by the dedicated Singleton DatabaseConnection for the requested profile:
    - 'local' : Local MySQL server (127.0.0.1:3306)
    - 'hosted': Cloud MySQL server (Aiven MySQL)

    Why `@st.cache_resource` is essential:
    --------------------------------------
    Streamlit re-executes the entire Python script on every user interaction or button click.
    Caching the managers ensures that the Singleton connection pool and cached statements
    persist across reruns without reconnecting to MySQL on every interaction.
    """
    db = DatabaseConnection.for_environment(profile)
    employees = EmployeeManager(db)
    projects = ProjectManager(db)
    reviews = ReviewManager(db)
    analytics = AnalyticsManager(db)
    return employees, projects, reviews, analytics


def mail_setting(name: str, fallback: str = "") -> str:
    """
    [HELPER / UTILITY FUNCTION]
    Resolves email server configuration settings across Streamlit secrets and environment variables.
    """
    try:
        value = st.secrets.get(name)
        if value:
            return str(value)
    except Exception:
        pass
    return os.getenv(name, fallback)


@st.cache_resource(show_spinner=False)
def get_notifier() -> AssignmentNotifier:
    """
    [HELPER / UTILITY FUNCTION]
    Cached factory that instantiates the single AssignmentNotifier instance for the session.
    """
    return AssignmentNotifier(MailSettings.load(get=mail_setting))


# --------------------------------------------------------------------------
# Small rendering helpers & Error Display
# --------------------------------------------------------------------------


def error_kind(exc: BaseException) -> str | None:
    """
    [HELPER / UTILITY FUNCTION]
    Determines whether an exception belongs to `src.exceptions` by inspecting class names.
    Uses string matching on `__mro__` to remain resilient across Streamlit live code reloads.
    """
    for klass in type(exc).__mro__:
        if klass.__module__ in ("src.exceptions", "exceptions"):
            return klass.__name__
    return None


def duplicate_email_hint(employees_mgr, email: str) -> str:
    """
    [HELPER / UTILITY FUNCTION]
    Constructs a clear user explanation when an email uniqueness clash occurs,
    naming the conflicting employee ID and full name.
    """
    clash = employees_mgr.find_by_email(email)
    if clash is None:
        return (
            f"{email} cannot be used: it is already taken or clashes with "
            "an existing record."
        )
    return (
        f"{clash.employee_id} ({clash.full_name}) already uses {clash.email}. "
        "Use a different address, or edit that employee in the Directory tab."
    )


def show_action_error(
    exc: BaseException,
    action: str,
    on_duplicate: Callable[[], None] | None = None,
) -> None:
    """
    [CORE / IMPORTANT FUNCTION]
    Reports a failed database or business action safely without crashing the Streamlit app.

    Prevents raw tracebacks from reaching the end user while logging full details
    to the logger for developer debugging.
    """
    kind = error_kind(exc)

    if kind == "DuplicateRecordError":
        if on_duplicate is not None:
            on_duplicate()
        else:
            st.error(str(exc))
        return

    if kind is not None:
        st.error(f"{kind}: {exc}")
        return

    logger.exception("Unexpected failure while %s: %s", action, exc)
    st.error(
        f"Could not {action} because of an unexpected problem "
        f"(`{type(exc).__name__}`). Nothing was saved. The details are in "
        "the application log - please report them."
    )


def money(value: float | None) -> str:
    """
    [HELPER / UTILITY FUNCTION]
    Formats a numeric value as an Indian Rupee currency string (e.g. 50000 -> "₹50,000").
    Safely handles None, NaN, and pre-formatted string values.
    """
    if value is None:
        return "-"

    if isinstance(value, str):
        text = value.strip()
        if not text:
            return "-"
        if text.startswith("₹"):
            return text
        try:
            value = float(text.replace(",", ""))
        except ValueError:
            return text

    try:
        amount = float(value)
    except (TypeError, ValueError):
        return str(value)

    if pd.isna(amount):
        return "-"
    return f"₹{amount:,.0f}"


def empty_state(message: str) -> None:
    """
    [HELPER / UTILITY FUNCTION]
    Renders a standard informational callout when a dataset is empty.
    """
    st.info(message)


def show_form_errors(errors: dict[str, str], heading: str) -> None:
    """
    [CORE / IMPORTANT FUNCTION]
    Renders multi-field validation error summaries returned by `FormValidator`.
    Lists every invalid input field alongside its actionable error message.
    """
    st.error(f"{heading} - {len(errors)} field(s) need attention.")
    for field, message in errors.items():
        st.error(f"**{field}**: {message}")


def money_column(values: pd.Series) -> pd.Series:
    """
    [HELPER / UTILITY FUNCTION]
    Vectorized currency formatting helper for pandas DataFrame columns.
    """
    return values.map(money)


# --------------------------------------------------------------------------
# Employee picker
#
# One control, four call sites: the transfer form, the SCD2 explorer, the
# review form and the assignment form all need "pick one employee".
#
# Every one of them used to build a plain selectbox from
# `list_employees(limit=800)`, which orders by EmployeeID and therefore
# stops around EMP-0000984. An employee created through this app gets the
# next free id - EMP-0015001 onwards - so onboarding someone made them
# immediately invisible to every one of those four forms. The database had
# the row; the UI simply could not reach it.
#
# The fix has to live in the control rather than in each caller: a search
# box that queries the server, newest records first by default, and an
# honest count of what is not being shown.
# --------------------------------------------------------------------------

PICKER_LIMIT = 200


def employee_picker(
    employees_mgr,
    key: str,
    label: str = "Employee",
    status: str | None = "Active",
    limit: int = PICKER_LIMIT,
) -> str | None:
    """
    [CORE / IMPORTANT FUNCTION]
    Interactive employee search bar + selectbox dropdown component.

    Critical Architectural Solution:
    --------------------------------
    Solves the 'new employee invisibility bug':
    Previously, static pickers queried `ORDER BY EmployeeID LIMIT 800`, which capped
    around EMP-0000984. Brand new employees received IDs like EMP-0015001+, making them
    invisible in dropdowns!
    This control passes `recent_first=True` to order by `EmployeeID DESC`, ensuring newly
    onboarded employees immediately show up at the top of the picker. Also incorporates
    server-side text search (matching name, email, or employee ID).

    Returns:
    --------
    str | None
        Selected EmployeeID, or None if filter matches no records.
    """
    term = st.text_input(
        f"Search {label.lower()}",
        key=f"{key}_search",
        placeholder="name, email or employee id",
        max_chars=100,
    ).strip()

    people = employees_mgr.list_employees(
        search=term or None,
        employment_status=status,
        limit=limit,
        recent_first=True,
    )
    matching = employees_mgr.count_employees(
        search=term or None,
        employment_status=status,
    )

    lookup = {
        f"{row.EmployeeID} - {row.FirstName} {row.LastName} "
        f"({row.DepartmentName})": row.EmployeeID
        for row in people.itertuples()
    }

    if not lookup:
        st.selectbox(
            label,
            [f"No matching {label.lower()}"],
            index=0,
            disabled=True,
        )
        return None

    if matching > len(people):
        st.caption(
            f"Showing the first {len(people):,} of {matching:,} matching "
            f"{label.lower()}(s) - narrow the search to reach the rest."
        )
    elif not term:
        st.caption(
            f"Showing the {len(people):,} most recently added"
            f"{f' {label.lower()}s' if status == 'Active' else ''}. "
            "Search to reach anyone else."
        )

    return lookup[st.selectbox(label, list(lookup.keys()))]


# --------------------------------------------------------------------------
# Guarded text inputs
# --------------------------------------------------------------------------

# Each entry is matched against a single character.
_ALLOWED_CHARACTERS = {
    # Digits only. [0-9] rather than \d to block non-ASCII numerals.
    "phone": r"[0-9]",
    # Local part plus domain.
    "email": r"[A-Za-z0-9._%+\-@'\u2018\u2019]",
    # A letter, or real-name punctuation.
    "name": r"(?:[^\W\d_]|[ .'\u2018\u2019-])",
    "role": r"(?:[^\W\d_]|[0-9 .,&/()'\u2018\u2019-])",
    # Any printable character, dropping control codes.
    "text": r"[^\x00-\x1f\x7f]",
}


# Set by clear_guarded_inputs after a successful save.
_RESET_FLAG = "gi_reset_pending"


def clear_guarded_inputs(*keys: str) -> None:
    """
    [HELPER / UTILITY FUNCTION]
    Resets the state of guarded input text boxes after form submission.
    """
    st.session_state[_RESET_FLAG] = list(keys)


def guarded_input(
    container,
    label: str,
    key: str,
    *,
    kind: str = "text",
    default: str = "",
    max_chars: int | None = None,
    placeholder: str | None = None,
    help: str | None = None,
    validator=None,
):
    """
    [CORE / IMPORTANT FUNCTION]
    Advanced interactive input field with keystroke sanitization and inline validation.

    How it Works:
    -------------
    Streamlit does not allow blocking native browser keystrokes directly.
    Instead, this widget attaches an `on_change` callback to `st.session_state`:
    1. Characters matching illegal patterns (e.g. non-digits in phone fields) are
       automatically stripped before the input box redraws.
    2. When the user leaves focus or presses Enter, `validator(text)` is triggered.
    3. If validation fails, an inline error is displayed immediately below the field,
       providing instant feedback before final form submission.
    """
    state_key = f"gi_{key}"
    error_key = f"gie_{key}"

    pending = st.session_state.get(_RESET_FLAG)
    if pending and key in pending:
        st.session_state.pop(_RESET_FLAG, None)
        st.session_state.pop(state_key, None)
        st.session_state.pop(error_key, None)

    if state_key not in st.session_state:
        st.session_state[state_key] = default

    char_re = re.compile(rf"(?:{_ALLOWED_CHARACTERS[kind]})")

    def on_change() -> None:
        text = str(st.session_state.get(state_key, ""))
        allowed = "".join(ch for ch in text if char_re.match(ch))
        if allowed != text:
            st.session_state[state_key] = allowed
            text = allowed

        if validator is None or not text.strip():
            st.session_state.pop(error_key, None)
            return
        try:
            validator(text)
        except Exception as exc:  # noqa: BLE001 - a callback cannot crash
            if error_kind(exc) == "ValidationError":
                st.session_state[error_key] = str(exc)
            else:
                logger.exception(
                    "Unexpected failure while checking %s", label
                )
                st.session_state[error_key] = (
                    "This field could not be checked. Please try again."
                )
        else:
            st.session_state.pop(error_key, None)

    value = container.text_input(
        label,
        key=state_key,
        max_chars=max_chars,
        placeholder=placeholder,
        help=help,
        on_change=on_change,
    )

    problem = st.session_state.pop(error_key, None)
    if problem:
        container.error(problem)

    return value


# --------------------------------------------------------------------------
# Authentication Gate & Login Portal
# --------------------------------------------------------------------------


def render_login_screen() -> None:
    """
    [PRESENTATION / AUTHENTICATION GATEWAY]
    Renders the SkillFlow enterprise authentication portal.
    Features:
    1. One-Click Persona Quick-Access: Instant evaluation across corporate RBAC roles.
    2. Corporate Credential Form: Authentic email + password login verification.
    3. Test Credentials Directory: Complete role mapping and security scopes.
    """
    st.markdown(
        """
        <div class="login-hero">
            <div class="login-hero-badge">Enterprise Security & Access Gateway</div>
            <h1 class="login-hero-title">SkillFlow Workforce Intelligence</h1>
            <p class="login-hero-sub">
                Enterprise Data Warehouse & Analytics Platform with Granular Role-Based Access Control.
                Select a verified persona below for instant evaluation, or sign in with corporate credentials.
            </p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    tab_demo, tab_manual = st.tabs(
        [
            "One-Click Demo Personas (Reviewer Mode)",
            "Corporate Credential Sign In",
        ]
    )

    with tab_demo:
        st.markdown(
            "<p style='text-align: center; color: #94a3b8; font-size: 0.92rem; margin-bottom: 20px;'>"
            "Select an organizational role below to immediately test its permissions, restricted views, and operational capabilities."
            "</p>",
            unsafe_allow_html=True,
        )

        cols = st.columns(4)
        for idx, (email, user) in enumerate(DEMO_USERS.items()):
            role_meta = RBAC.get_role_info(user.role)
            with cols[idx]:
                st.markdown(
                    f"""
                    <div class="persona-card">
                        <div class="persona-header">
                            <div>
                                <span style="font-size: 0.70rem; font-weight: 800; padding: 2px 7px; border-radius: 4px; background: color-mix(in srgb, {role_meta['badge_color']} 18%, transparent); color: {role_meta['badge_color']}; border: 1px solid {role_meta['badge_color']};">
                                    {getattr(user, 'badge_tag', 'USER')}
                                </span>
                                <h4 class="persona-name" style="margin-top: 6px;">{user.name}</h4>
                                <p class="persona-title">{user.title}</p>
                            </div>
                        </div>
                        <div>
                            <span class="persona-badge" style="background: color-mix(in srgb, {role_meta['badge_color']} 15%, transparent); color: {role_meta['badge_color']}; border: 1px solid {role_meta['badge_color']};">
                                {role_meta['badge']}
                            </span>
                        </div>
                        <p class="persona-desc">{role_meta['description']}</p>
                        <div class="persona-cred">Email: {user.email}</div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
                if st.button(
                    f"Sign In as {user.role.value}",
                    key=f"demo_login_{user.role.name}",
                    use_container_width=True,
                    type="primary" if user.role == UserRole.HR_ADMIN else "secondary",
                ):
                    st.session_state["authenticated"] = True
                    st.session_state["current_user"] = user
                    st.session_state["active_role"] = user.role
                    st.rerun()

    with tab_manual:
        st.markdown("<br>", unsafe_allow_html=True)
        login_col, cheat_col = st.columns([1, 1], gap="large")

        with login_col:
            st.markdown(
                """
                <div style="font-weight: 700; font-size: 1.15rem; margin-bottom: 4px;">
                    Corporate Single Sign-On (SSO)
                </div>
                <div style="font-size: 0.85rem; color: #94a3b8; margin-bottom: 16px;">
                    Authenticate using your verified corporate credentials.
                </div>
                """,
                unsafe_allow_html=True,
            )
            with st.form("manual_credential_form"):
                email_input = st.text_input(
                    "Corporate Email",
                    value="admin@skillflow.com",
                    placeholder="name@skillflow.com",
                )
                password_input = st.text_input(
                    "Password",
                    value="admin",
                    type="password",
                    placeholder="Enter account password",
                )
                submit_login = st.form_submit_button(
                    "Authenticate & Sign In",
                    use_container_width=True,
                    type="primary",
                )

                if submit_login:
                    matched_user = RBAC.authenticate(email_input, password_input)
                    if matched_user:
                        st.session_state["authenticated"] = True
                        st.session_state["current_user"] = matched_user
                        st.session_state["active_role"] = matched_user.role
                        st.success(f"Access granted: {matched_user.name} ({matched_user.role.value})")
                        st.rerun()
                    else:
                        st.error("Authentication failed. Invalid email or password. See the Credentials Directory on the right.")

        with cheat_col:
            st.markdown(
                """
                <div style="font-weight: 700; font-size: 1.15rem; margin-bottom: 4px;">
                    Test Credentials Directory
                </div>
                <div style="font-size: 0.85rem; color: #94a3b8; margin-bottom: 16px;">
                    Pre-seeded test accounts for sandbox security verification.
                </div>
                """,
                unsafe_allow_html=True,
            )
            cheat_data = [
                {"Role": "HR Administrator", "Email": "admin@skillflow.com", "Password": "admin", "Scope": "Full Read/Write, SCD2, Dual-Write, DW Sync"},
                {"Role": "Department Manager", "Email": "manager@skillflow.com", "Password": "manager", "Scope": "Team Analytics, Reviews, Projects"},
                {"Role": "Compliance Auditor", "Email": "auditor@skillflow.com", "Password": "audit", "Scope": "Strict Read-Only, DW Logs, SCD2 Audit"},
                {"Role": "Standard Employee", "Email": "employee@skillflow.com", "Password": "emp", "Scope": "Self-Service Directory, Project Catalog"},
            ]
            st.dataframe(
                pd.DataFrame(cheat_data),
                use_container_width=True,
                hide_index=True,
            )

    st.markdown("---")
    st.caption("SkillFlow Enterprise Security Gateway | End-to-end Session RBAC Protection | Built with Streamlit & MySQL 8.0")


# Session State Initialization
if "authenticated" not in st.session_state:
    st.session_state["authenticated"] = False
    st.session_state["current_user"] = None
    st.session_state["active_role"] = UserRole.HR_ADMIN

# Authentication Gate: Blocks all unauthorized requests
if not st.session_state["authenticated"]:
    render_login_screen()
    st.stop()

# --------------------------------------------------------------------------
# Sidebar & Authenticated Session Management
# --------------------------------------------------------------------------

current_user: User = st.session_state.get("current_user") or DEMO_USERS["admin@skillflow.com"]
active_role: UserRole = st.session_state.get("active_role", current_user.role)
role_info = RBAC.get_role_info(active_role)

st.sidebar.markdown(
    """
    <div class="brand-container">
        <div class="brand-badge">SF</div>
        <div>
            <div class="brand-title">SkillFlow</div>
            <div class="brand-subtitle">Workforce Intelligence & DW</div>
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)

# Active User Profile Card
st.sidebar.markdown(
    f"""
    <div class="user-card">
        <div class="user-card-header">
            <span class="user-name">{current_user.name}</span>
            <span style="font-size: 0.72rem; padding: 2px 8px; border-radius: 12px; background: color-mix(in srgb, {role_info['badge_color']} 18%, transparent); color: {role_info['badge_color']}; font-weight: 700; border: 1px solid {role_info['badge_color']};">
                {role_info['badge']}
            </span>
        </div>
        <div style="font-size: 0.78rem; opacity: 0.75; margin-top: 2px;">
            {current_user.title} • {current_user.department}
        </div>
        <div style="font-size: 0.75rem; opacity: 0.85; margin-top: 6px; line-height: 1.35;">
            {role_info['description']}
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)

# Rapid Persona Switcher (Enables live switching during evaluations)
with st.sidebar.expander("Switch Demo Persona", expanded=False):
    st.caption("Evaluate another role's permissions:")
    for email, demo_u in DEMO_USERS.items():
        is_current = demo_u.email == current_user.email
        btn_label = f"{demo_u.name} ({demo_u.role.value}) {'[ACTIVE]' if is_current else ''}"
        if st.button(
            btn_label,
            key=f"sb_switch_{demo_u.role.name}",
            use_container_width=True,
            disabled=is_current,
        ):
            st.session_state["current_user"] = demo_u
            st.session_state["active_role"] = demo_u.role
            st.rerun()

# Sign Out button
if st.sidebar.button("Sign Out", use_container_width=True):
    st.session_state["authenticated"] = False
    st.session_state["current_user"] = None
    st.session_state["active_role"] = UserRole.HR_ADMIN
    st.rerun()

st.sidebar.divider()

is_cloud_env = (
    "STREAMLIT_SHARING_MODE" in os.environ
    or "STREAMLIT_SERVER_ENVIRONMENT" in os.environ
    or os.getenv("DEPLOYMENT_ENV") == "hosted"
    or not os.path.exists(".env")
)

st.sidebar.markdown(
    "<div style='font-size: 0.74rem; text-transform: uppercase; font-weight: 700; opacity: 0.7; letter-spacing: 0.5px; margin-bottom: 2px;'>"
    "Connection Profile</div>",
    unsafe_allow_html=True,
)
db_choice = st.sidebar.radio(
    "Target Connection",
    ["Local (127.0.0.1:3306)", "Hosted (Aiven Cloud)"],
    index=1 if is_cloud_env else 0,
    label_visibility="collapsed",
    key="db_env_profile",
)

active_profile = "hosted" if "Hosted" in db_choice else "local"

employees_mgr, projects_mgr, reviews_mgr, analytics_mgr = get_managers(
    active_profile
)

connected = analytics_mgr.is_alive()
profile_name = "Local (127.0.0.1)" if active_profile == "local" else "Hosted (Aiven Cloud)"

if connected:
    st.sidebar.markdown(
        f"<div style='font-size: 0.8rem; opacity: 0.85; display: flex; align-items: center; gap: 6px; margin: 6px 0;'>"
        f"<span style='width: 8px; height: 8px; border-radius: 50%; background: #10b981; display: inline-block;'></span>"
        f"<b>Database:</b> {profile_name} Connected</div>",
        unsafe_allow_html=True,
    )
else:
    st.sidebar.markdown(
        f"<div style='font-size: 0.8rem; opacity: 0.85; display: flex; align-items: center; gap: 6px; margin: 6px 0;'>"
        f"<span style='width: 8px; height: 8px; border-radius: 50%; background: #ef4444; display: inline-block;'></span>"
        f"<b>Database:</b> {profile_name} Failed</div>",
        unsafe_allow_html=True,
    )

PAGE_HOME = "Dashboard"
PAGE_EMPLOYEES = "Employees"
PAGE_PROJECTS = "Projects"
PAGE_REVIEWS = "Reviews"
PAGE_WAREHOUSE = "Warehouse"

# Dynamic Navigation based on RBAC permissions
allowed_pages = RBAC.get_allowed_pages(active_role)
page = st.sidebar.radio(
    "Navigate",
    allowed_pages,
)

if connected:
    counts = analytics_mgr.warehouse_row_counts()
    total_rows = int(counts["rows_count"].sum()) if not counts.empty else 0
    st.sidebar.divider()
    st.sidebar.metric("Warehouse rows", f"{total_rows:,}")
    dim_emp_count = 0
    if not counts.empty and (counts.table_name == "dim_employee").any():
        dim_emp_count = int(
            counts.loc[counts.table_name == "dim_employee", "rows_count"].iloc[0]
        )
    st.sidebar.metric("SCD2 versions", f"{dim_emp_count:,}")

if not connected:
    db_err = analytics_mgr.get_last_error()
    st.error(
        "Cannot reach MySQL database. Set `DATABASE_URL` in `.streamlit/secrets.toml` "
        "or in your Streamlit Community Cloud app settings under **Secrets**, then reload."
    )
    if db_err:
        with st.expander("Connection Diagnostic Details", expanded=True):
            st.code(db_err, language="text")
            st.info(
                "Tip for Cloud MySQL (e.g. Aiven, AWS RDS, DigitalOcean):\n"
                "- URI format: `mysql+pymysql://avnadmin:PASSWORD@HOST:PORT/employee_analytics_dw`\n"
                "- Ensure your database instance is running and port is open to the public."
            )
    st.stop()


# ==========================================================================
# [PAGE 1: EXECUTIVE DASHBOARD]
# ==========================================================================
# Purpose:
# Displays high-level executive KPI cards and 4 core OLAP visualisations
# backed by SQL Star Schema views in MySQL:
# - Chart 1: Year-over-Year Performance Trend (SQL LAG)
# - Chart 2: Top Performers by Department (SQL DENSE_RANK)
# - Chart 3: Attrition Risk Distribution & Model
# - Chart 4: Project Bottlenecks & Capacity Overload
# ==========================================================================

if page == PAGE_HOME:
    st.title("Executive Dashboard")
    role_meta = RBAC.get_role_info(active_role)
    st.markdown(
        f"""
        <div style="background: color-mix(in srgb, {role_meta['badge_color']} 10%, transparent); border: 1px solid color-mix(in srgb, {role_meta['badge_color']} 30%, transparent); border-radius: 10px; padding: 10px 16px; margin-bottom: 16px; display: flex; align-items: center; justify-content: space-between;">
            <div>
                <span style="font-weight: 700; font-size: 0.88rem; color: {role_meta['badge_color']}; text-transform: uppercase; letter-spacing: 0.5px;">
                    {role_meta['title']} View
                </span>
                <span style="font-size: 0.84rem; opacity: 0.85; margin-left: 10px;">
                    {role_meta['description']}
                </span>
            </div>
            <span style="font-size: 0.72rem; padding: 3px 9px; border-radius: 12px; font-weight: 700; background: color-mix(in srgb, {role_meta['badge_color']} 20%, transparent); color: {role_meta['badge_color']};">
                {role_meta['badge']}
            </span>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.caption("All figures are computed live from the Kimball Star Schema via dimensional views.")

    kpi = analytics_mgr.kpi_summary()

    if not kpi:
        empty_state("Warehouse is empty. Run the ETL procedures first.")
        st.stop()

    total_emp = int(kpi.get("total_employees") or 0)
    active_emp = int(kpi.get("active_employees") or 0)
    term_emp = int(kpi.get("terminated_employees") or 0)
    attrition = kpi.get("attrition_pct", 0.0)
    avg_sal = kpi.get("avg_salary", 0.0)
    avg_sc = kpi.get("avg_score", 0.0)

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Employees", f"{total_emp:,}")
    c2.metric("Active", f"{active_emp:,}")
    c3.metric(
        "Attrition",
        f"{attrition}%",
        delta=f"{term_emp} leavers" if term_emp else None,
        delta_color="inverse",
    )
    c4.metric("Avg salary", money(avg_sal))
    c5.metric("Avg score", f"{avg_sc}")

    st.divider()

    # ---------------- Year over year trend ----------------
    st.subheader("Year-over-year performance trend")
    st.caption("Computed with LAG() in vw_performance_trend_yoy.")

    trend = analytics_mgr.performance_trend_yoy()
    if trend.empty:
        empty_state("No review data yet.")
    else:
        left, right = st.columns([3, 2])

        with left:
            figure = px.line(
                trend,
                x="review_year",
                y="avg_score",
                markers=True,
                hover_data={
                    "review_count": True,
                    "avg_rating": ":.2f",
                    "yoy_score_change": ":.2f",
                },
                labels={"review_year": "Year", "avg_score": "Avg performance score"},
            )
            figure.update_traces(line=dict(width=3, color=PALETTE[0]))
            figure.update_layout(height=380, showlegend=False)
            st.plotly_chart(figure, width="stretch")

        with right:
            display = trend[
                ["review_year", "avg_score", "yoy_score_change",
                 "yoy_score_pct", "review_count", "promotions"]
            ].copy()
            display.columns = [
                "Year", "Avg score", "YoY change", "YoY %", "Reviews", "Promotions",
            ]
            st.dataframe(
                display,
                width="stretch",
                hide_index=True,
                height=380,
                column_config={
                    "YoY change": st.column_config.NumberColumn(format="%.2f"),
                    "YoY %": st.column_config.NumberColumn(format="%.2f %%"),
                    "Avg score": st.column_config.NumberColumn(format="%.2f"),
                },
            )

    st.divider()

    # ---------------- Top performers ----------------
    st.subheader("Top performers by department")
    st.caption("Ranked with DENSE_RANK() over each department's average score.")

    departments = ["All departments"] + analytics_mgr.department_list()
    pick = st.columns([2, 1, 1])
    chosen_dept = pick[0].selectbox("Department", departments, key="top_dept")
    top_n = pick[1].slider("Top N", 3, 15, 5)
    min_reviews = pick[2].slider("Min reviews", 1, 10, 2)

    department_filter = None if chosen_dept == "All departments" else chosen_dept
    leaders = analytics_mgr.top_performers(
        department=department_filter, top_n=top_n, min_reviews=min_reviews
    )
    leaders = leaders[leaders["dept_rank"] <= top_n]

    if leaders.empty:
        empty_state("No employee meets the review threshold.")
    else:
        bar_left, bar_right = st.columns(2)

        with bar_left:
            leaders = leaders.copy()
            leaders["MonthlyIncome"] = leaders["MonthlyIncome"].map(money)
            figure = px.bar(
                leaders.sort_values("avg_score"),
                x="avg_score",
                y="employee_name",
                color="DepartmentName",
                orientation="h",
                hover_data={
                    "JobRole": True, "MonthlyIncome": True,
                    "avg_rating": ":.2f", "review_count": True,
                },
                labels={"avg_score": "Avg score", "employee_name": ""},
                color_discrete_sequence=PALETTE,
            )
            figure.update_layout(height=max(320, 22 * len(leaders)), showlegend=False)
            st.plotly_chart(figure, width="stretch")

        with bar_right:
            table = leaders[
                ["dept_rank", "employee_name", "DepartmentName", "JobRole",
                 "avg_score", "avg_rating", "review_count"]
            ].copy()
            table.columns = [
                "Rank", "Employee", "Department", "Role",
                "Avg score", "Avg rating", "Reviews",
            ]
            st.dataframe(
                table,
                width="stretch",
                hide_index=True,
                height=max(320, 22 * len(table)),
            )

    st.divider()

    # ---------------- Attrition risk ----------------
    st.subheader("Attrition risk")
    st.caption("Risk score blends low score, low rating, short tenure and leavers.")

    risk_left, risk_mid, risk_right = st.columns(3)
    risk_dept = risk_left.selectbox(
        "Risk department",
        ["All departments"] + analytics_mgr.department_list(),
        key="risk_dept",
    )
    bands = analytics_mgr.risk_band_summary()
    band_options = ["All bands"] + bands["risk_band"].tolist()
    risk_band = risk_mid.selectbox("Risk band", band_options, key="risk_band")
    min_risk = risk_right.slider("Minimum risk score", 0, 100, 40, step=5)

    at_risk = analytics_mgr.attrition_risk(
        department=None if risk_dept == "All departments" else risk_dept,
        risk_band=None if risk_band == "All bands" else risk_band,
        min_risk=min_risk,
        limit=200,
    )
    dept_attrition = analytics_mgr.attrition_by_department()

    if at_risk.empty:
        empty_state("Nobody matches the selected risk filter.")
    else:
        a1, a2 = st.columns([2, 3])

        with a1:
            figure = px.bar(
                dept_attrition,
                x="attrition_pct",
                y="department",
                orientation="h",
                color="leavers",
                hover_data={"headcount": True, "avg_risk_score": ":.1f"},
                labels={"attrition_pct": "Attrition %", "department": ""},
                color_continuous_scale="Reds",
            )
            figure.update_layout(height=380, coloraxis_showscale=False)
            st.plotly_chart(figure, width="stretch")

        with a2:
            table = at_risk[
                ["employee_name", "DepartmentName", "JobRole", "risk_band",
                 "risk_score", "avg_score", "YearsOfService", "EmploymentStatus"]
            ].copy()
            table.columns = [
                "Employee", "Department", "Role", "Risk band",
                "Risk score", "Avg score", "Tenure", "Status",
            ]
            st.dataframe(
                table,
                width="stretch",
                hide_index=True,
                height=380,
                column_config={
                    # `format` is not optional decoration. A progress
                    # column over a float renders with the "percent"
                    # format by default, and percent multiplies by 100,
                    # so a risk score of 72.35 was labelled "7235%".
                    # The bar is scaled by min_value/max_value; the label
                    # is scaled by `format`. These scores are already on
                    # a 0-100 scale, so they are shown as plain numbers.
                    "Risk score": st.column_config.ProgressColumn(
                        "Risk score",
                        min_value=0,
                        max_value=100,
                        format="%.1f",
                    ),
                    "Avg score": st.column_config.NumberColumn(format="%.1f"),
                    "Tenure": st.column_config.NumberColumn(format="%.1f yrs"),
                },
            )

    st.divider()

    # ---------------- Project bottlenecks ----------------
    st.subheader("Project bottleneck analysis")
    st.caption(
        "Total allocation above 1000% with a below-average score is "
        "flagged as a critical bottleneck."
    )

    status_counts = analytics_mgr.bottleneck_status_counts()
    metric_columns = st.columns(len(status_counts) or 1)
    for column, row in zip(metric_columns, status_counts.to_dict("records")):
        column.metric(row["bottleneck_status"], int(row["projects"]))

    projects_frame = analytics_mgr.project_bottlenecks(limit=60)
    if projects_frame.empty:
        empty_state("No project allocation data yet.")
    else:
        p1, p2 = st.columns(2)

        with p1:
            figure = px.scatter(
                projects_frame,
                x="total_allocation_pct",
                y="avg_score",
                size="people_assigned",
                color="bottleneck_status",
                hover_name="ProjectName",
                hover_data={
                    "ProjectManagerName": True,
                    "score_gap": ":.1f",
                    "load_rank": True,
                },
                labels={
                    "total_allocation_pct": "Total allocation %",
                    "avg_score": "Avg performance score",
                },
                color_discrete_map={
                    "Critical Bottleneck": "#C1121F",
                    "Underperforming": "#F4A261",
                    "Over-Allocated": "#457B9D",
                    "Healthy": "#2A9D8F",
                },
            )
            figure.add_hline(
                y=float(projects_frame["company_avg_score"].iloc[0]),
                line_dash="dash",
                annotation_text="company average",
            )
            figure.update_layout(height=400, legend_title_text="Status")
            st.plotly_chart(figure, width="stretch")

        with p2:
            worst = projects_frame.nsmallest(15, "avg_score")
            figure = px.bar(
                worst.sort_values("avg_score"),
                x="avg_score",
                y="ProjectName",
                orientation="h",
                color="bottleneck_status",
                labels={"avg_score": "Avg score", "ProjectName": ""},
                color_discrete_map={
                    "Critical Bottleneck": "#C1121F",
                    "Underperforming": "#F4A261",
                    "Over-Allocated": "#457B9D",
                    "Healthy": "#2A9D8F",
                },
            )
            figure.update_layout(height=400, showlegend=False)
            st.plotly_chart(figure, width="stretch")

    st.caption(
        f"Company benchmark score: "
        f"{projects_frame['company_avg_score'].iloc[0] if not projects_frame.empty else '-'}"
    )

    st.divider()

    # ---------------- Chart 5: Department Workforce Growth (SCD Type 2 Timeline) ----------------
    st.subheader("Department Workforce Growth & Headcount Evolution (SCD Type 2 Timeline)")
    st.caption("Point-in-time annual headcount distribution and compensation progression powered by dim_employee version history.")

    hc_history = analytics_mgr.department_headcount_history()
    if hc_history.empty:
        empty_state("No historical headcount data available.")
    else:
        hc_col1, hc_col2 = st.columns(2)

        with hc_col1:
            st.markdown("<p style='font-weight: 600; font-size: 0.88rem; margin-bottom: 6px;'>Historical Headcount Scaling by Department</p>", unsafe_allow_html=True)
            hc_fig = px.bar(
                hc_history,
                x="snapshot_year",
                y="headcount",
                color="DepartmentName",
                barmode="stack",
                labels={"snapshot_year": "Snapshot Year", "headcount": "Total Headcount", "DepartmentName": "Department"},
                color_discrete_sequence=PALETTE,
            )
            hc_fig.update_layout(
                height=380,
                margin=dict(l=10, r=10, t=10, b=10),
                legend=dict(orientation="h", yanchor="bottom", y=-0.35, xanchor="center", x=0.5, font=dict(size=9)),
            )
            st.plotly_chart(hc_fig, width="stretch")

        with hc_col2:
            st.markdown("<p style='font-weight: 600; font-size: 0.88rem; margin-bottom: 6px;'>Average Monthly Salary Progression</p>", unsafe_allow_html=True)
            sal_fig = px.line(
                hc_history,
                x="snapshot_year",
                y="avg_salary",
                color="DepartmentName",
                markers=True,
                labels={"snapshot_year": "Snapshot Year", "avg_salary": "Avg Monthly Salary", "DepartmentName": "Department"},
                color_discrete_sequence=PALETTE,
            )
            sal_fig.update_layout(
                height=380,
                margin=dict(l=10, r=10, t=10, b=10),
                showlegend=False,
            )
            st.plotly_chart(sal_fig, width="stretch")

    st.divider()

    # ---------------- Chart 6: Department Performance & Promotion Scorecard ----------------
    st.subheader("Department Performance & Promotion Scorecard")
    st.caption("Cross-departmental performance benchmarking, review volumes, and promotion mobility rates.")

    scorecard = analytics_mgr.department_scorecard()
    if scorecard.empty:
        empty_state("No department scorecard data available.")
    else:
        sc_col1, sc_col2 = st.columns(2)

        with sc_col1:
            st.markdown("<p style='font-weight: 600; font-size: 0.88rem; margin-bottom: 6px;'>Average Performance Score vs Promotions Awarded</p>", unsafe_allow_html=True)
            sc_fig = px.bar(
                scorecard.sort_values("avg_score", ascending=True),
                x="avg_score",
                y="department",
                orientation="h",
                color="promotions",
                labels={"avg_score": "Avg Performance Score", "department": "", "promotions": "Promotions Recommended"},
                color_continuous_scale="Blues",
            )
            sc_fig.update_layout(
                height=380,
                margin=dict(l=10, r=10, t=10, b=10),
            )
            st.plotly_chart(sc_fig, width="stretch")

        with sc_col2:
            st.markdown("<p style='font-weight: 600; font-size: 0.88rem; margin-bottom: 6px;'>Department Executive Benchmark Table</p>", unsafe_allow_html=True)
            sc_display = scorecard[[
                "dept_rank", "department", "reviews", "employees_reviewed",
                "avg_score", "avg_rating", "promotions", "leaver_review_pct"
            ]].copy()
            sc_display.columns = [
                "Rank", "Department", "Reviews", "Reviewed Staff",
                "Avg Score", "Rating", "Promotions", "Leaver Reviews %"
            ]
            st.dataframe(
                sc_display,
                width="stretch",
                hide_index=True,
                height=380,
                column_config={
                    "Rank": st.column_config.NumberColumn(format="%d"),
                    "Avg Score": st.column_config.NumberColumn(format="%.2f"),
                    "Rating": st.column_config.NumberColumn(format="%.2f / 5.0"),
                    "Leaver Reviews %": st.column_config.NumberColumn(format="%.2f %%"),
                },
            )


# ==========================================================================
# [PAGE 2: EMPLOYEE DIRECTORY, ONBOARDING & SCD TYPE 2 TRANSFERS]
# ==========================================================================
# Purpose:
# 1. Directory Tab: Search, filter, inspect full employee records, and delete.
# 2. Onboard Employee Tab: Form with atomic dual-write to OLTP `employees`
#    and first version in DW `dim_employee` via `sp_apply_employee_change`.
# 3. Change Department (SCD2) Tab: Department transfer form that invokes
#    `sp_apply_employee_change`, closing old version and opening new SCD2 version.
# ==========================================================================

elif page == PAGE_EMPLOYEES:
    st.title("Employees")

    tab_directory, tab_onboard, tab_transfer = st.tabs(
        ["Directory", "Onboard employee", "Change department (SCD2)"]
    )

    department_rows = employees_mgr.list_departments()
    department_names = {d["DepartmentID"]: d["DepartmentName"] for d in department_rows}

    with tab_directory:
        f1, f2, f3 = st.columns([3, 2, 2])
        search = f1.text_input(
            "Search",
            placeholder="name, email or employee id",
            max_chars=100,
        )
        dept_filter = f2.selectbox(
            "Department",
            ["All departments"] + list(department_names.values()),
        )
        status_filter = f3.selectbox(
            "Employment status", ["All", "Active", "Terminated"]
        )

        dept_id = (
            next(
                (k for k, v in department_names.items() if v == dept_filter), None
            )
            if dept_filter != "All departments"
            else None
        )
        status_value = None if status_filter == "All" else status_filter

        frame = employees_mgr.list_employees(
            search=search or None,
            department_id=dept_id,
            employment_status=status_value,
            limit=500,
        )
        matching = employees_mgr.count_employees(
            search=search or None,
            department_id=dept_id,
            employment_status=status_value,
        )

        if matching > len(frame):
            st.caption(
                f"Showing the first {len(frame):,} of {matching:,} employees "
                "matching this filter - narrow the search to see the rest."
            )
        else:
            st.caption(f"{matching:,} employee(s) match this filter.")
        if frame.empty:
            empty_state("No employee matches this filter.")
        else:
            display = frame[
                ["EmployeeID", "FirstName", "LastName", "DepartmentName", "JobRole",
                 "JobLevel", "MonthlyIncome", "HireDate", "EmploymentStatus"]
            ].copy()
            display["MonthlyIncome"] = money_column(display["MonthlyIncome"])
            display.columns = [
                "Employee ID", "First name", "Last name", "Department", "Role",
                "Level", "Monthly income", "Hire date", "Status",
            ]
            st.dataframe(display, width="stretch", hide_index=True)

    with tab_onboard:
        st.subheader("Onboard a new employee")
        st.caption("Written to the OLTP `employees` table.")

        if not RBAC.has_permission(active_role, Permission.ONBOARD_EMPLOYEE):
            st.markdown(
                f"""
                <div class="rbac-restricted-box">
                    <div class="rbac-restricted-title"> Access Restricted: Onboarding Disabled</div>
                    <p class="rbac-restricted-desc">
                        Adding new personnel requires <strong> HR Administrator</strong> privileges.<br/>
                        Your active role (<strong>{role_info['title']}</strong>) operates in <em>{role_info['badge']}</em> mode.
                        Switch to <strong> HR Administrator</strong> in the sidebar to onboard new employees.
                    </p>
                </div>
                """,
                unsafe_allow_html=True,
            )
        else:
            o1, o2 = st.columns(2)
            first_name = guarded_input(
                o1,
                "First name *",
                "onb_first",
                kind="name",
                max_chars=MAX_NAME,
                validator=lambda v: validate_name(v, "First name"),
                help="Letters, spaces, hyphens and apostrophes only.",
            )
            last_name = guarded_input(
                o2,
                "Last name *",
                "onb_last",
                kind="name",
                max_chars=MAX_NAME,
                validator=lambda v: validate_name(v, "Last name"),
                help="Letters, spaces, hyphens and apostrophes only.",
            )

            e1, e2, e3 = st.columns(3)
            email = guarded_input(
                e1,
                "Email *",
                "onb_email",
                kind="email",
                max_chars=MAX_EMAIL,
                placeholder="name@example.com",
                validator=validate_email,
                help=(
                    "Checked as soon as you leave the box. Letters, digits "
                    "and . _ % + - only."
                ),
            )
            phone = guarded_input(
                e2,
                "Phone",
                "onb_phone",
                kind="phone",
                max_chars=PHONE_DIGITS,
                placeholder="9XXXXXXXXX",
                validator=lambda v: validate_phone(v, "Phone"),
                help=(
                    f"Digits only, exactly {PHONE_DIGITS} of them, starting "
                    f"with 6, 7, 8 or 9. Letters and symbols are refused."
                ),
            )
            gender = e3.selectbox("Gender", ["", "Female", "Male", "Other"])

            d1, d2 = st.columns(2)
            department_id = d1.selectbox(
                "Department *",
                list(department_names.keys()),
                format_func=lambda k: department_names[k],
            )
            job_role = guarded_input(
                d2,
                "Job role *",
                "onb_role",
                kind="role",
                default="Analyst",
                max_chars=MAX_ROLE,
                validator=lambda v: validate_role(v, "Job role"),
                help="For example: Analyst, Senior Analyst, Team Lead.",
            )

            j1, j2, j3 = st.columns(3)
            hire_date = j1.date_input("Hire date *", value=date.today())
            job_level = j2.number_input(
                "Job level", 1, 10, 2, help="Whole number between 1 and 10."
            )
            monthly_income = j3.number_input(
                "Monthly income (INR)",
                0,
                9_999_999,
                50_000,
                step=1_000,
                help="Whole rupees, cannot be negative.",
            )

            managers = employees_mgr.distinct_managers()
            manager_options = ["None"] + [
                f"{m['EmployeeID']} - {m['name']} ({m['JobRole']})" for m in managers
            ]
            manager_choice = st.selectbox("Manager", manager_options)
            submitted = st.button("Add employee", type="primary")

            if submitted:
                # Check everything first so the user sees all the problems at
                # once, instead of one per submit.
                checker = FormValidator()
                checker.name("First name", first_name)
                checker.name("Last name", last_name)
                checker.email("Email", email)
                checker.phone("Phone", phone)
                checker.role("Job role", job_role)
                checker.number("Monthly income", monthly_income, minimum=0)

                if not checker.ok:
                    show_form_errors(checker.errors, "Cannot add employee")
                else:
                    try:
                        manager_id = (
                            None
                            if manager_choice == "None"
                            else manager_choice.split(" - ")[0]
                        )
                        new_employee = Employee(
                            employee_id=employees_mgr.next_employee_id(),
                            first_name=clean(first_name),
                            last_name=clean(last_name),
                            email=checker.values["Email"],
                            phone_number=checker.values["Phone"],
                            gender=gender or None,
                            hire_date=hire_date,
                            department_id=department_id,
                            job_role=clean(job_role),
                            job_level=int(job_level),
                            monthly_income=float(monthly_income),
                            manager_employee_id=manager_id,
                        )
                        employees_mgr.add_employee(new_employee)
                        clear_guarded_inputs(
                            "onb_first", "onb_last", "onb_email",
                            "onb_phone", "onb_role",
                        )
                        st.success(
                            f"Added {new_employee.full_name} as "
                            f"{new_employee.employee_id}."
                        )

                        # Checked through version_count() rather than the DAL's
                        # return value: version_count predates this and is
                        # already used by the transfer tab, so a stale imported
                        # module degrades this into a warning instead of a
                        # traceback after the row has already been committed.
                        if employees_mgr.version_count(new_employee.employee_id) >= 1:
                            st.info(
                                "Also written to `dim_employee`, so the "
                                "headcount KPIs on the dashboard already include "
                                "them. Their history starts on their hire date "
                                f"({new_employee.hire_date:%d %b %Y}), and the next "
                                "DW rebuild keeps that same row."
                            )
                        else:
                            st.warning(
                                f"{new_employee.employee_id} is saved in the OLTP "
                                "`employees` table but has **no** `dim_employee` "
                                "row, so the dashboard headcount will not include "
                                "them. A DW refresh will pick them up: employees "
                                "without a staging history row are now rebuilt "
                                "from current OLTP state."
                            )
                    except Exception as exc:  # noqa: BLE001 - one place to report
                        show_action_error(
                            exc,
                            "add the employee",
                            on_duplicate=lambda: st.error(
                                duplicate_email_hint(
                                    employees_mgr, checker.values["Email"]
                                )
                            ),
                        )

    with tab_transfer:
        st.subheader("Change department (SCD Type 2)")
        st.caption(
            "Saving here updates OLTP *and* closes the current "
            "Dim_Employee version, inserting a new one."
        )

        employee_id = employee_picker(employees_mgr, key="transfer")
        if employee_id is None:
            empty_state("No active employee matches that search.")
        else:
            record = employees_mgr.get_employee(employee_id)
            st.write(
                {
                    "Employee ID": record.employee_id,
                    "Name": record.full_name,
                    "Current department": record.department_name,
                    "Job role": record.job_role,
                    "Level": record.job_level,
                    "Monthly income": money(record.monthly_income),
                    "SCD2 versions": employees_mgr.version_count(record.employee_id),
                }
            )

            if not RBAC.has_permission(active_role, Permission.TRANSFER_EMPLOYEE_SCD2):
                st.markdown(
                    f"""
                    <div class="rbac-restricted-box">
                        <div class="rbac-restricted-title"> Access Restricted: Department Transfer Locked</div>
                        <p class="rbac-restricted-desc">
                            Executing SCD Type 2 employee transfers requires <strong> HR Administrator</strong> or <strong> Department Manager</strong> permissions.<br/>
                            Your active role (<strong>{role_info['title']}</strong>) is restricted to read-only analytical mode.
                        </p>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
            else:
                new_department = st.selectbox(
                    "Move to department",
                    [
                        k for k in department_names
                        if k != record.department_id
                    ],
                    format_func=lambda k: department_names[k],
                )
                effective = st.date_input(
                    "Effective from",
                    value=date.today(),
                    help=(
                        "Cannot be earlier than the hire date, "
                        f"{record.hire_date:%d %b %Y}."
                    ),
                )

                if st.button("Apply transfer", type="primary"):
                    if record.hire_date and effective < record.hire_date:
                        show_form_errors(
                            {
                                "Effective from": (
                                    f"Cannot be before the hire date "
                                    f"({record.hire_date:%d %b %Y})."
                                )
                            },
                            "Cannot apply transfer",
                        )
                    else:
                        try:
                            outcome = employees_mgr.change_department(
                                record.employee_id, new_department, effective
                            )
                            st.success(
                                f"{outcome['employee_name']} moved from "
                                f"{outcome['from_department']['department_name']} to "
                                f"{outcome['to_department']['department_name']} "
                                f"on {outcome['effective_date']}."
                            )
                            st.caption(
                                f"Dim_Employee now holds "
                                f"{outcome['scd2_version']} version(s) for "
                                f"this employee."
                            )
                        except Exception as exc:  # noqa: BLE001 - one place to report
                            show_action_error(exc, "transfer the employee")

            st.divider()
            st.markdown("**Current SCD2 timeline**")
            history = employees_mgr.employee_history(record.employee_id)
            if history.empty:
                empty_state("No warehouse history for this employee yet.")
            else:
                timeline = history.copy()
                timeline["IsCurrent"] = timeline["IsCurrent"].map(
                    {1: "current", 0: "closed"}
                )
                timeline["MonthlyIncome"] = money_column(timeline["MonthlyIncome"])
                timeline.columns = [
                    "Employee SK", "Department", "Role", "Level", "Salary",
                    "Valid from", "Valid to", "State", "Years of service",
                ]
                st.dataframe(timeline, width="stretch", hide_index=True)


# ==========================================================================
# [PAGE 3: PROJECT MANAGEMENT & RESOURCE ALLOCATION]
# ==========================================================================
# Purpose:
# 1. Project List Tab: Browse and search organizational projects and staffing.
# 2. Create Project Tab: Register new projects in OLTP `projects`.
# 3. Assign Employee Tab: Allocate employee bandwidth (0.01% - 100%) with strict
#    cumulative workload ceiling checks and non-blocking SMTP notification email.
# ==========================================================================

elif page == PAGE_PROJECTS:
    st.title("Projects")

    tab_projects, tab_create, tab_assign = st.tabs(
        ["Project list", "Create project", "Assign employee"]
    )

    project_statuses = ["Planned", "In Progress", "Completed", "On Hold", "Cancelled"]

    with tab_projects:
        p1, p2, p3 = st.columns([3, 2, 2])
        search = p1.text_input(
            "Search",
            placeholder="project name or id",
            max_chars=100,
        )
        status = p2.selectbox("Status", ["All"] + project_statuses)
        project_type = p3.selectbox(
            "Type", ["All"] + projects_mgr.distinct_types()
        )

        frame = projects_mgr.list_projects(
            search=search or None,
            project_status=None if status == "All" else status,
            project_type=None if project_type == "All" else project_type,
            limit=500,
        )
        st.caption(f"{len(frame)} project(s) shown (capped at 500).")
        if frame.empty:
            empty_state("No project matches this filter.")
        else:
            display = frame[
                ["ProjectID", "ProjectName", "ProjectType", "ProjectStatus",
                 "ProjectManagerName", "ProjectStartDate", "ProjectEndDate",
                 "assignment_count"]
            ].copy()
            display.columns = [
                "Project ID", "Name", "Type", "Status", "Manager",
                "Start", "End", "Assignments",
            ]
            st.dataframe(display, width="stretch", hide_index=True)

    with tab_create:
        st.subheader("Create a project")

        if not RBAC.has_permission(active_role, Permission.CREATE_PROJECT):
            st.markdown(
                f"""
                <div class="rbac-restricted-box">
                    <div class="rbac-restricted-title"> Access Restricted: Project Creation Locked</div>
                    <p class="rbac-restricted-desc">
                        Registering organizational projects requires <strong> HR Administrator</strong> privileges.<br/>
                        Your active role (<strong>{role_info['title']}</strong>) is restricted from adding new projects.
                    </p>
                </div>
                """,
                unsafe_allow_html=True,
            )
        else:
            managers = employees_mgr.distinct_managers()
            manager_options = ["None"] + [
                f"{m['EmployeeID']} - {m['name']} ({m['JobRole']})" for m in managers
            ]

            c1, c2 = st.columns(2)
            project_name = guarded_input(
                c1,
                "Project name *",
                "proj_name",
                kind="text",
                max_chars=MAX_PROJECT_NAME,
                validator=lambda v: validate_text(v, "Project name"),
                help=f"Up to {MAX_PROJECT_NAME} characters.",
            )
            chosen_type = c2.selectbox(
                "Project type",
                projects_mgr.distinct_types() or ["Data Analytics"],
            )

            s1, s2, s3 = st.columns(3)
            start = s1.date_input("Start date *", value=date.today())
            end = s2.date_input("End date", value=None)
            project_state = s3.selectbox("Status", project_statuses, index=1)

            manager_choice = st.selectbox("Project manager", manager_options)
            created = st.button("Create project", type="primary")

            if created:
                if not clean(project_name):
                    show_form_errors(
                        {"Project name": "Project name is required."},
                        "Cannot create project",
                    )
                elif end and start and end < start:
                    show_form_errors(
                        {"End date": "End date cannot be before the start date."},
                        "Cannot create project",
                    )
                else:
                    try:
                        project = Project(
                            project_id=projects_mgr.next_project_id(),
                            project_name=clean(project_name),
                            project_type=chosen_type,
                            project_start_date=start,
                            project_end_date=end,
                            project_status=project_state,
                            project_manager_employee_id=(
                                None if manager_choice == "None"
                                else manager_choice.split(" - ")[0]
                            ),
                        )
                        projects_mgr.add_project(project)
                        clear_guarded_inputs("proj_name")
                        st.success(
                            f"Created {project.project_id} - {project.project_name}."
                        )
                    except Exception as exc:  # noqa: BLE001 - one place to report
                        show_action_error(exc, "create the project")

    with tab_assign:
        st.subheader("Assign an employee to a project")

        if not RBAC.has_permission(active_role, Permission.ASSIGN_EMPLOYEE):
            st.markdown(
                f"""
                <div class="rbac-restricted-box">
                    <div class="rbac-restricted-title"> Access Restricted: Staffing Allocation Locked</div>
                    <p class="rbac-restricted-desc">
                        Allocating employees to projects requires <strong> HR Administrator</strong> or <strong> Department Manager</strong> permissions.<br/>
                        Your active role (<strong>{role_info['title']}</strong>) is restricted to read-only project monitoring.
                    </p>
                </div>
                """,
                unsafe_allow_html=True,
            )
        else:
            active_projects = projects_mgr.list_projects(
                project_status="In Progress", limit=400
            )
            if active_projects.empty:
                empty_state("No in-progress project to assign against.")
            else:
                project_lookup = {
                    f"{row.ProjectID} - {row.ProjectName}": row.ProjectID
                    for row in active_projects.itertuples()
                }
                chosen_project = st.selectbox(
                    "Project", list(project_lookup.keys()), key="assign_project"
                )
                project_id = project_lookup[chosen_project]
                project_name = active_projects.loc[
                    active_projects["ProjectID"] == project_id, "ProjectName"
                ].iloc[0]

                person_id = employee_picker(employees_mgr, key="assign")
                if person_id is None:
                    empty_state("No active employee matches that search.")
                else:
                    person = employees_mgr.get_employee(person_id)
                    person_name = person.full_name

                    a1, a2, a3 = st.columns(3)
                    role = guarded_input(
                        a1,
                        "Assignment role *",
                        "asg_role",
                        kind="role",
                        default="Contributor",
                        max_chars=MAX_ROLE,
                        validator=lambda v: validate_role(v, "Assignment role"),
                        help="For example: Contributor, Lead, Reviewer.",
                    )
                    allocation = a2.slider("Allocation %", 5, 100, 50, step=5)
                    start = a3.date_input("Start date", value=date.today())

                    current = projects_mgr.current_allocation(person_id)
                    st.info(
                        f"{person_name} is currently allocated "
                        f"{current:.0f}% across all projects."
                    )

                    if st.button("Create assignment", type="primary"):
                        checker = FormValidator()
                        checker.role("Assignment role", role)
                        checker.number(
                            "Allocation %", allocation, minimum=0.01, maximum=100
                        )
                        if not checker.ok:
                            show_form_errors(checker.errors, "Cannot create assignment")
                        else:
                            try:
                                projects_mgr.add_assignment(
                                    person_id,
                                    project_id,
                                    clean(role),
                                    float(allocation),
                                    start,
                                )
                                st.success(
                                    f"Assigned to {chosen_project.split(' - ')[0]} at "
                                    f"{allocation}%."
                                )

                                # The assignment is already committed, so the
                                # notification is a separate, last step that
                                # can only add information: it reports the
                                # outcome instead of raising, which keeps an
                                # unusable email id from undoing the booking
                                # or taking the page down.
                                notice = get_notifier().notify_assignment(
                                    employee_id=person_id,
                                    employee_name=person_name,
                                    employee_email=person.email,
                                    project_id=project_id,
                                    project_name=project_name,
                                    assignment_role=clean(role),
                                    allocation_percentage=float(allocation),
                                    start_date=start,
                                )
                                if notice.sent:
                                    st.success(
                                        f"Email sent to {person_name} "
                                        f"({notice.recipient})."
                                    )
                                elif notice.invalid_address:
                                    st.warning(notice.detail)
                                elif notice.not_configured:
                                    st.info(notice.detail)
                                else:
                                    st.warning(notice.detail)
                            except Exception as exc:  # noqa: BLE001 - one place to report
                                show_action_error(exc, "create the assignment")

        st.divider()
        st.markdown("**Active assignments**")
        active = projects_mgr.list_assignments(
            assignment_status="Active", limit=300
        )
        if active.empty:
            empty_state("No active assignments.")
        else:
            display = active[
                ["AssignmentID", "employee_name", "DepartmentName", "ProjectID",
                 "ProjectName", "AssignmentRole", "AllocationPercentage",
                 "AssignmentStartDate"]
            ].copy()
            display.columns = [
                "Assignment ID", "Employee", "Department", "Project ID",
                "Project", "Role", "Allocation %", "Start",
            ]
            st.dataframe(display, width="stretch", hide_index=True)


# ==========================================================================
# [PAGE 4: PERFORMANCE APPRAISAL REVIEWS]
# ==========================================================================
# Purpose:
# 1. Submit Review Tab: Record annual employee appraisals with rating (1-5),
#    performance score (0-100), promotion recommendation, and 1-per-day collision guard.
# 2. Review History Tab: Browse past appraisals, filter by rating/department,
#    and audit evaluations.
# ==========================================================================

elif page == PAGE_REVIEWS:
    st.title("Performance reviews")

    tab_submit, tab_list = st.tabs(["Submit review", "Review history"])

    with tab_submit:
        st.subheader("Submit a performance review")
        st.caption("Written to the OLTP `performance_reviews` table.")

        if not RBAC.has_permission(active_role, Permission.SUBMIT_REVIEW):
            st.markdown(
                f"""
                <div class="rbac-restricted-box">
                    <div class="rbac-restricted-title"> Access Restricted: Appraisal Submission Locked</div>
                    <p class="rbac-restricted-desc">
                        Submitting performance appraisals requires <strong> HR Administrator</strong> or <strong> Department Manager</strong> permissions.<br/>
                        Your active role (<strong>{role_info['title']}</strong>) is restricted to read-only review history exploration.
                    </p>
                </div>
                """,
                unsafe_allow_html=True,
            )
        else:
            person_id = employee_picker(employees_mgr, key="review", status=None)
            if person_id is None:
                empty_state("No employee matches that search.")
            else:
                v1, v2, v3 = st.columns(3)
                review_date = v1.date_input("Review date", value=date.today())
                rating = v2.slider("Rating (1-5)", 1.0, 5.0, 3.0, step=0.5)
                score = v3.slider("Performance score", 0, 100, 60)

                promotion = st.checkbox(
                    "Recommend for promotion",
                    value=(rating >= 4 and score >= 75),
                    help=(
                        "Defaults to the warehouse rule: rating >= 4 "
                        "and score >= 75."
                    ),
                )

                suggestion = rating >= 4 and score >= 75
                if suggestion and not promotion:
                    st.warning(
                        "The warehouse rule would recommend a promotion for "
                        "this rating and score."
                    )

                if st.button("Submit review", type="primary"):
                    try:
                        review = Review(
                            review_id=reviews_mgr.next_review_id(),
                            employee_id=person_id,
                            review_date=review_date,
                            rating=float(rating),
                            performance_score=float(score),
                            promotion_recommendation="Yes" if promotion else "No",
                        )
                        reviews_mgr.add_review(review)
                        st.success(
                            f"Saved {review.review_id} for {person_id}."
                        )
                        st.info(
                            "It will appear on the dashboard after the next "
                            "fact-table refresh."
                        )
                    except Exception as exc:  # noqa: BLE001 - one place to report
                        show_action_error(exc, "submit the review")

    with tab_list:
        r1, r2, r3 = st.columns([3, 2, 2])
        search = r1.text_input(
            "Search",
            placeholder="employee or review id",
            max_chars=100,
        )
        min_rating = r2.slider("Minimum rating", 1.0, 5.0, 1.0, step=0.5)
        limit = r3.slider("Rows", 50, 1000, 250, step=50)

        frame = reviews_mgr.list_reviews(
            min_rating=min_rating if min_rating > 1 else None,
            limit=limit,
        )
        if search:
            needle = search.lower()
            mask = (
                frame["employee_name"].str.lower().str.contains(needle, na=False)
                | frame["ReviewID"].str.lower().str.contains(needle, na=False)
            )
            frame = frame[mask]

        st.caption(f"{len(frame)} review(s) shown.")
        if frame.empty:
            empty_state("No review matches this filter.")
        else:
            display = frame[
                ["ReviewID", "employee_name", "DepartmentName", "ReviewDate",
                 "Rating", "PerformanceScore", "PromotionRecommendation"]
            ].copy()
            display.columns = [
                "Review ID", "Employee", "Department", "Date",
                "Rating", "Score", "Promotion",
            ]
            st.dataframe(
                display,
                width="stretch",
                hide_index=True,
                column_config={
                    # Explicit format for the same reason as the attrition
                    # risk score: the default "percent" format multiplies a
                    # float by 100, so a score of 72 was labelled "7200%".
                    "Score": st.column_config.ProgressColumn(
                        "Score",
                        min_value=0,
                        max_value=100,
                        format="%.0f",
                    ),
                },
            )


# ==========================================================================
# [PAGE 5: DATA WAREHOUSE HEALTH, SCD TYPE 2 RECONCILIATION & ETL]
# ==========================================================================
# Purpose:
# 1. SCD2 Explorer Tab: Replays department headcount history over time,
#    inspects pay progression timelines, and reviews historical SCD2 versions.
# 2. Warehouse Contents Tab: Audits row counts across dimensions and fact tables.
# 3. ETL Load Log Tab: Displays procedure run history from `dw_load_log`.
# 4. Sync & Health Tab: Evaluates 7 core reconciliation invariants, inspects
#    pending attribute drift, triggers incremental delta syncs, and executes
#    full warehouse rebuilds.
# ==========================================================================

elif page == PAGE_WAREHOUSE:
    st.title("Data warehouse")

    tab_scd2, tab_quality, tab_load, tab_sync = st.tabs(
        ["SCD2 explorer", "Warehouse contents", "ETL load log", "Sync & health"]
    )

    with tab_scd2:
        st.subheader("Slowly changing dimension explorer")
        st.caption(
            "Dim_Employee stores one row per version. Historical rows are "
            "never overwritten, which is what makes point-in-time facts "
            "reconstructable."
        )

        employee_id = employee_picker(employees_mgr, key="scd2", status=None)
        if employee_id is None:
            empty_state("No employee matches that search.")
        else:
            try:
                progression = analytics_mgr.pay_progression(employee_id)
            except Exception as exc:  # noqa: BLE001 - one place to report
                show_action_error(exc, "load the pay progression")
                progression = pd.DataFrame()

            if not progression.empty:
                display = progression[
                    ["EffectiveStartDate", "EffectiveEndDate", "DepartmentName",
                     "JobRole", "MonthlyIncome", "prev_salary",
                     "salary_change_pct", "change_type", "IsCurrent"]
                ].copy()
                display["IsCurrent"] = display["IsCurrent"].map(
                    {1: "current", 0: "closed"}
                )
                display["Valid to"] = display["EffectiveEndDate"].replace(
                    "9999-12-31", "open"
                )
                display["MonthlyIncome"] = money_column(display["MonthlyIncome"])
                display["prev_salary"] = money_column(display["prev_salary"])
                display = display[
                    ["EffectiveStartDate", "Valid to", "DepartmentName", "JobRole",
                     "MonthlyIncome", "prev_salary", "salary_change_pct",
                     "change_type", "IsCurrent"]
                ]
                display.columns = [
                    "Valid from", "Valid to", "Department", "Role", "Salary",
                    "Previous salary", "Change %", "Change type", "State",
                ]
                st.dataframe(
                    display,
                    width="stretch",
                    hide_index=True,
                    column_config={
                        "Change %": st.column_config.NumberColumn(format="%.2f %%")
                    },
                )

        st.divider()
        st.markdown("**Version counts by change type**")
        stats = analytics_mgr.scd2_version_stats()
        if stats.empty:
            empty_state("No SCD2 rows yet.")
        else:
            figure = px.bar(
                stats,
                x="change_type",
                y="version_rows",
                color="employees",
                labels={
                    "change_type": "Change type",
                    "version_rows": "Version rows",
                    "employees": "Distinct employees",
                },
                color_continuous_scale="Blues",
            )
            figure.update_layout(height=340, coloraxis_showscale=False)
            st.plotly_chart(figure, width="stretch")

        st.markdown("**Recent department transfers**")
        transfers = analytics_mgr.scd2_transfers(limit=50)
        if transfers.empty:
            empty_state("No transfers recorded.")
        else:
            st.dataframe(transfers, width="stretch", hide_index=True)

    with tab_quality:
        st.subheader("Warehouse contents")

        counts = analytics_mgr.warehouse_row_counts()
        figure = px.bar(
            counts,
            x="table_name",
            y="rows_count",
            labels={"table_name": "", "rows_count": "Rows"},
            color_discrete_sequence=PALETTE,
        )
        figure.update_layout(height=360, showlegend=False)
        st.plotly_chart(figure, width="stretch")
        st.dataframe(counts, width="stretch", hide_index=True)

        st.divider()
        st.markdown("**Headcount history by department (replayed from SCD2)**")
        history = analytics_mgr.department_headcount_history()
        if history.empty:
            empty_state("No history yet.")
        else:
            figure = px.area(
                history,
                x="snapshot_year",
                y="headcount",
                color="DepartmentName",
                labels={"snapshot_year": "Year", "headcount": "Active headcount"},
            )
            figure.update_layout(height=420)
            st.plotly_chart(figure, width="stretch")
            st.dataframe(history, width="stretch", hide_index=True)

    with tab_load:
        st.subheader("ETL load log")
        st.caption("Each row is one sp_load_oltp_master / sp_load_dw_master run.")

        log = analytics_mgr.load_log()
        if log.empty:
            empty_state("No ETL runs recorded yet.")
        else:
            st.dataframe(log, width="stretch", hide_index=True)

        st.info(
            "Employee changes made in this app are written to Dim_Employee "
            "straight away, so a full rebuild is only needed to repair "
            "history. Use the Sync & health tab for routine catch-up."
        )

    with tab_sync:
        st.subheader("OLTP to warehouse reconciliation")
        st.caption(
            "Every metric below compares the two systems directly. The "
            "invariants are the ones that must hold for the point-in-time "
            "fact join to be trustworthy; a non-zero value means the "
            "warehouse is not consistent with itself."
        )

        try:
            report = analytics_mgr.reconciliation()
        except Exception as exc:  # noqa: BLE001 - one place to report
            show_action_error(exc, "load the reconciliation report")
            report = None

        # A sync run from the previous click hands back the reconciliation
        # it measured *after* repairing, so that row replaces the one read
        # above and the whole panel below - metrics, invariant table and
        # the pass/fail banner - redraws as already repaired. Without this
        # the banner kept claiming an invariant was broken until the page
        # was refreshed, because Streamlit has already rendered it by the
        # time the button is handled further down.
        #
        # Both keys are popped, so the panel falls back to a live read on
        # the next interaction instead of showing a snapshot for ever.
        fresh_report = st.session_state.pop("wh_sync_report", None)
        sync_notice = st.session_state.pop("wh_sync_notice", None)
        if fresh_report is not None:
            report = fresh_report
        if sync_notice:
            st.success(sync_notice)

        if report is not None:
            headline = st.columns(4)
            headline[0].metric("Employees in OLTP", f"{report['oltp_employees']:,}")
            headline[1].metric("Employees in warehouse", f"{report['dw_employees']:,}")
            headline[2].metric("Open Dim_Employee versions",
                               f"{report['dw_open_versions']:,}")
            headline[3].metric("Unpublished changes",
                               f"{report['pending_changes']:,}")

            st.markdown("**Invariants**")
            checks = pd.DataFrame(
                [
                    {
                        "Check": item["label"],
                        "Rows affected": item["value"],
                        "Status": "ok" if item["value"] == 0 else "broken",
                    }
                    for item in report["invariants"].values()
                ]
            )
            if report["invariants_ok"]:
                st.success("All invariants hold: the warehouse agrees with the OLTP.")
            else:
                st.error(
                    "At least one invariant is broken. Run a full rebuild "
                    "(`python etl/run_pipeline.py`) to repair it."
                )
            st.dataframe(
                checks,
                width="stretch",
                hide_index=True,
                column_config={
                    "Rows affected": st.column_config.NumberColumn(format="%d"),
                },
            )

            st.caption(
                f"{report['employees_history_newer_than_oltp']:,} employees "
                "have a staging history that runs ahead of the OLTP snapshot "
                "(promotions generated from it). That is by design, and is "
                "why history - not the employees table - is the source of "
                "truth for those versions."
            )

            st.divider()
            st.markdown("**Publish pending changes**")

            if not RBAC.has_permission(active_role, Permission.TRIGGER_DW_SYNC):
                st.markdown(
                    f"""
                    <div class="rbac-restricted-box">
                        <div class="rbac-restricted-title"> ETL Maintenance Locked</div>
                        <p class="rbac-restricted-desc">
                            Triggering on-demand warehouse delta synchronization requires <strong> HR Administrator</strong> credentials.<br/>
                            Your active role (<strong>{role_info['title']}</strong>) is restricted from executing database ETL procedures.
                        </p>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
            else:
                st.caption(
                    "Seeds Dim_Employee for any employee that has no version, "
                    "versions the ones whose attributes moved, then refreshes "
                    "the reviews a transfer could have re-pointed. No table is "
                    "truncated, so history is untouched."
                )

                lookback = st.number_input(
                    "Fact lookback window (days)",
                    min_value=0,
                    max_value=3650,
                    value=30,
                    step=5,
                    key="sync_lookback",
                    help=(
                        "Reviews dated inside this window are rebuilt on every "
                        "sync, because a transfer can be back dated. Older "
                        "reviews can only need a full rebuild."
                    ),
                )

                if st.button("Sync warehouse", type="primary"):
                    try:
                        outcome = analytics_mgr.sync_warehouse(int(lookback))
                        # Handed to the next run, which redraws the panel from
                        # the post-sync reconciliation.
                        st.session_state["wh_sync_notice"] = (
                            f"Seeded {outcome['employees_seeded']} employee(s), "
                            f"versioned {outcome['employees_versioned']}, "
                            f"refreshed {outcome['fact_rows_refreshed']:,} fact "
                            f"row(s) from {outcome['window_start']}, "
                            f"published {outcome['new_reviews']} new review(s)."
                        )
                        st.session_state["wh_sync_report"] = outcome["reconciliation"]
                        st.rerun()
                    except Exception as exc:  # noqa: BLE001 - one place to report
                        show_action_error(exc, "sync the warehouse")

            if report["pending_changes"]:
                st.markdown("**Unpublished changes**")
                st.caption(
                    "These employees were edited in the OLTP without the "
                    "warehouse being told. Only employees created outside "
                    "the synthesized dataset appear here."
                )
                pending = analytics_mgr.pending_changes(limit=100)
                st.dataframe(pending, width="stretch", hide_index=True)
            else:
                st.caption(
                    "Nothing pending: every employee's warehouse version "
                    "matches their OLTP record."
                )
