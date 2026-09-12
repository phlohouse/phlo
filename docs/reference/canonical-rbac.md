# Canonical RBAC

Phlo includes a canonical RBAC control plane for describing roles, subjects, and
policies once and then compiling them into backend-native enforcement artifacts.

Use this page when you need the source-of-truth model, file shapes, command
behavior, or backend support boundaries.

## Overview

The canonical RBAC workflow centers on two files under `.phlo/authorization/`:

- `roles.yaml`: role definitions, inheritance, and subject-to-role assignments
- `policies.yaml`: canonical allow rules for actions on resource types

The CLI drives the workflow:

```bash
phlo authz validate
phlo authz plan
phlo authz sync
phlo authz verify
```

Typical flow:

1. edit `.phlo/authorization/roles.yaml`
2. edit `.phlo/authorization/policies.yaml`
3. run `phlo authz validate`
4. run `phlo authz plan`
5. run `phlo authz sync`
6. run `phlo authz verify`

## Configuration Layout

The loader reads RBAC configuration from `.phlo/authorization/` by default.
Pass `--path` to point commands at a different `.phlo` directory root.

### `roles.yaml`

`roles.yaml` defines role names, optional inheritance, and subject assignment.

```yaml
version: 1
roles:
  viewer:
    inherits: []
    description: Read-only access
  analyst:
    inherits:
      - viewer
    description: Interactive query access
  admin:
    inherits:
      - analyst
    description: Administrative access
subjects:
  services:
    dagster-agent:
      - analyst
  users:
    alice@example.com:
      - admin
    bob@example.com:
      - viewer
```

Fields:

- `version`: schema version for the roles document
- `roles`: mapping of role name to role definition
- `roles.<name>.inherits`: parent roles to include in the hierarchy
- `roles.<name>.description`: optional human-readable description
- `subjects.services`: service principal to roles mapping
- `subjects.users`: user principal to roles mapping

Role hierarchy rules:

- inherited roles must exist
- cycles are rejected during validation
- hierarchy expansion is available in the canonical model

Current implementation note:

- backend compilers compile policies for the roles listed in each policy rule
  directly; they do not currently fan out grants to inherited roles for you

### `policies.yaml`

`policies.yaml` defines canonical permission rules.

```yaml
version: 1
policies:
  - policy_id: allow_analyst_dataset_read
    effect: allow
    principal:
      roles:
        - analyst
    action: dataset.read
    resource:
      type: dataset
      id_pattern: analytics.*

  - policy_id: allow_platform_storage_write
    effect: allow
    principal:
      roles:
        - admin
    action: object.write
    resource:
      type: object
      id_pattern: platform-artifacts
```

Fields:

- `policy_id`: stable identifier for the rule
- `effect`: currently `allow` only
- `principal.roles`: roles the rule applies to
- `principal.attributes`: optional attribute map reserved in the model
- `action`: canonical action name
- `resource.type`: canonical resource type
- `resource.id_pattern`: backend-specific identifier or wildcard pattern
- `resource.attributes`: optional attribute map reserved in the model

## Canonical Actions And Resource Types

Canonical actions:

- `dataset.read`
- `dataset.query`
- `asset.read`
- `asset.execute`
- `service.read`
- `service.manage`
- `admin.read`
- `admin.manage`
- `object.read`
- `object.write`
- `catalog.read`
- `catalog.manage`

Canonical resource types:

- `dataset`
- `asset`
- `service`
- `admin`
- `object`
- `catalog`

## Validation Rules

`phlo authz validate` checks:

- `roles.yaml` and `policies.yaml` can be parsed
- referenced roles exist
- role inheritance has no cycles
- the combined canonical RBAC model is internally consistent

Important limitation:

- canonical RBAC rejects `deny` policies today

If a policy uses `effect: deny`, validation fails and planning or compilation
will also reject it. Do not model deny semantics in canonical config until the
backend compilation path supports them.

## CLI Commands

### `phlo authz validate`

Validate canonical RBAC config under the target `.phlo` directory.

```bash
phlo authz validate [OPTIONS]
```

Options:

- `--path PATH`: path to the `.phlo` directory root

Use it to catch malformed YAML, missing roles, hierarchy cycles, and unsupported
policy effects before you plan or apply changes.

### `phlo authz plan`

Generate backend-specific changes without mutating any backend.

```bash
phlo authz plan [OPTIONS]
```

Options:

- `--path PATH`: path to the `.phlo` directory root
- `--backend TEXT`: restrict planning to one or more backends
- `--environment TEXT`: context label passed to compilers, default `development`

Use it to review the version hash and planned artifact creation or deletion
before syncing.

### `phlo authz sync`

Compile canonical RBAC into backend-native artifacts and apply them.

```bash
phlo authz sync [OPTIONS]
```

Options:

- `--path PATH`: path to the `.phlo` directory root
- `--backend TEXT`: restrict sync to one or more backends
- `--environment TEXT`: context label passed to compilers, default `development`
- `--dry-run`: plan without applying changes

Use `--dry-run` when you want sync-style output but no mutation. A successful
sync reports applied and failed artifact counts per backend.

### `phlo authz verify`

Compare desired compiled artifacts with backend state and report drift.

```bash
phlo authz verify [OPTIONS]
```

Options:

