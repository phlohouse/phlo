"""CLI commands for alert management.

This module provides the Click command implementations for managing
alerts through the Phlo CLI. It includes commands for testing alert
configuration, listing destinations, and checking system status.

Commands:
    test: Send a test alert to verify configuration.
    list: Display configured alert destinations.
    status: Show overall alert system status and statistics.

Examples:
    Testing alert configuration:
        $ phlo alerts test
        $ phlo alerts test --severity critical
        $ phlo alerts test --destination slack

    Listing destinations:
        $ phlo alerts list

    Checking status:
        $ phlo alerts status

"""

from __future__ import annotations

from typing import Optional

import click
from rich.console import Console
from rich.table import Table

from phlo_alerting import AlertSeverity
from phlo_alerting.manager import get_alert_manager

console = Console()


@click.group(name="alerts")
def alerts_group():
    """Alert management and configuration.

    Command group for managing Phlo alert destinations and testing
    configuration. Supports Slack, PagerDuty, and Email destinations.

    Examples:
        $ phlo alerts --help
        $ phlo alerts test
        $ phlo alerts list

    """
    pass


@alerts_group.command(name="test")
@click.option(
    "--severity",
    type=click.Choice(["info", "warning", "error", "critical"]),
    default="warning",
    help="Alert severity level for test",
)
@click.option(
    "--destination",
    type=str,
    default=None,
    help="Specific destination to test (default: all)",
)
def test_alerts(severity: str, destination: Optional[str]) -> None:
    """Send a test alert to configured destinations.

        Sends a test alert through the alerting system to verify that
    destinations are properly configured and receiving notifications.
    Useful for validating alert setup after configuration changes.

    Examples:
            $ phlo alerts test
            ✓ Test alert sent successfully!

            $ phlo alerts test --severity critical
            ✓ Test alert sent successfully!

            $ phlo alerts test --destination slack
            ✓ Test alert sent successfully!

            $ phlo alerts test (no destinations configured)
            ✗ No alert destinations configured...

    """
    from phlo_alerting import Alert

    manager = get_alert_manager()

    if not manager.destinations:
        console.print(
            "[red]✗[/red] No alert destinations configured. "
            "Set PHLO_ALERT_SLACK_WEBHOOK, PHLO_ALERT_PAGERDUTY_KEY, or PHLO_ALERT_EMAIL_* environment variables."
        )
        return

    # Create test alert
    alert = Alert(
        title="Phlo Test Alert",
        message="This is a test alert from the Phlo CLI. If you see this, alerts are working!",
        severity=AlertSeverity(severity),
        asset_name="phlo_test",
        run_id="test_run_123",
        error_message=None,
    )

    # Send to specific or all destinations
    destinations = [destination] if destination else None

    if manager.send(alert, destinations=destinations):
        console.print(
            "[green]✓[/green] Test alert sent successfully! "
            "Check your configured alert destinations."
        )
    else:
        console.print("[red]✗[/red] Failed to send test alert.")


@alerts_group.command(name="list")
def list_destinations() -> None:
    """List configured alert destinations.

        Displays all currently configured alert destinations with their
    types and status. Shows configuration guidance if no destinations
        are configured.

    Examples:
            With destinations configured:
                $ phlo alerts list
                ┌───────────┬────────────────────┬────────┐
                │ Name      │ Type               │ Status │
                ├───────────┼────────────────────┼────────┤
                │ slack     │ SlackAlertDestination  │ ✓ Ready │
                └───────────┴────────────────────┴────────┘

            Without destinations:
                $ phlo alerts list
                ⚠ No alert destinations configured.
                (shows environment variable setup instructions)

    """
    manager = get_alert_manager()

    if not manager.destinations:
        console.print(
            "[yellow]⚠[/yellow]  No alert destinations configured.\n"
            "To enable alerts, set environment variables:"
        )
        console.print(
            """
  PHLO_ALERT_SLACK_WEBHOOK=https://hooks.slack.com/services/...
  PHLO_ALERT_SLACK_CHANNEL=#alerts        (optional)
  PHLO_ALERT_PAGERDUTY_KEY=...
  PHLO_ALERT_EMAIL_SMTP_HOST=smtp.example.com
  PHLO_ALERT_EMAIL_SMTP_PORT=587          (optional, default: 587)
  PHLO_ALERT_EMAIL_SMTP_USER=user@example.com
  PHLO_ALERT_EMAIL_SMTP_PASSWORD=password
  PHLO_ALERT_EMAIL_RECIPIENTS=team@example.com,admin@example.com
        """
        )
        return

    table = Table(title="Configured Alert Destinations")
    table.add_column("Name", style="cyan")
    table.add_column("Type", style="magenta")
    table.add_column("Status", style="green")

    for name, destination in manager.destinations.items():
        dest_type = destination.__class__.__name__
        status = "✓ Ready"

        table.add_row(name, dest_type, status)

    console.print(table)


@alerts_group.command(name="status")
def check_status() -> None:
    """Check alert system status.

        Displays overall alert system health including configured destinations,
    recent alert statistics, and next steps for configuration.

    Examples:
            $ phlo alerts status
            Alert System Status

            Configured Destinations: 2
              • slack
              • email

            Recent Alerts Sent: 5
            Deduplication Window: 60 minutes

            Next Steps
            1. Configure at least one alert destination...

    """
    manager = get_alert_manager()

    console.print("[bold]Alert System Status[/bold]\n")

    # Check destinations
    console.print(f"Configured Destinations: {len(manager.destinations)}")
    for name in manager.destinations:
        console.print(f"  • {name}")

    if not manager.destinations:
        console.print("  [yellow]None configured[/yellow]")

    # Show statistics
    console.print(f"\nRecent Alerts Sent: {len(manager._sent_alerts)}")
    console.print(f"Deduplication Window: {manager._dedup_window_minutes} minutes")

    # Show configuration guidance
    if len(manager.destinations) == 0:
        console.print("\n[bold]Next Steps[/bold]")
        console.print("1. Configure at least one alert destination via environment variables")
        console.print("2. Run [cyan]phlo alerts test[/cyan] to verify configuration")
        console.print("3. Alerts will automatically trigger on run failures")


if __name__ == "__main__":
    alerts_group()
