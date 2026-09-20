# Add observability

Use this guide when you need telemetry, log storage, metrics, dashboards, alerts, or the optional ClickStack alternative. The packages in this guide are preview integrations unless the package reference says otherwise.

## Before you start

Start with a working Phlo project and the default Dagster stack. Review the support tier in [Package reference](../reference/packages.md) before enabling preview services. The observability profile includes Alloy, Loki, Prometheus, Grafana, ClickStack, and the `phlo-observe-plugin` service.

## 1. Install the integrations

Add the packages you need to the project environment. The package entry points register service definitions, hooks, or observability extensions when installed.

```bash
uv add phlo-otel phlo-alloy phlo-loki phlo-prometheus phlo-grafana phlo-alerting
```

Use `phlo-clickstack` instead of the Loki and Prometheus storage path when you choose the ClickStack alternative. `phlo-observe-plugin` provides the optional Observatory ingestion service and is configured separately.

## 2. Generate the service configuration

Generate service files after installing the packages.

```bash
phlo services init --profile observability
```

Alloy uses `ALLOY_PORT` with default `12345`. Loki uses `LOKI_PORT` with default `3100`. Prometheus uses `PROMETHEUS_PORT` with default `9090`. Grafana uses `GRAFANA_PORT` with default `3003`.

ClickStack uses `CLICKSTACK_PORT` with default `18080`, `CLICKSTACK_HTTP_PORT` with default `18123`, and `CLICKSTACK_NATIVE_PORT` with default `19002`. The observer service uses `PHLO_OBSERVER_PORT` with default `10010`.

## 3. Start the selected services

Start only the services you selected in the generated configuration.

```bash
phlo services start
phlo services status
```

The service manifests define the observability profile and service dependencies. They do not make preview services part of the blessed-core default extra.

## 4. Enable telemetry

Set standard OpenTelemetry environment variables when you want traces, metrics, or logs exported through OTLP.

```bash
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317
export OTEL_SERVICE_NAME=phlo
export OTEL_TRACES_EXPORTER=otlp
export OTEL_METRICS_EXPORTER=otlp
```

The `phlo-otel` provider reads `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_SERVICE_NAME`, `OTEL_SERVICE_NAMESPACE`, `OTEL_SERVICE_VERSION`, `OTEL_SERVICE_INSTANCE_ID`, `OTEL_TRACES_EXPORTER`, `OTEL_METRICS_EXPORTER`, and `OTEL_LOGS_EXPORTER`.

## 5. Configure alerts

Configure one or more `PHLO_ALERT_*` settings for Slack, PagerDuty, or SMTP destinations. The `AlertingHookPlugin` and Dagster `failure_alert_sensor` use those settings to send alerts after failed runs.

```bash
phlo alerts status
phlo alerts list
phlo alerts test --severity warning
```

With no destinations configured, `phlo alerts status` reports zero configured destinations and recommends configuring an alert destination before running a test.

## Verify

Open Grafana at the port configured by `GRAFANA_PORT`, query Prometheus at its configured port, or inspect Loki through the configured log path. For ClickStack, use the ClickStack service port and the `phlo clickstack query` inspection command.

## Related

- [Monitor and debug](monitor-and-debug.md)
- [Package reference](../reference/packages.md)
- [Configuration](../reference/configuration.md)
