"""
Field Validation & Form Integrity
==================================
READING SEQUENCE STEP 2: Understand business input rules.

Reusable validators for form inputs collected by Streamlit.
Each function either returns the cleaned value or raises `ValidationError` with
a message that is safe to display directly to end-users in the UI.

The constraints mirror the OLTP column definitions in `database/03_create_oltp.sql`.
"""

from __future__ import annotations

import re
from typing import Any, Callable

from .exceptions import ValidationError

# ---------------------------------------------------------------------------
# [REGEX PATTERNS - INPUT SAFETY & LOCALIZATION]
# ---------------------------------------------------------------------------

# Apostrophe variants support Indian and international real names (e.g. D'Alia, O'Connor)
_APOS = "'\u2018\u2019"

# Pragmatic business email validation pattern
EMAIL_RE = re.compile(
    r"^[A-Za-z0-9._%+\-" + _APOS + r"]+"
    r"@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}$"
)

# Unicode letter recognition (supports non-ASCII names like José, Müller)
_LETTER = r"[^\W\d_]"

# Name validation: letters, spaces, real-name punctuation
NAME_RE = re.compile(
    rf"^{_LETTER}(?:{_LETTER}|[ .'\u2018\u2019-])*$"
)

# Job role validation: e.g. "Senior Analyst (Actuarial)" or "Lead, Grade 2"
ROLE_RE = re.compile(
    rf"^{_LETTER}[\w .,&/()'\u2018\u2019-]*$"
)

# 10-digit Indian mobile numbers (starts with 6, 7, 8, or 9)
PHONE_RE = re.compile(r"^[6-9][0-9]{9}$")

# Numeric digits only
DIGITS_RE = re.compile(r"^[0-9]+$")

# Control characters (ASCII 0-31 and DEL) to prevent injection/formatting issues
CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")

# ---------------------------------------------------------------------------
# [COLUMN WIDTH LIMITS - MIRRORS MYSQL OLTP SCHEMA]
# ---------------------------------------------------------------------------

PHONE_DIGITS = 10
AGE_MIN = 18
AGE_MAX = 65

MAX_NAME = 100
MAX_EMAIL = 255
MAX_ROLE = 100
MAX_PROJECT_NAME = 255


# ===========================================================================
# [HELPER / UTILITY FUNCTION] - String Sanitization
# ===========================================================================

def clean(value: Any) -> str:
    """
    [HELPER / UTILITY FUNCTION]
    Strips surrounding whitespace, safely converting None to an empty string.
    """
    return "" if value is None else str(value).strip()


# ===========================================================================
# [HELPER / UTILITY FUNCTION] - General Text Validation Primitive
# ===========================================================================

def validate_text(
    value: Any,
    field: str,
    *,
    required: bool = True,
    max_length: int | None = None,
    pattern: re.Pattern[str] | None = None,
    hint: str | None = None,
) -> str:
    """
    [HELPER / UTILITY FUNCTION]
    General-purpose text check: validates presence, control characters, max length, and regex pattern.
    Returns the cleaned string. Raises `ValidationError` on rule failure.
    """
    text = clean(value)

    if not text:
        if required:
            raise ValidationError(f"{field} is required.")
        return ""

    if CONTROL_RE.search(text):
        raise ValidationError(f"{field} cannot contain control characters.")

    if max_length is not None and len(text) > max_length:
        raise ValidationError(
            f"{field} must be at most {max_length} characters (got {len(text)})."
        )

    if pattern is not None and not pattern.match(text):
        raise ValidationError(hint or f"{field} contains invalid characters.")

    return text


# ===========================================================================
# [CORE VALIDATION FUNCTION] - Employee Name
# ===========================================================================

def validate_name(value: Any, field: str = "Name") -> str:
    """
    [CORE VALIDATION FUNCTION]
    Validates first or last name against `MAX_NAME` (100 chars) and `NAME_RE`.
    Allows real-name punctuation (apostrophes, hyphens, spaces).
    """
    return validate_text(
        value,
        field,
        max_length=MAX_NAME,
        pattern=NAME_RE,
        hint=f"{field} may only contain letters, spaces, hyphens, apostrophes and periods.",
    )


