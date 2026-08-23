"""Email alert destination.

This module provides EmailAlertDestination, which sends alerts via SMTP
to configured email recipients. Supports both plain text and HTML email
formats with severity-based styling.

Configuration is typically loaded from environment variables:
    PHLO_ALERT_EMAIL_SMTP_HOST: SMTP server hostname
    PHLO_ALERT_EMAIL_SMTP_PORT: SMTP server port (default: 587)
    PHLO_ALERT_EMAIL_SMTP_USER: SMTP username
    PHLO_ALERT_EMAIL_SMTP_PASSWORD: SMTP password
    PHLO_ALERT_EMAIL_RECIPIENTS: Comma-separated recipient list

Examples:
    Basic usage:
        >>> from phlo_alerting.destinations.email import EmailAlertDestination
        >>> dest = EmailAlertDestination(
        ...     smtp_host="smtp.gmail.com",
        ...     smtp_user="alerts@example.com",
        ...     smtp_password="secret",
        ...     recipients=["team@example.com"]
        ... )
        >>> dest.send(alert)
        True

See Also:
    manager.AlertDestination: Base class defining the interface.
    settings.AlertingSettings: Configuration model for email settings.

"""

from __future__ import annotations

import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

from phlo.logging import get_logger
from phlo_alerting.manager import Alert, AlertDestination, AlertSeverity

logger = get_logger(__name__)


