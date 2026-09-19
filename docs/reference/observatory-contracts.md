# Observatory Contracts

Provider-neutral Observatory contracts define how packages expose platform
capabilities to the Observatory UI without coupling the UI to provider-specific
APIs, credentials, URLs, or implementation details.

Audience: package authors and platform developers who contribute capabilities,
read models, UI surfaces, or guarded actions.

## Capability Inventory

`GET /api/observatory/capability-inventory`

Returns the provider-neutral inventory of capabilities available to Observatory
for the active project and runtime. The inventory is the UI's source of truth
for deciding which surfaces, read models, actions, and native escape hatches are
available.

Consumers should treat the endpoint as descriptive, not imperative:

- use it to discover contributed capabilities
- use it to decide which Observatory surfaces to show
- use it to bind UI contributions to read models and guarded actions
- do not infer provider internals from provider names, package names, or native
  service URLs

## Endpoint Families

All Observatory endpoints are rooted at `/api/observatory`. They are grouped by user workflow rather than by provider package.

## Canonical Surfaces

These are the canonical browser routes and API roots. Removed Observatory routes
and API families are hard 404s. They are not redirected, retained, or served as
aliases.

| Surface | Browser route | API family | Status |
| --- | --- | --- | --- |
| Overview | `/` | `/api/observatory/overview`, `/api/observatory/capability-inventory`, `/api/observatory/mission/context`, `/api/observatory/mission/overview/*` | canonical |
| Datasets | `/datasets`, `/datasets/{dataset_id}` | `/api/observatory/datasets`, `/api/observatory/datasets/{dataset_id}`, `/api/observatory/mission/datasets/{dataset_id}` | canonical |
| Runs | `/runs`, `/runs/{run_id}` | `/api/observatory/mission/runs`, `/api/observatory/mission/runs/{run_id}` + `/events`, `/logs`, `/quality`, `/stages`, `/artifacts`, `/configuration`, `/consumers`, `/traces` | canonical |
| Releases | `/releases` | `/api/observatory/mission/releases/summary`, `/api/observatory/mission/releases/candidates`, `/api/observatory/mission/releases/candidates/{id}`, `…/preview`, `…/promotion`, `/api/observatory/mission/releases/completed` | canonical |
| Platform | `/platform` | `/api/observatory/mission/platform/summary`, `…/services`, `…/services/{service_id}`, `…/maintenance`, `…/backup`, `/api/observatory/services`, `/api/observatory/services/{service_id}/probe` | canonical |
| Operations | `/operations` | `/api/observatory/operations`, `/api/observatory/operations/{operation_id}`, `…/agent-context`, `/api/observatory/actions` | canonical |
| Settings | `/settings` | `/api/observatory/mission/settings/summary`, `…/providers`, `…/defaults`, `…/members`, `…/notifications` | canonical |
| Governance | `/governance` | `/api/observatory/mission/governance/summary`, `…/audit`, `…/access-drift`, `…/ownership-gaps`, `…/publication-plan/{dataset_id}`, `…/publication-reviews` | canonical |
| Docs / Reference | `/docs`, `/reference` | `/api/observatory/search` | canonical |
| Tables | none (dataset-scoped) | `/api/observatory/tables`, `/api/observatory/table-preview/{table_id}`, `/api/observatory/query` | canonical |
| Lineage | dataset-scoped | `/api/observatory/assets`, `/api/observatory/assets/{asset_id}`, `/api/observatory/asset-graph` | canonical |
| Quality | run/dataset-scoped | `/api/observatory/quality` | canonical |
| Change Review | dataset-scoped | `/api/observatory/branches`, `/api/observatory/branches/actions` | canonical |
| Logs | run-scoped | `/api/observatory/logs`, `/api/observatory/logs/facets` | canonical |
| Capability surfaces | `/governance` | matching `/api/observatory/{surface}` families | canonical |
| Extensions | none | `/api/observatory/extensions`, `/api/observatory/extension-manifests` | canonical |
| MCP run log streaming | none | `/api/loki` | intentional exception until MCP has an Observatory streaming contract |

Removed legacy browser surfaces and API families are hard 404s. Observatory
does not keep compatibility aliases for the old catalog, data, asset, branch,
extension, table, graph, hub, or pre-Dataset naming surfaces.

| Family | Endpoints | Contract intent |
| --- | --- | --- |
| Runtime overview | `overview`, `capabilities`, `capability-inventory`, `mission/context`, `mission/overview/*` | Tell the browser what the active project can show and do, including the resolved environment identity. |
| Services and operations | `services`, `services/{service_id}`, `services/{service_id}/probe`, `operations`, `operations/{operation_id}`, `runs`, `runs/{run_id}/retry`, `runs/{run_id}/cancel`, `mission/platform/*` | Expose runtime health, service details/probes, operator workflows, and run state. |
| Datasets | `datasets`, `datasets/{dataset_id}`, `mission/datasets/{dataset_id}`, `dataset-workflow/config`, `publishing`, `governance`, `pipelines` | Expose governed/publishable datasets, readiness, ownership, publication, and pipeline read models. |
| Run evidence | `mission/runs`, `mission/runs/{run_id}`, `mission/runs/{run_id}/{events,logs,quality,stages,artifacts,configuration,consumers,traces}` | Join run identity across logical/provider/attempt/report evidence; attempt-scoped, cursor-paginated. |
| Releases | `mission/releases/summary`, `mission/releases/candidates`, `mission/releases/candidates/{id}`, `…/preview`, `…/promotion`, `mission/releases/completed` | Derive release candidates from governed WAP reports; digest-bound preview; guarded, idempotent promotion. |
| Tables and lineage | `assets`, `assets/{asset_id}`, `asset-graph`, `tables`, `table-preview/{table_id}`, `query`, `saved-queries`, `stage-diff`, `row-journey/{table_id}/{row_id}` | Expose provider-neutral asset, table, query, diff, lineage, and row provenance read models. |
| Quality and logs | `quality`, `quality/{check_id}`, `logs`, `logs/facets` | Support quality triage and evidence inspection. |
| Change review | `branches`, `branches/{branch_name}`, `branches/actions` | Describe branch state and execute guarded branch operations. |
| Governance and settings | `mission/governance/*`, `mission/settings/*` | Expose governance evidence and settings state for the active environment. |
| Capability surfaces | `storage`, `observability`, `apis`, `bi` | Allow packages to contribute specialized operator surfaces without hardcoding provider APIs in the UI. |
| Extensions and settings | `extensions`, `extensions/{extension_id}`, `settings`, `search`, `actions` | Expose extension inventory, global search, settings state, and generic guarded actions. |

