# phlo-pgweb

pgweb database browser service plugin for Phlo.

## Description

Web-based PostgreSQL database browser for exploring metadata, lineage store, and operational data.

## Installation

```bash
uv add phlo-pgweb
# or
phlo plugin install pgweb
```

## Configuration

| Variable            | Default | Description       |
| ------------------- | ------- | ----------------- |
| `PGWEB_PORT`        | `8081`  | Web UI port       |
| `POSTGRES_USER`     | `phlo`  | Database user     |
| `POSTGRES_PASSWORD` | `phlo`  | Database password |
| `POSTGRES_DB`       | `phlo`  | Database name     |

## Auto-Configuration

This package is **fully auto-configured**:

| Feature                  | How It Works                                            |
| ------------------------ | ------------------------------------------------------- |
| **Database Connection**  | Auto-connects to Phlo's PostgreSQL using `DATABASE_URL` |
| **Service Dependencies** | Depends on `postgres` service                           |

## Usage

```bash
phlo services start --service pgweb
```

## Dependencies

- postgres

## Endpoints

- **Web UI**: `http://localhost:8081`
