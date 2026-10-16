"""
Project Assignment Email Notification Service
=============================================
READING SEQUENCE STEP 6: External Notification Services & SMTP Integration.

Dispatches email notifications when an employee is assigned to a project in the UI.

Core Architectural Contract:
----------------------------
1. Non-Blocking / Fault-Tolerant:
   `AssignmentNotifier.notify_assignment()` NEVER raises an unhandled exception.
   It always returns a typed `NotificationResult` object indicating status:
   `sent`, `invalid_address`, `not_configured`, or `failed`.
2. Decoupled from Database Transactions:
   The database write in `ProjectManager.add_assignment()` is committed to MySQL BEFORE
   the email is attempted. A mail relay failure or network timeout must NEVER abort an
   already-committed project booking or crash the Streamlit session.
3. Flexible Configuration:
   Loads configuration from `.env` locally or from Streamlit Cloud's `st.secrets`.
"""

from __future__ import annotations

import logging
import os
import smtplib
from dataclasses import dataclass
from datetime import date
from email.message import EmailMessage
from email.utils import formataddr
from typing import Callable

from dotenv import load_dotenv

from .exceptions import ValidationError
from .validation import validate_email

# Load environment variables from .env file if present
load_dotenv()

# Configure module logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Default SMTP Settings & Status Constants
# ---------------------------------------------------------------------------

DEFAULT_HOST = "localhost"
DEFAULT_PORT = 587
DEFAULT_TIMEOUT = 10.0

# NotificationResult status codes
STATUS_SENT = "sent"
STATUS_INVALID_ADDRESS = "invalid_address"
STATUS_NOT_CONFIGURED = "not_configured"
STATUS_FAILED = "failed"


# ============================================================================
# [UTILITY HELPERS]
# ============================================================================

def _truthy(raw: str | None, default: bool = True) -> bool:
    """
    [HELPER / UTILITY FUNCTION]
    Parses boolean string representation from environment variables ('1', 'true', 'yes', 'on').
    """
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _default_lookup(name: str, fallback: str = "") -> str:
    """
    [HELPER / UTILITY FUNCTION]
    Standard lookup function falling back to os.getenv.
    """
    return os.getenv(name, fallback)


# ============================================================================
# [MAIL SETTINGS CONFIGURATION]
# ============================================================================

@dataclass(frozen=True)
class MailSettings:
    """
    [CORE CONFIGURATION CLASS]
    Encapsulates SMTP server credentials and communication parameters.
    """

    sender: str = ""
    sender_name: str = ""
    host: str = ""
    port: int = DEFAULT_PORT
    username: str = ""
    password: str = ""
    use_tls: bool = True
    timeout: float = DEFAULT_TIMEOUT
    enabled: bool = True

    @classmethod
    def load(
        cls,
        get: Callable[[str, str], str] | None = None,
    ) -> "MailSettings":
        """
        [CORE / IMPORTANT FUNCTION]
        Loads SMTP settings from environment variables or custom lookup callable (e.g. st.secrets).

        Parameters:
        -----------
        get : Callable[[str, str], str] | None
            Function with signature `get(key, fallback) -> value`.
            Defaults to reading `os.getenv`.
        """
        get = get or _default_lookup

        def text(name: str, fallback: str = "") -> str:
            return str(get(name, fallback) or "").strip()

        try:
            port = int(text("SMTP_PORT", str(DEFAULT_PORT)))
        except ValueError:
            logger.warning(
                "SMTP_PORT=%r is not a number, using %d",
                text("SMTP_PORT"),
                DEFAULT_PORT,
            )
            port = DEFAULT_PORT

        try:
            timeout = float(text("SMTP_TIMEOUT", str(DEFAULT_TIMEOUT)))
        except ValueError:
            timeout = DEFAULT_TIMEOUT

        sender = text("MAIL_SENDER")
        return cls(
            sender=sender,
            sender_name=text("MAIL_SENDER_NAME") or sender,
            host=text("SMTP_HOST", DEFAULT_HOST),
            port=port,
            username=text("SMTP_USERNAME"),
            password=text("SMTP_PASSWORD"),
            use_tls=_truthy(text("SMTP_USE_TLS"), default=True),
            timeout=timeout,
            enabled=_truthy(text("MAIL_ENABLED"), default=True),
        )

    @property
    def is_complete(self) -> bool:
        """
        [HELPER / UTILITY FUNCTION]
        Returns True if minimal required settings (sender and host) are present.
        """
        return bool(self.sender and self.host)


# ============================================================================
# [NOTIFICATION RESULT VALUE OBJECT]
# ============================================================================

@dataclass(frozen=True)
class NotificationResult:
    """
    [CORE VALUE OBJECT CLASS]
    Structured result returned after an email dispatch attempt.
    Contains user-friendly explanation safe to render in the Streamlit UI.
    """

    status: str
    recipient: str = ""
    detail: str = ""

    @property
    def sent(self) -> bool:
        """[HELPER / UTILITY FUNCTION] True if email was successfully sent."""
        return self.status == STATUS_SENT

    @property
    def invalid_address(self) -> bool:
        """[HELPER / UTILITY FUNCTION] True if rejected due to bad email syntax."""
        return self.status == STATUS_INVALID_ADDRESS

    @property
    def not_configured(self) -> bool:
        """[HELPER / UTILITY FUNCTION] True if SMTP server is not set up."""
        return self.status == STATUS_NOT_CONFIGURED

    @property
    def failed(self) -> bool:
        """[HELPER / UTILITY FUNCTION] True if SMTP network connection or auth failed."""
        return self.status == STATUS_FAILED

    def __bool__(self) -> bool:
        return self.sent


# ============================================================================
# [ASSIGNMENT NOTIFIER SERVICE]
# ============================================================================