# ===========================================================================
# [CORE VALIDATION FUNCTION] - Job / Project Role
# ===========================================================================

def validate_role(value: Any, field: str = "Role") -> str:
    """
    [CORE VALIDATION FUNCTION]
    Validates job titles or assignment roles against `MAX_ROLE` (100 chars) and `ROLE_RE`.
    """
    return validate_text(
        value,
        field,
        max_length=MAX_ROLE,
        pattern=ROLE_RE,
        hint=f"{field} may only contain letters, digits, spaces and . ' - & /",
    )


# ===========================================================================
# [CORE VALIDATION FUNCTION] - Email Address
# ===========================================================================

def validate_email(value: Any, field: str = "Email", required: bool = True) -> str:
    """
    [CORE VALIDATION FUNCTION]
    Validates syntactically valid email address, forces lower-case, and checks against `EMAIL_RE`.
    """
    text = validate_text(
        value,
        field,
        required=required,
        max_length=MAX_EMAIL,
        hint=f"{field} must look like name@example.com - check for a missing @ or domain.",
    )
    if not text:
        return ""
    if not EMAIL_RE.match(text):
        raise ValidationError(
            f"'{text}' is not a valid email address. Expected something like name@example.com."
        )
    return text.lower()


# ===========================================================================
# [CORE VALIDATION FUNCTION] - Mobile Phone Number
# ===========================================================================

def validate_phone(
    value: Any,
    field: str = "Phone",
    required: bool = False,
) -> str | None:
    """
    [CORE VALIDATION FUNCTION]
    Validates exact 10-digit Indian mobile number format (`PHONE_RE`).
    Stored as CHAR(10) in MySQL, so exact length validation prevents space-padding defects.
    """
    text = clean(value)

    if not text:
        if required:
            raise ValidationError(f"{field} is required.")
        return None

    if not DIGITS_RE.match(text):
        raise ValidationError(f"{field} must contain digits only.")

    if len(text) != PHONE_DIGITS:
        raise ValidationError(
            f"{field} must be exactly {PHONE_DIGITS} digits (got {len(text)})."
        )

    if not PHONE_RE.match(text):
        raise ValidationError(
            f"{field} must be a valid 10-digit Indian mobile number, starting with 6, 7, 8 or 9."
        )

    return text


# ===========================================================================
# [CORE VALIDATION FUNCTION] - Integer Digits Only
# ===========================================================================

