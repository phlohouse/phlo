# phlo-alloy

Grafana Alloy log collector for Phlo.

## Description

Collects logs from all Docker containers and ships them to Loki for aggregation and querying.

## Installation

```bash
pip install phlo-alloy
# or
phlo plugin install alloy
```

## Profile

Part of the `observability` profile.

## Configuration

| Variable     | Default | Description     |
| ------------ | ------- | --------------- |
| `ALLOY_PORT` | `12345` | Alloy HTTP port |

## Auto-Configuration

This package is **fully auto-configured**:

| Feature                 | How It Works                                           |
| ----------------------- | ------------------------------------------------------ |
| **Container Discovery** | Auto-discovers all Docker containers via Docker socket |
| **Log Collection**      | Collects stdout/stderr from all containers             |
| **Loki Shipping**       | Ships logs to Loki for storage and querying            |
| **Metrics Labels**      | Exposes Alloy metrics for Prometheus                   |

### Docker API Access

Alloy never mounts the Docker socket. It discovers and reads container logs
through `alloy-docker-proxy`, a read-only Docker API proxy
(`tecnativa/docker-socket-proxy`) that allows only `GET` requests for
containers, networks, events, ping and version. The proxy is reachable only on
the internal `alloy-docker-api` network shared with Alloy.

## Usage

```bash
# Start with observability profile
phlo services start --profile observability

# Or start individually
phlo services start --service alloy
```

## Endpoints

- **HTTP**: `http://localhost:12345`
- **Ready**: `http://localhost:12345/-/ready`

## Entry Points

- `phlo.plugins.services` - Provides `AlloyServicePlugin`
