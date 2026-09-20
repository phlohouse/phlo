# Expose data

This guide publishes a table through Phlo's API and shows how the optional REST, GraphQL, and BI services fit around the default Trino path.

## Before you start

- You have a materialised table such as `raw.events` and know which audience may read it.
- The default stack is running, or you have installed the optional package for the surface you need.
- You can edit `phlo.yaml` and regenerate `.phlo/` with `phlo services init`.

## 1. Declare the published surface

Create a publish asset that records ownership, audience, and freshness metadata:

```python
from phlo.flow import publish


@publish(
    table="raw.events",
    audience=["analytics"],
    owner="data-platform",
    freshness_hours=24,
)
def published_events():
    return None
```

The declaration adds a governed publish surface. It does not copy the table or create a second storage engine.

## 2. Enable the Phlo API

`phlo-api` is the native API surface and is opt-in in the generated local stack. Enable it in `phlo.yaml`:

```yaml
infrastructure:
  services:
    phlo-api:
      enabled: true
```

```bash
phlo services init
phlo services start --service phlo-api
```

The service listens on host port `4000` by default. Its source-backed routes include `/api/config`, `/api/services`, `/api/observability/health`, and `/api/trino/preview/{table}`.

## 3. Query a table through phlo-api

Use the real Trino preview route for a read-only table preview:

```bash
curl "http://localhost:4000/api/trino/preview/raw.events?limit=10"
```

The response is JSON containing the selected table preview, and the API resolves the configured query-engine capability rather than requiring your client to open a Trino shell.

## 4. Add REST, GraphQL, or BI services

`phlo-postgrest`, `phlo-hasura`, and `phlo-superset` are optional service plugins. Enable only the surfaces required by consumers:

```yaml
infrastructure:
  services:
    postgrest:
      enabled: true
    hasura:
      enabled: true
    superset:
      enabled: true
```

```bash
phlo services init
phlo services start --service postgrest
phlo services start --service hasura
phlo services start --service superset
```

The verified default package mappings expose PostgREST on `3002`, Hasura on `8082`, and Superset on `10007`. Inspect `phlo services ports` after generation to confirm host overrides.

| Surface | Default or opt-in | Use |
| --- | --- | --- |
| Trino | Default | SQL access through `phlo trino --catalog iceberg`. |
| `phlo-api` | Opt-in | Phlo-native metadata, preview, lineage, and observability routes. |
| PostgREST | Opt-in | REST over published PostgreSQL schemas. |
| Hasura | Opt-in | GraphQL and subscriptions over PostgreSQL. |
| Superset | Opt-in (`10007`) | Browser dashboards and SQL exploration. |

## 5. Inspect generated routes and ports

```bash
phlo services ports
phlo services status
```

The ports command prints the actual host mappings from the generated Compose project, while status confirms the service health before a consumer receives a URL.

## Verify

```bash
curl -fsS http://localhost:4000/api/observability/health
```

A healthy API returns a JSON health response with HTTP status `200`. If the route is unavailable, check that `phlo-api` is enabled, regenerated, and running.

## Related

- [Secure the stack](secure-the-stack.md) for API authorisation and service boundaries.
- [Choose your stack](choose-your-stack.md) for optional service providers.
- [Auth and access](../reference/auth-and-access.md) for access decorators and policy metadata.