def validate_digits(
    value: Any,
    field: str,
    *,
    length: int | None = None,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    """
    [CORE VALIDATION FUNCTION]
    Validates numeric-only input (counts, job levels). Rejects signs, decimals, or characters.
    """
    text = clean(value)

    if not text:
        raise ValidationError(f"{field} is required.")

    if not DIGITS_RE.match(text):
        raise ValidationError(f"{field} must be a number - digits only.")

    number = int(text)

    if length is not None and len(text) != length:
        raise ValidationError(
            f"{field} must be exactly {length} digits (got {len(text)})."
        )

    if minimum is not None and number < minimum:
        raise ValidationError(f"{field} must be at least {minimum}.")

    if maximum is not None and number > maximum:
        raise ValidationError(f"{field} must be at most {maximum}.")

    return number


# ===========================================================================
# [HELPER / UTILITY FUNCTION] - Age Range Validation
# ===========================================================================

def validate_age(
    value: Any,
    field: str = "Age",
    minimum: int = AGE_MIN,
    maximum: int = AGE_MAX,
) -> int:
    """
    [HELPER / UTILITY FUNCTION]
    Validates person age bounded within working years (18 to 65).
    """
    number = validate_digits(value, field)
    if number < minimum or number > maximum:
        raise ValidationError(
            f"{field} must be between {minimum} and {maximum} (got {number})."
        )
    return number


# ===========================================================================
# [CORE VALIDATION FUNCTION] - Decimal Numbers (Salary / Allocation / Rating)
# ===========================================================================

def validate_number(
    value: Any,
    field: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
    required: bool = True,
) -> float | None:
    """
    [CORE VALIDATION FUNCTION]
    Validates float/decimal fields with inclusive range (used for MonthlyIncome, AllocationPercentage, Rating).
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise ValidationError(f"{field} is required.")
        return None

    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValidationError(f"{field} must be a number.") from None

    if number != number:  # Guard against NaN
        raise ValidationError(f"{field} must be a number.")

    if minimum is not None and number < minimum:
        raise ValidationError(f"{field} must be at least {minimum:,.0f}.")
    if maximum is not None and number > maximum:
        raise ValidationError(f"{field} must be at most {maximum:,.0f}.")

    return number


# ===========================================================================
# [CORE VALIDATION FUNCTION] - Business Identifier Keys
# ===========================================================================

def validate_identifier(
    value: Any,
    field: str,
    *,
    prefix: str,
    digits: int,
) -> str:
    """
    [CORE VALIDATION FUNCTION]
    Validates structured business natural keys (e.g., EMP-0000001, PROJ-00001, DEPT-001).
    """
    text = validate_text(value, field, max_length=len(prefix) + digits)
    pattern = re.compile(rf"^{re.escape(prefix)}[0-9]{{{digits}}}$")
    if not pattern.match(text):
        raise ValidationError(
            f"{field} must look like {prefix} followed by {digits} digits."
        )
    return text


# ===========================================================================
# [CORE COMPONENT] - FormValidator: Multi-Error Accumulator
# ===========================================================================

class FormValidator:
    """
    [CORE COMPONENT - FORM INTEGRITY ACCUMULATOR]
    Executes multiple field validators and accumulates all errors at once.
    Instead of rejecting a form on the first error and frustrating users,
    `FormValidator` collects all field errors so the UI can highlight them simultaneously.
    """

    def __init__(self) -> None:
        self.errors: dict[str, str] = {}
        self.values: dict[str, Any] = {}

    def _record(self, field: str, message: str) -> None:
        self.errors.setdefault(field, message)

    def _run(self, field: str, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except ValidationError as exc:
            self._record(field, str(exc))
            return None

    def text(self, field: str, value: Any, **kwargs: Any) -> str | None:
        result = self._run(field, validate_text, value, field, **kwargs)
        self.values[field] = result
        return result

    def name(self, field: str, value: Any) -> str | None:
        result = self._run(field, validate_name, value, field)
        self.values[field] = result
        return result

    def role(self, field: str, value: Any) -> str | None:
        result = self._run(field, validate_role, value, field)
        self.values[field] = result
        return result

    def email(self, field: str, value: Any, required: bool = True) -> str | None:
        result = self._run(field, validate_email, value, field, required)
        self.values[field] = result
        return result

    def phone(self, field: str, value: Any, required: bool = False) -> str | None:
        result = self._run(field, validate_phone, value, field, required)
        self.values[field] = result
        return result

    def digits(self, field: str, value: Any, **kwargs: Any) -> int | None:
        result = self._run(field, validate_digits, value, field, **kwargs)
        self.values[field] = result
        return result

    def age(self, field: str, value: Any, **kwargs: Any) -> int | None:
        result = self._run(field, validate_age, value, field, **kwargs)
        self.values[field] = result
        return result

    def number(self, field: str, value: Any, **kwargs: Any) -> float | None:
        result = self._run(field, validate_number, value, field, **kwargs)
        self.values[field] = result
        return result

    @property
    def ok(self) -> bool:
        """Returns True if all validated fields passed with zero errors."""
        return not self.errors

    def __bool__(self) -> bool:
        return self.ok

    def messages(self) -> list[str]:
        """Returns user-facing error message strings formatted as 'Field: Error'."""
        return [f"{field}: {msg}" for field, msg in self.errors.items()]


__all__ = [
    "AGE_MAX",
    "AGE_MIN",
    "EMAIL_RE",
    "MAX_EMAIL",
    "MAX_NAME",
    "MAX_PROJECT_NAME",
    "MAX_ROLE",
    "NAME_RE",
    "PHONE_DIGITS",
    "PHONE_RE",
    "FormValidator",
    "clean",
    "validate_age",
    "validate_digits",
    "validate_email",
    "validate_identifier",
    "validate_name",
    "validate_number",
    "validate_phone",
    "validate_role",
    "validate_text",
]