class EmailAlertDestination(AlertDestination):
    """Send alerts via email using SMTP.
        Concrete implementation of AlertDestination that delivers alerts
    to email recipients via SMTP. Supports TLS encryption and
    authentication. Formats alerts as both plain text and HTML with
    severity-based color coding.

    Examples:
            >>> dest = EmailAlertDestination(
            ...     smtp_host="smtp.example.com",
            ...     smtp_port=587,
            ...     recipients=["admin@example.com"]
            ... )
            >>> isinstance(dest, AlertDestination)
            True
    """

    def __init__(
        self,
        smtp_host: str,
        smtp_port: int = 587,
        smtp_user: Optional[str] = None,
        smtp_password: Optional[str] = None,
        recipients: Optional[list[str]] = None,
    ):
        """Initialize email destination.
        Creates an EmailAlertDestination instance with SMTP configuration.
        The destination is ready to send alerts once initialized, though
        actual SMTP connections are established per-send.

        Raises: None; validation occurs during send().
        Examples:
            >>> dest = EmailAlertDestination(
            ...     smtp_host="smtp.example.com",
            ...     smtp_port=587,
            ...     smtp_user="alerts@example.com",
            ...     recipients=["team@example.com"]
            ... )
        """
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.smtp_user = smtp_user
        self.smtp_password = smtp_password
        self.recipients = recipients or []

    def send(self, alert: Alert) -> bool:
        """Send alert via email.
                Connects to the SMTP server, authenticates if credentials are
        provided, and sends the alert as a multipart email (plain text + HTML).
        Uses TLS encryption via STARTTLS.

        Raises: None; SMTP errors are caught and logged.
        Examples:
                    >>> from phlo_alerting.manager import Alert, AlertSeverity
                    >>> alert = Alert(
                    ...     title="Test Alert",
                    ...     message="Test message",
                    ...     severity=AlertSeverity.WARNING
                    ... )
                    >>> result = dest.send(alert)
                    >>> isinstance(result, bool)
                    True
        """
        if not self.recipients:
            logger.warning("No email recipients configured")
            return False

        try:
            # Build email
            msg = MIMEMultipart("alternative")
            msg["Subject"] = f"[{alert.severity.value.upper()}] {alert.title}"
            msg["From"] = self.smtp_user or "phlo@example.com"
            msg["To"] = ", ".join(self.recipients)

            # Build plain text and HTML versions
            text_content = self._build_text(alert)
            html_content = self._build_html(alert)

            msg.attach(MIMEText(text_content, "plain"))
            msg.attach(MIMEText(html_content, "html"))

            # Send email
            with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
                server.starttls()
                if self.smtp_user and self.smtp_password:
                    server.login(self.smtp_user, self.smtp_password)
                server.sendmail(msg["From"], self.recipients, msg.as_string())

            return True

        except Exception:
            logger.exception(
                "email_alert_send_failed",
                alert_title=alert.title,
                severity=alert.severity.value,
                asset_name=alert.asset_name,
                run_id=alert.run_id,
                recipient_count=len(self.recipients),
            )
            return False

    def _build_text(self, alert: Alert) -> str:
        """Build plain text email content.
        Constructs a human-readable plain text representation of the alert
        suitable for email clients that don't support HTML.

        Examples:
            >>> from phlo_alerting.manager import Alert, AlertSeverity
            >>> alert = Alert(
            ...     title="Test",
            ...     message="Test message",
            ...     severity=AlertSeverity.ERROR,
            ...     asset_name="test_asset"
            ... )
            >>> text = dest._build_text(alert)
            >>> "Phlo Alert Notification" in text
            True
            >>> "Asset: test_asset" in text
            True
        """
        content = f"""
Phlo Alert Notification
=======================

Title: {alert.title}
Severity: {alert.severity.value.upper()}
Time: {alert.timestamp.isoformat() if alert.timestamp else "N/A"}

Message:
{alert.message}
"""

        if alert.asset_name:
            content += f"\nAsset: {alert.asset_name}"

        if alert.run_id:
            content += f"\nRun ID: {alert.run_id}"

        if alert.error_message:
            content += f"\n\nError Details:\n{alert.error_message}"

        return content

    def _build_html(self, alert: Alert) -> str:
        """Build HTML email content.
                Constructs an HTML representation of the alert with severity-based
        color coding and structured layout. Uses inline styles for email client
        compatibility.

        Examples:
                    >>> from phlo_alerting.manager import Alert, AlertSeverity
                    >>> alert = Alert(title="Test", message="Test", severity=AlertSeverity.CRITICAL)
                    >>> html = dest._build_html(alert)
                    >>> "<html>" in html
                    True
                    >>> "#cc0000" in html  # Critical severity color
                    True
        """
        severity_color = {
            AlertSeverity.INFO: "#36a64f",
            AlertSeverity.WARNING: "#ff9900",
            AlertSeverity.ERROR: "#ff3333",
            AlertSeverity.CRITICAL: "#cc0000",
        }.get(alert.severity, "#999999")

        html = f"""
        <html>
            <body style="font-family: Arial, sans-serif;">
                <div style="border-left: 4px solid {severity_color}; padding: 15px; background: #f9f9f9; margin: 10px 0;">
                    <h2 style="margin-top: 0; color: {severity_color};">{alert.title}</h2>

                    <table style="width: 100%; margin: 15px 0;">
                        <tr>
                            <td style="font-weight: bold; width: 120px;">Severity:</td>
                            <td style="color: {severity_color}; font-weight: bold;">{alert.severity.value.upper()}</td>
                        </tr>
                        <tr>
                            <td style="font-weight: bold;">Time:</td>
                            <td>{alert.timestamp.isoformat() if alert.timestamp else "N/A"}</td>
                        </tr>
        """

        if alert.asset_name:
            html += f"""
                        <tr>
                            <td style="font-weight: bold;">Asset:</td>
                            <td>{alert.asset_name}</td>
                        </tr>
            """

        if alert.run_id:
            html += f"""
                        <tr>
                            <td style="font-weight: bold;">Run ID:</td>
                            <td><code>{alert.run_id}</code></td>
                        </tr>
            """

        html += """
                    </table>

                    <div style="margin: 15px 0;">
                        <h3>Message</h3>
                        <p>{}</p>
                    </div>
        """.format(alert.message)

        if alert.error_message:
            html += f"""
                    <div style="background: #f0f0f0; padding: 10px; border-radius: 3px; margin: 15px 0;">
                        <h3>Error Details</h3>
                        <pre style="margin: 0; font-size: 12px;">{alert.error_message}</pre>
                    </div>
            """

        html += """
                </div>
            </body>
        </html>
        """

        return html
