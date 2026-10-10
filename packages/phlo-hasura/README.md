# phlo-hasura

Hasura GraphQL engine for Phlo.

## Description

GraphQL API engine with real-time subscriptions. Exposes PostgreSQL tables as GraphQL endpoints.

## Installation

```bash
pip install phlo-hasura
# or
phlo plugin install hasura
```

## Profile

Part of the `api` profile.

## Configuration

| Variable              | Default                    | Description             |
| --------------------- | -------------------------- | ----------------------- |
| `HASURA_PORT`         | `8082`                     | Hasura console/API port |
| `HASURA_IMAGE`        | `hasura/graphql-engine:v2.49.5@sha256:...` | Complete immutable Hasura image reference |
| `HASURA_ADMIN_SECRET` | auto-generated | Admin secret            |

## Auto-Configuration

This package is **fully auto-configured**:

| Feature            | How It Works                                           |
| ------------------ | ------------------------------------------------------ |
| **Table Tracking** | Auto-tracks tables in `api` schema via post_start hook |
| **Metrics Labels** | Exposes Hasura metrics at `/v1/metrics`                |
| **Anonymous Role** | Configured with `anonymous` role for public access     |

### Post-Start Hook

```yaml
hooks:
  post_start:
    - name: track-tables
      command: python -m phlo_hasura.hooks track-tables api
```

## Usage

```bash
# Start with API profile
phlo services start --profile api

# Or start individually
phlo services start --service hasura
```

## Endpoints

- **GraphQL**: `http://localhost:8082/v1/graphql`
- **Console**: `http://localhost:8082/console`
- **Metrics**: `http://localhost:8082/v1/metrics`

## Entry Points

- `phlo.plugins.services` - Provides `HasuraServicePlugin`
- `phlo.plugins.cli` - Provides Hasura CLI commands