- `--path PATH`: path to the `.phlo` directory root
- `--backend TEXT`: restrict verification to one or more backends
- `--environment TEXT`: context label passed to compilers, default `development`

Verification reports:

- `missing`: desired artifacts not found in the backend
- `extra`: managed backend artifacts not present in desired state
- `mismatched`: reserved for artifact content differences when supported

## Backend Compiler Coverage Matrix

The following matrix shows which canonical actions each backend compiler supports.
A ✓ means the compiler can generate enforcement artifacts for that action.
An em dash (—) means the action is not applicable to that backend.

| Action | Trino | PostgreSQL | Hasura | MinIO | Nessie |
|--------|-------|-----------|--------|-------|--------|
| `dataset.read` | ✓ | ✓ | ✓ | ✓ | ✓ |
| `dataset.query` | ✓ | ✓ | ✓ | ✓ | ✓ |
| `asset.read` | ✓ | ✓ | ✓ | — | — |
| `asset.execute` | ✓ | ✓ | ✓ | — | — |
| `service.read` | ✓ | ✓ | ✓ | — | — |
| `service.manage` | ✓ | ✓ | ✓ | — | — |
| `admin.read` | ✓ | ✓ | ✓ | — | — |
| `admin.manage` | ✓ | ✓ | ✓ | — | — |
| `dataset.write` | — | ✓ | — | ✓ | ✓ |
| `dataset.publish` | — | — | — | — | ✓ |
| `asset.approve` | — | — | — | — | — |
| `settings.read` | — | — | — | — | — |
| `settings.manage` | — | — | — | — | — |
| `object.read` | — | — | — | ✓ | — |
| `object.write` | — | — | — | ✓ | — |
| `catalog.read` | — | — | — | — | ✓ |
| `catalog.manage` | — | — | — | — | ✓ |
| `platform_metadata.read` | — | — | — | — | — |
| `observability.read` | — | — | — | — | — |
| `maintenance.read` | — | — | — | — | — |
| `run.read` | — | — | — | — | — |
| `run.execute` | — | — | — | — | — |
| `run.manage` | — | — | — | — | — |
| `audit.read` | — | — | — | — | — |

Compiler support means the code path exists to generate enforcement artifacts for
that action. It does not mean the backend is running or that the generated
artifacts have been applied.

Several canonical control-plane actions are enforced directly by regulated
surface adapters today and therefore show no backend compiler coverage yet.
`phlo authz verify` only requires compiler support for actions that appear in
the configured RBAC policies.

## Backend Support Matrix

| Backend | Supported canonical actions | Deny support | Verify coverage | Notes |
|---------|---------------------------|--------------|-----------------|-------|
| `trino` | `dataset.read`, `dataset.query`, `asset.read`, `asset.execute`, `service.read`, `service.manage`, `admin.read`, `admin.manage` | No | Compares desired vs current managed grants; reports missing and extra artifacts | Managed state is derived from governance backend `list_policies()` output and filtered to managed role names. |
| `postgresql` | `dataset.read`, `dataset.query`, `dataset.write` | No | Compares desired vs current managed grants with coverage semantics; reports missing and extra artifacts | A `schema.*` policy is satisfied by a full-coverage grant or a predefined `pg_*_all_data` membership. `object.*` resources belong to MinIO and compile as S3 policies, not Postgres grants. |
| `hasura` | `dataset.read`, `dataset.query`, `asset.read`, `asset.execute`, `service.read`, `service.manage`, `admin.read`, `admin.manage` | No | Desired-state only; current-state introspection is not implemented | Canonical actions map to Hasura permission artifacts, but verify cannot confirm live metadata drift yet. |
| `minio` | `object.read`, `object.write`, `dataset.read`, `dataset.query`, `dataset.write` | No | Compares desired vs current managed policy documents including group attachment; reports missing, extra, and mismatched artifacts | A same-named document with stale content or no group attachment is drift, not state. `dataset.*` policies map onto warehouse object prefixes. |
| `nessie` | `catalog.read`, `catalog.manage`, `dataset.read`, `dataset.query`, `dataset.write`, `dataset.publish` | No | Compares desired vs rendered managed authz rules; readiness probe also requires a live Nessie that has loaded the rendered file | Rules are static Quarkus config rendered into `.phlo/nessie/authz.properties`; a write stages config and convergence stays pending until Nessie restarts. |

## Backend-Specific Follow-Up

After `phlo authz sync`, backend-specific setup still matters:

- Trino: confirm the governance backend is registered and exposes current grants
  if you want meaningful drift verification
- PostgreSQL: check live grants and any row-level security or schema ownership
  rules outside the canonical compiler output
- Hasura: confirm permissions are reflected in Hasura metadata and JWT role
  claims align with the roles you compiled
- MinIO: sync verifies group attachment, so confirm canonical roles exist as
  MinIO groups (the OIDC or IAM subject mapping still maps users to groups)
- Nessie: rendered rules only apply at container start — restart Nessie after
  `phlo authz sync` for staged rules to take effect; readiness reports
  `nessie_rules_pending_restart` until then

## Related Docs

- [Auth And Access Model](auth-and-access.md) for the larger authn/authz picture
- [CLI Reference](cli-reference.md) for command-level reference
- [Security](../setup/security.md) for backend-specific security setup after the
  canonical RBAC model is defined
