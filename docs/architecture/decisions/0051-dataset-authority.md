# ADR 0051: Dataset authority contract

- **Status:** Accepted
- **Approval:** _Maintainer sign-off recorded here by the programme controller on acceptance._
- **Date:** 2026-09-03
- **Task:** T4-01 (Phlo-V1 roadmap, Phase 4 — Dataset product centre); GitHub issue #838
- **Supersedes:** Observatory's de-facto authority over Dataset workflow state. No earlier ADR
  records that authority; before this ADR it existed only as behavior in
  `packages/phlo-api/src/phlo_api/observatory_api/observatory.py` (its own governance matrix,
  readiness computation, and `.phlo/observatory/dataset_workflow.json` overlay). This ADR
  revokes it. Earlier programme ADRs in the accepted series are 0047 (production
  trust/readiness), 0048 (run-evidence semantics), 0049 (continuity/upgrade); the release
  promotion contract is ADR 0050 on a separate Wave-1 branch.

## Context

Today there are two independent derivations of Dataset facts:

- **Core** owns declarations and one governance check. A table's `published` flag comes from
  `@phlo.publish` asset metadata applied to the governance surface
  (`src/phlo/governance/surface.py`, `apply_publish` at lines 175-177, surfaced at 221 and
  printed by `phlo governance check`, `src/phlo/cli/commands/governance.py:70`). Publishing
  materializes through a publish-target provider after one governance readiness check
  (`src/phlo/helpers/publishing.py`: `governance_publish_readiness` and
  `require_governance_ready` raise `PhloConfigError` when declarations are missing or fail).
- **Observatory** independently derives Controls and readiness from its own loads of datasets
  and quality checks (`observatory.py` `_load_governance_matrix` at 1383-1401,
  `_publishing_readiness` at 1739-1794, which computes its own `is_publishable`), and stores
  workflow state in its own overlay file `.phlo/observatory/dataset_workflow.json`
  (`_dataset_workflow_path`/`_dataset_workflow_write_lock` at 328-344, whole-document
  load/replace at 346-376), with publication transitions writing that file directly
  (`_execute_dataset_publication_action` at 4024-4037) and candidate transitions beside them
  (`_execute_candidate_workflow_action` at 4070-4096).

Consequences visible in the code today: UI and core can disagree on governed, ready, and
published; the Observatory `/actions` route performs mutations without the scope checks its
sibling routes already use (`post_observatory_action` at 4942-4966 has no `require_scope`,
while run and asset mutation routes call `require_scope(http_request, "lakehouse:operate")`
at 4319/4350/4559/4590 and workflow-wizard calls `require_scope(http_request, "project:write")`
at 4889/4901); and Observatory search has no `dataset` kind at all
(`observatory_search.py` indexes services, assets, tables, operations, quality, extensions).
Horizon B — CLI, API, Observatory, and provider returning identical Dataset facts, stable
across restarts and workers — is unreachable until authority is frozen.

## Terms (recorded, then resolved)

The word "publish" is overloaded. This ADR fixes both senses; "Dataset" (capital D) is the
governed product, never the raw table.

| Term | Meaning | Plane |
| --- | --- | --- |
| **Materialization** ("publish to target") | `publish_table`/`publish_many` writing a lakehouse table through a publish-target provider. | Data plane |
| **Publication transition** ("publish internally", "retire") | A governance-plane change of a Dataset's `publication_state`. Never moves data. | Governance plane |
| **Candidate** | A lakehouse table not yet promoted to a Dataset. Identified as `candidate:<table_id>`. | Governance plane |
| **Promoted Dataset** | A Dataset whose identity is `<table_id>`; created by the candidate `promote` transition. | Governance plane |
| **Readiness** | Whether the policy permits a publication transition now. Derived from policy plus evidence. | Governance plane |

"Publish" unqualified always means the data-plane materialization. Any surface that means the
lifecycle transition says "publish internally" (the existing Observatory action label).

## Decision table (canonical facts — no inference required)

Canonical authority is **core** for identity, policy, and state; evidence is produced by
executors and stored where ADR 0048 run-evidence semantics place it; Observatory is a pure
read-model client and may render but never derive or decide.

