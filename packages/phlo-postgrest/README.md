# phlo-postgrest

PostgREST API service for Phlo.

## Description

Automatically generates RESTful API from PostgreSQL schemas. Exposes published tables via REST endpoints.

## Installation

```bash
uv add phlo-postgrest
# or
phlo plugin install postgrest
```

## Profile

Part of the `api` profile.

## Configuration

| Variable            | Default   | Description        |
| ------------------- | --------- | ------------------ |
| `POSTGREST_PORT`    | `3002`    | PostgREST API port |
| `POSTGREST_VERSION` | `v14.15` | PostgREST version  |
| `POSTGRES_USER`     | `phlo`    | Database user      |
| `POSTGRES_PASSWORD` | `phlo`    | Database password  |
| `POSTGRES_DB`       | `phlo`    | Database name      |
| `DBT_API_SOURCE_SCHEMA` | unset  | dbt model schema to expose; inferred only when the manifest contains exactly one model schema |

## Auto-Configuration

This package is **fully auto-configured**:

| Feature                 | How It Works                           |
| ----------------------- | -------------------------------------- |
| **Database Connection** | Auto-connects to Phlo's PostgreSQL     |
| **Schema Exposure**     | Exposes `api` and `public` schemas     |
| **Anonymous Role**      | Uses `POSTGRES_USER` as anonymous role |
| **OpenAPI Docs**        | Auto-generates OpenAPI spec            |

## Usage

```bash
# Start with API profile
phlo services start --profile api

# Or start individually
phlo services start --service postgrest
```

## Endpoints

- **API Base**: `http://localhost:3002`
- **OpenAPI Spec**: `http://localhost:3002/`

## Entry Points

- `phlo.plugins.services` - Provides `PostgrestServicePlugin`
- `phlo.plugins.cli` - Provides CLI commands for PostgREST
