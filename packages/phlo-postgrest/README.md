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
| **Schema Exposure**     | Exposes the published marts schema (`POSTGRES_MART_SCHEMA`, default `marts`) and `api` |
| **Anonymous Role**      | A `NOLOGIN` `anon` role with read-only `SELECT` on published marts, including tables published later |
| **OpenAPI Docs**        | Auto-generates OpenAPI spec            |

Anonymous callers cannot write, and cannot see Dagster, Nessie, Polaris or
phlo's own tables, because those schemas are never exposed and `anon` has no
grants on them.

## Hardening

- To serve only chosen models, tag them in dbt (`public`, `analyst`, `admin`)
  and run `phlo postgrest generate-views`. The views land in `api` with
  matching grants.
- To turn off anonymous reads, set `db-anon-role` in
  `.phlo/postgrest/conf/postgrest.conf` to a role with no grants. The
  start hook keeps any role other than the database owner, and ingress
  (oauth2-proxy) or JWT auth (`phlo postgrest setup-auth`) can then gate
  access.

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