| Concern | Candidate Dataset | Promoted Dataset | Canonical owner |
| --- | --- | --- | --- |
| Identity (ID) | `candidate:<table_id>` (table's stable asset key) | `<table_id>` — promotion preserves the table key as the Dataset ID | Core |
| Workflow state | `claimed → review → promoted \| rejected` (no record = open); `rejected` is terminal | Record carries `state: promoted`, `publication_state`, `approval_state` | Core (durable store) |
| Publication state | n/a (candidates have none) | `draft → published → retired`; `retired` is terminal | Core (durable store) |
| Policy (readiness rules) | Promotion policy: claim, review, promote, reject | Publication policy: which controls block publish/retire; from `@phlo.contract`, `@phlo.publish`, `@phlo.access` declarations | Core |
| Policy version | Fixed with the Dataset record; changes bump the record's `schema_version` | Same | Core |
| Evidence (inputs to readiness) | Quality checks and governance surface readings for the table | Same, plus run evidence per ADR 0048 | Executors produce; core reads; ADR 0048 store holds |
| Readiness verdict | Computed once by core from policy + evidence | Same | Core |
| Durable store | Project-scoped durable state via the settings service (`observatory_durable_state.py`): namespace `observatory.dataset_workflow.<sha256(project_root)>`, collection `schema_version: 2` | Same record, keyed `<table_id>` | Core (durable store) |
| Transition authorization | Authenticated principal with `lakehouse:operate` scope (API); operator identity (CLI); UI acts only via API | Same | Core API layer, Plan 001 semantics (PRs #810/#811, production trust ADR 0047) |
| Idempotency key | Client-supplied `action_id` stored on the record; replay returns the original outcome | Same | Core (durable store) |
| Audit | Append-only audit event per transition attempt: actor, scope, `action_id`, resource, before → after, outcome | Same | Core API layer |

### Storage, concurrency, and multi-worker semantics

- The durable store is the transactional, project-scoped settings-backed store already used by
  other Observatory collections (`observatory_durable_state.py`: hashed project-root
  namespace, `load_collection`/`mutate_collection`, `STATE_SCHEMA_VERSION`,
  `StorageCorruptionError` on version mismatch or unreadable state). This is the blessed
  durable-store candidate; no new infrastructure is introduced.
- Every mutation runs inside the store's transaction (single serialized writer per
  namespace). The in-process `RLock` plus `flock` file lock pattern stays only for the
  migration window (below); the durable transaction, not the file lock, is the authority
  after migration.
- State on restart and across workers is read from the durable store, never from memory or
  the legacy file once imported.

### Idempotency, conflicts, and unknown outcomes

- A transition is a compare-and-set: `(resource_id, expected current state, action)` plus the
  client `action_id`. Commit sets the new state and records the `action_id`.
- **Replay:** a request carrying an `action_id` already committed with an identical request
  returns the original committed outcome without re-applying. Publishing an already-published
  Dataset with a fresh `action_id` is likewise an idempotent success reporting the existing
  state.
- **Conflict:** the current state does not match the expected pre-state (for example `retire`
  on a `draft` Dataset, or `publish` on a `retired` Dataset — `retired` is terminal). The
  result is a failed outcome naming the conflict; nothing is written.
- **Unknown outcome:** if a worker crashes between commit and response, the durable store is
  the single truth. Retrying with the same `action_id` yields the committed outcome;
  re-driving the transition is safe because it is a state set, not an increment. No surface
  invents a synthetic outcome.

### Authorization (bound to Plan 001)

- Transition authorization follows the Plan 001 semantics merged in PRs #810/#811 and the
  production trust contract (ADR 0047): in production every mutating call is authenticated,
  scoped, and fails closed; locally the same scope check runs with the operator identity.
- The transition **actor** is the authenticated principal on the API path (or the operator
  identity on the CLI path). The UI is never an authority; it calls the API.
- Every transition attempt appends an audit event with actor, scope, `action_id`, resource,
  before → after, and outcome. The `audit_operation` machinery already used by the run and
  asset mutation routes is the pattern.
- Note for Plans 020-023: the current `/actions` route does not enforce a scope
  (`observatory.py:4942-4966`); implementing this ADR requires adding the same
  `require_scope`/audit treatment its sibling routes already have. That change is
  implementation, out of scope here.

### Compatibility and overlay migration

- **Identity is preserved; no version break.** Promotion already records
  `dataset_id = table.id` and candidates are already addressed `candidate:<table_id>`
  (`observatory.py` 1180-1209, 4070-4096). This ADR freezes those forms.
- **Migration is a versioned, exactly-once import.** On first load of the
  `observatory.dataset_workflow.<project>` namespace, the store imports
  `.phlo/observatory/dataset_workflow.json` (datasets, candidates, config), preserves every
  record ID and field verbatim, and stamps the payload `schema_version: 2`. After import the
  legacy file is never read again; it is left untouched on disk for downgrade safety.
- **What happens to every existing overlay record:**
  - `datasets` records (publication/approval state): imported as-is into the durable
    namespace; unknown or missing states import as `draft` with a migration note field.
  - `candidates` records (`claimed`/`review`/`promoted`/`rejected`): imported as-is,
    including `state: promoted` records with their `dataset_id`, so promoted identity and
    publication state survive.
  - `config` (default owner, approval-state list): imported as workflow configuration.
  - A legacy file that fails to parse or whose `schema_version` is ahead of the store raises
    `StorageCorruptionError` (fail closed), matching existing store semantics.
- CLI and API payload shapes gain fields; nothing existing is removed in the same release
  that introduces the store.

## Alternatives considered

1. **Keep the Observatory overlay as authority.** Rejected: core and UI derive facts
   independently today, which is exactly the disagreement Horizon B forbids; the overlay is a
   whole-document JSON rewrite with a lock file, not a transactional store.
2. **Move Dataset state into the warehouse/catalog provider.** Rejected: Dataset workflow is
   governance-plane state about provider data, not provider data itself; it must exist and be
   readable before any provider is healthy.
3. **Event-sourced workflow journal.** Deferred: ADR 0048 run-evidence already covers
   evidence semantics; an event log would add a replay subsystem without changing any
   authority boundary decided here. The audit stream (append-only events) captures the
   history this ADR needs.

## Consequences

- Observatory loses evaluation authority: `_load_governance_matrix`, `_publishing_readiness`,
  the overlay load/write/lock helpers, and the two `_execute_*_workflow_action` paths become
  renderers and thin dispatchers over core read models (successor seams below). UI and core
  can no longer disagree by construction.
- Readiness is computed once, in core, from declarations and evidence; the readiness contract
  the PRD assigns to core (the four-features PRD no longer exists on `main`; the Phlo-V1
  roadmap Phase 4 exit is the authoritative statement) is realized here.
- `retired` becoming terminal is a behavior tightening: re-publication of a retired Dataset
  is a conflict, not a silent allowed transition.
- New writes require an authenticated, scoped, audited actor even on the generic `/actions`
  route.

## Successor seams (files, symbols, fixtures)

Plans 020-023 must be implementable from this table without inventing policy.

- **Core (new home of authority):** a core Dataset module owning identity, workflow state,
  publication state, policy evaluation, and the durable-store records defined above. It
  reuses `governance_publish_readiness`/`require_governance_ready`
  (`src/phlo/helpers/publishing.py`) and the governance surface
  (`src/phlo/governance/surface.py`) rather than duplicating them.
- **Observatory (revoked authority, pure client):** `_load_dataset_workflow_state`,
  `_write_dataset_workflow_state`, `_dataset_workflow_write_lock`,
  `_workflow_dataset_overlay`, `_workflow_candidate_overlay`, `_execute_dataset_publication_action`,
  `_execute_candidate_workflow_action`, and the derivation logic in
  `_load_governance_matrix`/`_publishing_readiness` are replaced by reads of core read
  models and dispatched transitions.
- **API/UI:** `resources.ts` Dataset resources (list, publishing-readiness, workflow config,
  detail — lines 531-604) keep their routes and shapes, backed by core read models; the
  generic `/actions` route dispatches through the scoped, audited core transition API.
- **Search:** `observatory_search.py` gains a `dataset` kind mapping to the Dataset detail
  route (currently absent).
- **CLI:** `phlo governance check` continues to print declaration-derived facts; Dataset
  workflow commands (claim/promote/reject/publish/retire) call the same core transition API
  with the operator identity.

**Acceptance fixtures (Horizon B):**

1. CLI, API, Observatory read model, and provider return identical identity, state, policy,
   and evidence facts for the same Dataset — no field differs by surface.
2. API and worker restarts, and concurrent workers, observe identical facts from the durable
   store; no divergence after restart.
3. Two workers race the same transition: exactly one commit, one idempotent replay; audit
   events account for both attempts.
4. A pre-existing `dataset_workflow.json` containing claimed, review, promoted, rejected,
   published, and retired records migrates with IDs and states preserved; the legacy file is
   untouched and ignored afterwards.
5. In production configuration, an unauthenticated or out-of-scope transition attempt fails
   closed and is audited.
