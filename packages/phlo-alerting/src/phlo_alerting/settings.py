"""Alerting settings configuration.

This module provides configuration management for the phlo-alerting package
using Pydantic models. It supports configuration via environment variables
with automatic type validation and default values.

All configuration values are read from environment variables with the
"PHLO_ALERT_" prefix. The get_settings() function provides cached
per-project-root settings for efficient repeated access.

Examples:
    Retrieving settings:
        >>> from phlo_alerting.settings import get_settings
        >>> settings = get_settings()
        >>> settings.phlo_alert_slack_webhook
        'https://hooks.slack.com/services/...'

    Checking configuration status:
        >>> settings.phlo_alert_slack_webhook is not None
        True

Environment Variables:
    PHLO_ALERT_SLACK_WEBHOOK: Slack incoming webhook URL.
    PHLO_ALERT_SLACK_CHANNEL: Default Slack channel for alerts (optional).
    PHLO_ALERT_PAGERDUTY_KEY: PagerDuty Events API v2 integration key.
    PHLO_ALERT_EMAIL_SMTP_HOST: SMTP server hostname.
    PHLO_ALERT_EMAIL_SMTP_PORT: SMTP server port (default: 587).
    PHLO_ALERT_EMAIL_SMTP_USER: SMTP username.
    PHLO_ALERT_EMAIL_SMTP_PASSWORD: SMTP password.
    PHLO_ALERT_EMAIL_RECIPIENTS: Comma-separated list of email recipients.

Builds on phlo.config.base and phlo.config.cache for project-root-cached settings access.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field

from phlo.config.base import BaseConfig
from phlo.config.cache import project_root_cached


class AlertingSettings(BaseConfig):
    """Alert integration configuration for Slack, PagerDuty, and Email.

    Pydantic-based configuration class that automatically loads settings
    from environment variables with "PHLO_ALERT_" prefix. Provides
    type-safe access to alerting configuration with validation.

    Field values load from ``PHLO_ALERT_*`` environment variables; see the
    module docstring for the full variable list.
    Examples:
        >>> settings = AlertingSettings()
        >>> settings.phlo_alert_email_smtp_port
        587
        >>> isinstance(settings.phlo_alert_email_recipients, list)
        True

    """

    phlo_alert_slack_webhook: str | None = Field(
        default=None, description="Slack incoming webhook URL"
    )
    phlo_alert_slack_channel: str | None = Field(
        default=None, description="Default Slack channel for alerts"
    )
    phlo_alert_pagerduty_key: str | None = Field(
        default=None, description="PagerDuty Events API v2 integration key"
    )
    phlo_alert_email_smtp_host: str | None = Field(default=None, description="SMTP server hostname")
    phlo_alert_email_smtp_port: int = Field(default=587, description="SMTP server port")
    phlo_alert_email_smtp_user: str | None = Field(default=None, description="SMTP username")
    phlo_alert_email_smtp_password: str | None = Field(default=None, description="SMTP password")
    phlo_alert_email_recipients: list[str] = Field(
        default_factory=list, description="Email recipients for alerts"
    )


@project_root_cached
def get_settings(project_root: Path) -> AlertingSettings:
    """Return cached alerting settings for the selected project root.

    Settings are cached per resolved project root, with up to 16 entries,
    and reused across the application lifecycle.

    Settings are parsed from ``PHLO_ALERT_*`` environment variables.
    Examples:
        >>> settings1 = get_settings()
        >>> settings2 = get_settings()
        >>> settings1 is settings2  # For the same project root
        True

    """
    return AlertingSettings()
