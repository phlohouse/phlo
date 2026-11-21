# phlo-alerting

Alert routing and notification plugin for Phlo.

## Description

Routes alerts from quality check failures, telemetry events, and pipeline errors to configured destinations (Slack, PagerDuty, Email).

## Installation

```bash
uv add phlo-alerting
# or
phlo plugin install alerting
```

## Configuration

| Variable                         | Default | Description                             |
| -------------------------------- | ------- | --------------------------------------- |
| `PHLO_ALERT_SLACK_WEBHOOK`       | -       | Slack incoming webhook URL              |
| `PHLO_ALERT_SLACK_CHANNEL`       | -       | Default Slack channel                   |
| `PHLO_ALERT_PAGERDUTY_KEY`       | -       | PagerDuty Events API v2 key             |
| `PHLO_ALERT_EMAIL_SMTP_HOST`     | -       | SMTP server hostname                    |
| `PHLO_ALERT_EMAIL_SMTP_PORT`     | `587`   | SMTP server port                        |
| `PHLO_ALERT_EMAIL_SMTP_USER`     | -       | SMTP username                           |
| `PHLO_ALERT_EMAIL_SMTP_PASSWORD` | -       | SMTP password                           |
| `PHLO_ALERT_EMAIL_RECIPIENTS`    | `[]`    | Comma-separated list: `a@x.com,b@y.com` |

## Auto-Configuration

This package is **fully auto-configured**:

| Feature               | How It Works                                                    |
| --------------------- | --------------------------------------------------------------- |
| **Hook Registration** | Automatically registers as a hook plugin via entry points       |
| **Event Handling**    | Listens for `quality.result` and `telemetry.*` events           |
| **Severity Mapping**  | Maps event severities to alert levels (critical, warning, info) |

### Supported Events

- `quality.result` - Receives quality check results and sends alerts on failures
- `telemetry.*` - Receives telemetry events and forwards to configured destinations

## Usage

### CLI Commands

```bash
# Send a test alert
phlo alerts test --destination slack

# List configured destinations
phlo alerts list-destinations
```

### Programmatic

```python
from phlo_alerting.manager import AlertManager

manager = AlertManager()
manager.send_alert(
    title="Quality Check Failed",
    message="Null check failed on table users",
    severity="critical"
)
```

## Entry Points

- `phlo.plugins.cli` - Provides `alerts` CLI command
- `phlo.plugins.hooks` - Registers `AlertingHookPlugin` for event handling