class AssignmentNotifier:
    """
    [CORE SERVICE CLASS - EMAIL DISPATCHER]
    Manages composition and SMTP dispatch of project allocation notification emails.
    """

    def __init__(self, settings: MailSettings | None = None) -> None:
        """
        [HELPER / UTILITY FUNCTION]
        Initializes the notifier with specified or auto-loaded MailSettings.
        """
        self.settings: MailSettings = settings or MailSettings.load()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def notify_assignment(
        self,
        *,
        employee_id: str,
        employee_name: str,
        employee_email: str | None,
        project_id: str,
        project_name: str,
        assignment_role: str,
        allocation_percentage: float,
        start_date: date | None = None,
    ) -> NotificationResult:
        """
        [CORE / IMPORTANT FUNCTION]
        Sends a project assignment notification email to an employee.

        Guaranteed never to raise exceptions. Returns a `NotificationResult` object.

        Parameters:
        -----------
        employee_id : str
            ID of the assigned employee.
        employee_name : str
            Full name of employee.
        employee_email : str | None
            Destination email address.
        project_id : str
            Assigned project ID.
        project_name : str
            Title of assigned project.
        assignment_role : str
            Designation within project.
        allocation_percentage : float
            Allocated percentage of bandwidth.
        start_date : date | None
            Start date of assignment.

        Returns:
        --------
        NotificationResult
            Detailed outcome of the email transmission.
        """
        settings = self.settings

        if not settings.enabled:
            return NotificationResult(
                STATUS_NOT_CONFIGURED,
                recipient=str(employee_email or ""),
                detail=(
                    "No mail sent: notifications are switched off "
                    "(MAIL_ENABLED=0)."
                ),
            )

        if not settings.is_complete:
            return NotificationResult(
                STATUS_NOT_CONFIGURED,
                recipient=str(employee_email or ""),
                detail=(
                    "No mail sent: MAIL_SENDER and SMTP_HOST are not "
                    "configured in .env."
                ),
            )

        # Validate email address syntax
        try:
            recipient = validate_email(
                employee_email, required=False
            ) if employee_email else ""
        except ValidationError as exc:
            return NotificationResult(
                STATUS_INVALID_ADDRESS,
                recipient=str(employee_email or ""),
                detail=f"No mail sent - the employee's email id is not valid: {exc}",
            )

        if not recipient:
            return NotificationResult(
                STATUS_INVALID_ADDRESS,
                detail=(
                    "No mail sent: this employee has no email address on "
                    "file, so there is nobody to notify."
                ),
            )

        message = self._build_message(
            recipient=recipient,
            employee_id=employee_id,
            employee_name=employee_name,
            project_id=project_id,
            project_name=project_name,
            assignment_role=assignment_role,
            allocation_percentage=allocation_percentage,
            start_date=start_date or date.today(),
        )
        return self._send(recipient, message)

    # ------------------------------------------------------------------
    # Message Composition & Transmission
    # ------------------------------------------------------------------

    def _build_message(
        self,
        *,
        recipient: str,
        employee_id: str,
        employee_name: str,
        project_id: str,
        project_name: str,
        assignment_role: str,
        allocation_percentage: float,
        start_date: date,
    ) -> EmailMessage:
        """
        [HELPER / UTILITY FUNCTION]
        Formats the plain-text MIME email body and headers.
        """
        settings = self.settings
        message = EmailMessage()
        message["Subject"] = (
            f"Project allocation: {project_name} ({project_id})"
        )
        message["From"] = formataddr(
            (settings.sender_name or settings.sender, settings.sender)
        )
        message["To"] = recipient
        message.set_content(
            f"Hello {employee_name},\n"
            "\n"
            "You have been allocated to the project below.\n"
            "\n"
            f"  Employee     : {employee_id} ({employee_name})\n"
            f"  Project      : {project_id} - {project_name}\n"
            f"  Role         : {assignment_role}\n"
            f"  Allocation   : {allocation_percentage:.0f}%\n"
            f"  Start date   : {start_date:%d %b %Y}\n"
            "\n"
            "Please raise any scheduling conflict with your manager.\n"
            "\n"
            "-- \n"
            "Employee Analytics & Data Warehouse System\n"
        )
        return message

    def _send(self, recipient: str, message: EmailMessage) -> NotificationResult:
        """
        [CORE / IMPORTANT FUNCTION]
        Connects to the configured SMTP server, handles STARTTLS / authentication,
        and transmits the email.
        Catches any socket/auth exceptions and returns a failure NotificationResult.
        """
        settings = self.settings
        try:
            with smtplib.SMTP(
                settings.host, settings.port, timeout=settings.timeout
            ) as server:
                server.ehlo()
                if settings.use_tls:
                    server.starttls()
                    server.ehlo()
                if settings.username:
                    server.login(settings.username, settings.password)
                server.send_message(message)
        except Exception as exc:  # noqa: BLE001 - mail failure must never crash application
            logger.warning(
                "Assignment notification to %s failed: %s", recipient, exc
            )
            return NotificationResult(
                STATUS_FAILED,
                recipient=recipient,
                detail=f"The assignment was saved, but the email could not be "
                       f"sent ({type(exc).__name__}: {exc}).",
            )

        logger.info("Assignment notification sent to %s", recipient)
        return NotificationResult(
            STATUS_SENT,
            recipient=recipient,
            detail=f"Email sent to {recipient}.",
        )


__all__ = [
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "DEFAULT_TIMEOUT",
    "STATUS_FAILED",
    "STATUS_INVALID_ADDRESS",
    "STATUS_NOT_CONFIGURED",
    "STATUS_SENT",
    "AssignmentNotifier",
    "MailSettings",
    "NotificationResult",
]