Route parameters that identify assets, tables, services, branches, and checks are stable resource identifiers, not provider URLs or secret-bearing connection strings.

## UI Contributions

A UI contribution describes how one capability appears in Observatory.

Required and supported fields:

| Field | Description |
| --- | --- |
| `name` | Stable contribution name shown in diagnostics and inventory output. |
| `capability_type` | Capability category, such as tables, lineage, quality, storage, governance, or services. |
| `capability_name` | Stable capability identifier within the capability type. |
| `surfaces` | Observatory surfaces where the contribution may appear. |
| `read_models` | Provider-neutral read contracts that the UI may query for this contribution. |
| `actions` | Guarded Observatory action contracts exposed for this contribution. |
| `native_links` | Declared provider-native escape hatches. Observatory currently suppresses emitted native links until a public link policy is defined. |
| `metadata` | Sanitized descriptive metadata for labels, grouping, filtering, and diagnostics. |

## Surfaces

Prefer existing Observatory surfaces before introducing new ones:

- `Datasets`
- `Tables`
- `Lineage`
- `Runs`
- `Quality`
- `Change Review`
- `Storage`
- `Observability`
- `Governance`
- `Publishing`
- `APIs`
- `BI`
- `Services`
- `Settings`

New surfaces should be reserved for capabilities that do not fit the existing
navigation model. A provider-specific surface is not, by itself, a reason
to add a surface.

Surface visibility should be capability-driven. For example, Change Review
should only appear when a branching or catalog provider contributes the
corresponding read models and actions. Do not promote a route into primary
navigation just because an endpoint exists.

## Metadata Rules

Metadata must be safe to return to the browser and safe to persist in logs,
diagnostics, screenshots, and support bundles.

Do not include:

- secrets
- passwords
- tokens
- raw provider URLs
- connection keys
- credentials
- private DSNs
- signed URLs
- provider-specific configuration values that grant access

Use metadata for neutral descriptors only, such as display labels, package
names, capability versions, supported modes, tags, and sanitized identifiers.

## Actions

Actions must use guarded Observatory action contracts. A UI contribution may
advertise an action only when the action is represented by a Observatory contract that
can describe:

- action identity
- target resource
- required parameters
- validation state
- safety guardrails
- execution status
- user-visible result or failure reason

Actions should not expose raw provider commands, shell commands, credentials,
or direct provider API calls to the UI.

Action endpoints:

- `POST /api/observatory/actions` for generic Observatory action contracts
- `POST /api/observatory/branches/actions` for branch-specific guarded actions

Both endpoints should return user-visible status and failure reasons that are
safe to display in browser UI and support bundles.

## Operation Journal

Observatory records guarded action outcomes in the provider-neutral operation
journal at `.phlo/observatory/operation_journal.json` for the active project.
The journal is exposed through `GET /api/observatory/operations` and
`GET /api/observatory/operations/{operation_id}`.

Journaled operations use the same `ObservatoryOperation` contract as provider-backed
maintenance read models. Action outcomes may have `succeeded`, `failed`, or
`skipped` status. A skipped operation means Observatory declined the action
because the required provider contract, capability, service state, or guardrail
was not available.

Operation metadata follows the standard Observatory metadata rules. It may include safe
values such as action id, action kind, risk level, required capability, message,
recorded timestamp, and affected file paths. It must not include secrets,
provider credentials, private connection strings, raw provider URLs, or signed
URLs.

Journaled operations also include an agent-ready observability contract under
`metadata.observability_contract`. The v1 schema name is
`phlo.operation_observability.v1` and carries stable operation, trace, log,
metric, and incident identifiers. Agents should use those identifiers rather
than parsing names or provider-specific metadata.

`GET /api/observatory/operations/{operation_id}/agent-context` returns a
compact incident and operation context for MCP clients. It includes the stable
identifiers, operation health, related resources, correlated logs, available
follow-up actions, and the retained history limit for the local journal.

## Native Links

Native links may eventually point users to provider-native tools when Observatory
does not yet cover a workflow or when the native tool remains the best place for
deep inspection. The current Observatory inventory contract accepts declared native links
but suppresses them in browser payloads until a public link policy is defined.

Native links are escape hatches. They must not replace provider-neutral read
models, Observatory surfaces, or guarded Observatory actions for core workflows.

Native link labels and destinations must not disclose credentials, embedded
tokens, private connection strings, or raw internal provider URLs.
