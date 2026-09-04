# ADR 0032: Eliminate Docker Images - Port Observatory Server to Python

## Status

**Accepted**

## Context

Observatory uses TanStack Start with Nitro and has 17+ server function modules that communicate with Trino, Nessie, Dagster, Loki, etc. These cannot be compiled to static assets, which means we cannot simply bundle Observatory as static files served by phlo-api.

### Current Server Modules (~130KB total)

| Module                 | Lines | Purpose                                |
| ---------------------- | ----- | -------------------------------------- |
| dagster.server.ts      | 739   | GraphQL queries to Dagster API         |
| trino.server.ts        | 644   | SQL execution via Trino HTTP API       |
| services.server.ts     | ~500  | Service health checks                  |
| contributing.server.ts | ~400  | Row-level lineage queries              |
| nessie.server.ts       | 450   | Git-like branch operations             |
| iceberg.server.ts      | 419   | Table metadata via Trino               |
| quality.server.ts      | ~350  | Data quality checks                    |
| lineage.server.ts      | ~280  | Column/asset lineage                   |
| loki.server.ts         | ~300  | Log queries                            |
| diff.server.ts         | ~250  | Branch diff computation                |
| graph.server.ts        | ~270  | Lineage graph building                 |
| Others                 | ~500  | Auth, cache, search, settings, plugins |

## Decision

**Port all server functions to phlo-api** (Python/FastAPI) and convert Observatory to a pure client-side SPA.

### Target Architecture

```
┌─────────────────────────────────────────────┐
│                 phlo-api                    │
│  ├── /api/trino/*      → Trino queries     │
│  ├── /api/dagster/*    → Dagster GraphQL   │
│  ├── /api/nessie/*     → Branch ops        │
│  ├── /api/iceberg/*    → Table metadata    │
│  ├── /api/quality/*    → Data quality      │
│  ├── /api/loki/*       → Logs              │
│  └── /*                → Observatory SPA   │
└─────────────────────────────────────────────┘
       Native uvicorn subprocess
```

## Implementation

### Phase 1: Core Data Access

New Python routers in `packages/phlo-api/src/phlo_api/observatory_api/`:

- `trino.py` - Query execution, preview, column profiling
- `iceberg.py` - Table listing, schema, metadata
- `dagster.py` - Health metrics, assets, runs
- `nessie.py` - Branches, commits, merge operations

### Phase 2: Secondary Features

- `quality.py` - Data quality check results
- `loki.py` - Log queries
- `lineage.py` - Column/asset lineage
- `search.py` - Global search

### Phase 3: Observatory Client Refactor

Replace `createServerFn()` calls with `fetch('/api/...')` and remove Nitro dependencies.

### Phase 4: Distribution

Publish `phlo-api` and `phlo-observatory` as packages; Observatory consumes phlo-api endpoints.

## Consequences

### Positive

- No Docker images needed for core services
- No Node.js runtime dependency for users
- Install `phlo` + `phlo-api` + `phlo-observatory` for the full UI stack
- Python-native backend (easier to extend)

### Negative

- ~2-3 days implementation effort
- Temporary feature parity gap during migration
- Some TypeScript type safety lost in API calls

## Related

- Prior: [ADR 0031 - Observatory as Core](./0031-observatory-as-core-and-dx-improvements.md)
- Supersedes Docker image publishing workflow
