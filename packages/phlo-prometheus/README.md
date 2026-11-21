# phlo-prometheus

Prometheus metrics collection service plugin for Phlo.

## Description

Metrics collection and monitoring for the Phlo data lakehouse. Uses Docker-based service discovery to automatically scrape metrics from all Phlo services.

## Installation

```bash
uv add phlo-prometheus
# or
phlo plugin install prometheus
```

## Profile

Part of the `observability` profile.

## Configuration

| Variable             | Default  | Description            |
| -------------------- | -------- | ---------------------- |
| `PROMETHEUS_PORT`    | `9090`   | Prometheus web UI port |
| `PROMETHEUS_VERSION` | `v3.13.1` | Prometheus version     |

## Auto-Configuration

This package is **fully auto-configured**:

| Feature               | How It Works                                                                        |
| --------------------- | ----------------------------------------------------------------------------------- |
| **Service Discovery** | Auto-discovers all Docker containers with `phlo.metrics.enabled=true` label         |
| **Scrape Targets**    | Dynamically reads `phlo.metrics.port` and `phlo.metrics.path` from container labels |
| **Container Labels**  | Extracts `service` and `project` labels from Docker Compose metadata                |

### Adding a New Scrape Target

Any service with these Docker labels will be auto-scraped:

```yaml
compose:
  labels:
    phlo.metrics.enabled: "true"
    phlo.metrics.port: "service-name:8080"
    phlo.metrics.path: "/metrics" # optional, defaults to /metrics
```

## Usage

```bash
# Start with observability profile
phlo services start --profile observability

# Or start individually
phlo services start --service prometheus
```

## Endpoints

- **Web UI**: `http://localhost:9090`
- **Metrics**: `http://localhost:9090/metrics`
- **Targets**: `http://localhost:9090/targets`

## Verification

```bash
# Check discovered targets
curl http://localhost:9090/api/v1/targets | jq '.data.activeTargets'
```
