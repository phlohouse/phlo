# phlo-traefik

Traefik reverse proxy service plugin for Phlo.

## Overview

Provides local reverse proxy for accessing Phlo services by hostname:
- `http://dagster.phlo.localhost`
- `http://minio.phlo.localhost`
- `http://trino.phlo.localhost`

## Usage

```bash
phlo services start --service traefik
open http://dagster.phlo.localhost
```

## Configuration

| Environment Variable | Default | Description |
|---------------------|---------|-------------|
| `TRAEFIK_HTTP_PORT` | `80` | Host port for Traefik HTTP entrypoint |
| `TRAEFIK_DOMAIN` | `phlo.localhost` | Base domain for service hostnames |
| `TRAEFIK_TRUSTED_IPS` | empty | Comma-separated IPs or CIDRs whose `X-Forwarded-*` headers Traefik keeps |

## Routed Services

The following services are automatically routed when Traefik is enabled:

| Hostname | Service | Port |
|----------|---------|------|
| `dagster.phlo.localhost` | Dagster | 3000 |
| `minio.phlo.localhost` | MinIO Console | 9001 |
| `minio-api.phlo.localhost` | MinIO API | 9000 |
| `trino.phlo.localhost` | Trino | 8080 |
| `nessie.phlo.localhost` | Nessie | 19120 |
| `clickhouse.phlo.localhost` | ClickHouse | 8123 |
| `api.phlo.localhost` | Phlo API | 4000 |
| `traefik.phlo.localhost` | Traefik Dashboard | (internal) |

## Troubleshooting

### Port 80 already in use

If port 80 is already in use on your machine, set a custom port:

```bash
# In .phlo/.env.local
TRAEFIK_HTTP_PORT=8088
```

Then access services with the port: `http://dagster.phlo.localhost:8088`
