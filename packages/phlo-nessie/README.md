# phlo-nessie

Nessie Git-like catalog for Phlo.

## Description

Nessie provides Git-like version control for Iceberg tables. Enables branching, merging, and time travel for the data lakehouse.

## Installation

```bash
uv add phlo-nessie
# or
phlo plugin install nessie

# Nessie plus the Trino catalog adapter
uv add 'phlo-nessie[trino]'
```

## Configuration

| Variable               | Default   | Description                |
| ---------------------- | --------- | -------------------------- |
| `NESSIE_PORT`          | `10003`   | Nessie API host port       |
| `NESSIE_OIDC_ENABLED`  | `false`   | Enable OIDC authentication |
| `NESSIE_AUTHZ_ENABLED` | `false`   | Enable authorization       |

## Auto-Configuration

Works out-of-the-box with sensible defaults:

| Feature              | How It Works                                               |
| -------------------- | ---------------------------------------------------------- |
| **Branch Init**      | Auto-creates `main` and `dev` branches via post_start hook |
| **Metrics Labels**   | Exposes Quarkus metrics at `/q/metrics`                    |
| **Postgres Storage** | Uses PostgreSQL for version store (default backend)        |
| **Catalog Plugins**  | `phlo-nessie[trino]` registers `iceberg` and `iceberg_dev` Trino catalogs |

### Post-Start Hook

```yaml
hooks:
  post_start:
    - name: init-branches
      command: python -m phlo_nessie.hooks init-branches
```

## Usage

```bash
# Start Nessie
phlo services start --service nessie

# List branches
phlo nessie branches

# Create a new branch
phlo nessie branch create feature/my-feature
```

## Endpoints

- **API**: `http://localhost:10003/api/v1`
- **Iceberg REST**: `http://localhost:10003/iceberg`

## Entry Points

- `phlo.plugins.services` - Provides `NessieServicePlugin`
- `phlo.plugins.cli` - Provides Nessie CLI commands
- `phlo.plugins.catalogs` - Provides optional Nessie-owned engine adapters such as Trino
