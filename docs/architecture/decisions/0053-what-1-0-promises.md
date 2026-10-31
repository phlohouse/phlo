# ADR 0053: What 1.0 Promises

## Status

**Accepted**

Amended (2026-10-31): the exit criteria in decision 7 are reduced to the evidence that protects Phlo's one real deployment and freezes the surfaces it depends on. Phlo has a single maintainer who is also its only user, so criteria written for an audience that does not exist yet (a required macOS CI lane, `blessed_core`-only lockstep versioning, external-consumer evidence) are deferred until a second user appears. Decisions 1, 3, 4 and 5 are amended to match: decision 1 names the maintainer's deployment as the live deployment, decision 3 lets the route deletion ship in the `1.0.0` release commit, decision 4 applies the shared version to the whole workspace, and decision 5 replaces the macOS CI lane with a recorded manual run. Decisions 2 and 6 are unchanged. ADR 0054 is amended in the same change to scope its per-package versioning to releases below `1.0.0`.

- Date: 2026-10-30
- Decision owner: iamgp
- Scope: the `1.0.0` release of the `blessed_core` profile in `registry/support/v1.json`, and the compatibility rules for the `1.x` release line
- Builds on: ADR 0047 (production trust and readiness), ADR 0048 (run evidence), ADR 0049 (continuity and upgrade), ADR 0050 (release promotion)

## Context

The README and the documentation home page both say that Phlo is alpha. The README says APIs, provider contracts, and the project layout may change "before 1.0". No decision says what 1.0 is, what it freezes, or how the project will know it has arrived.

The release machinery and the support boundary already exist:

- `registry/support/v1.json` declares `release_line: "1.x"` and a `blessed_core` profile with status `scope-frozen`. It contains 14 packages, 11 services, and 11 capabilities.
- The manifest's `current_release` is `0.18.0` with maturity `alpha` and `production_ready: false`. The manifest schema pins both values as constants (`maturity: const "alpha"`, `production_ready: const false`), and it pins `artifact_pinning` to `not_yet_published`.
- All five required gates (`security`, `run_evidence`, `maintenance`, `upgrade_restore`, `golden_path`) report `planned`. Every `blessed_core` package component reports `blocked`. `production.status` is `blocked`.
- ADRs 0047–0050 define each gate's evidence. ADR 0050 requires three qualifying runs against an immutable candidate before promotion. It also lets an alpha or beta candidate that is not production-ready skip production preflight and supported upgrade.
- The release set is not versioned in lockstep. `phlo`, `phlo-api`, `phlo-dagster`, and `phlo-observatory` are at `0.18.0`, the provider packages are at `0.17.0`, `phlo-core-plugins` and `phlo-pandera` are at `0.16.1`, and `phlo-observe-plugin` is at `0.2.1`. `relx.toml` versions each package from conventional commits.
- CI runs Python 3.12 on `ubuntu-latest` only. The Windows Compose portability lane runs the release golden-path harness and launcher contract tests on `windows-latest`. The manifest lists Python 3.13, native macOS, and non-x86 image architectures as `unverified`.
- At `7feced4d`, `phlo-api` serves 165 `/api/v1` endpoints plus 108 non-v1 endpoints in 13 families, including `/health` and 68 under `/api/observatory`. The bundled Observatory calls `/api/v1`. `phlo-mcp`, a `preview` package, still calls `/api/observatory` and other legacy families.
- Deprecated names such as `phlo_quality` and `phlo_ingestion` remain in `phlo.__all__` with a published 0.19.0 removal deadline.
- Phlo has one maintainer, who is also its only user. No Phlo deployment runs continuously yet; the maintainer is standing one up (a Nightscout CGM lakehouse running the blessed stack, including `phlo-minio`, on the maintainer's VPS behind a private HTTPS preview URL) to be the live deployment the gates require. No external consumer of any surface has been observed.

Without a decision, "1.0" could mean an API freeze while production remains blocked, or a production claim with no defined compatibility surface. Either outcome would make a promise the project cannot check.

## Decision

### 1. Definition of 1.0

`1.0.0` is published only when `registry/support/v1.json` at the release commit reports `current_release.production_ready: true`. That value is permitted only when all of the following hold:

- `gates.status` reports `passed` for each of `security`, `run_evidence`, `maintenance`, `upgrade_restore`, and `golden_path`.
- Each gate's evidence was produced on a live, production-shaped deployment of the exact candidate, as defined by ADRs 0047–0050. Fixture or disposable-service evidence does not pass a gate. The maintainer's own deployment, the Nightscout CGM lakehouse, is that live deployment; it is the only one that exists.
- Every `blessed_core` component in `gates.components` reports `passed` or `not_required`, with evidence.
- The candidate is promoted under ADR 0050 with the full production qualification scope: production preflight and a real supported upgrade and recovery path are included. ADR 0050's artifact-only scope for alpha and beta candidates cannot produce `1.0.0`.

A `1.0.0` version string does not by itself mean 1.0. A release that does not meet these conditions keeps a `0.x` version or an explicit pre-release suffix.

The support manifest schema currently forbids `production_ready: true`, and `production.status` allows only `blocked` or `required_before_release`. Changing the `production_ready` and `maturity` constants and `artifact_pinning`, and adding a `production.status` value `passed` (matching the gate vocabulary) that the 1.0 manifest uses, is part of the 1.0 work. It is a reviewed change on `main` under ADR 0050 concern 7. It is not a side effect of publication.

Release tooling rejects any version at or above `1.0.0` while the manifest at the release commit does not report `production_ready: true`. The check belongs in `scripts/validate_support_manifest.py` or the ADR 0050 promotion gate, so a breaking-change commit cannot propose `1.0.0` through conventional-commit versioning alone.

### 2. The compatibility surface

The 1.0 promise covers the following surfaces of the `blessed_core` profile. Each surface has a source of truth. A name or behaviour that is not reachable through one of these surfaces is not covered.

| Surface | What is covered | Source of truth |
| --- | --- | --- |
| `phlo` CLI | Command paths, options, arguments, defaults, and exit status of commands contributed by `blessed_core` packages. The JSON envelope (`schema_version`, `status`, `data`, `warnings`, `errors`, `reason_code`, `next_steps`) emitted with `--json` or `--format json`. Versioned operation envelopes (ADR 0049) and readiness reports (ADR 0047). | `src/phlo/cli`, `src/phlo/cli/output.py`, `docs/reference/generated/cli.json`, [CLI reference](../../reference/cli.md) |
| Python API | Names in `phlo.__all__`; the `phlo.ingest`, `phlo.publish`, `phlo.observe`, `phlo.contract`, and `phlo.access` decorators; `phlo.quality` and `phlo.quality.rules`; `phlo.export`; contracts and helpers; and the `blessed_core` provider APIs (`phlo.ingest.dlt`, `phlo.quality.pandera`). Signatures, documented behaviour, and raised exception types are covered. | `src/phlo/__init__.py`, [Python API reference](../../reference/python-api.md) |
| Project layout and configuration | The generated project tree; the `phlo.yaml` models in `src/phlo/config_schema.py`; environment-file names and precedence; and documented core and default-stack settings. | [Project layout](../../reference/project-layout.md), [Configuration](../../reference/configuration.md), [Settings inventory](../../reference/settings.md) |
| HTTP API | Every `/api/v1` method and path, its request and response schemas, status codes, error envelope, pagination, and authorisation actions. The anonymous `/health` liveness route. | `packages/phlo-api`, [HTTP API reference](../../reference/http-api.md), `docs/reference/generated/http-api.json` |
| Plugin contracts | The entry-point groups in `PLUGIN_FAMILIES`, `phlo.plugins.observatory`, and `phlo.project_templates`; the plugin base classes in `phlo.plugins.base`; capability specifications in `phlo.capabilities.specs`; hook event types, the `HookEvent` envelope and its `version`, and the hook registration and failure-policy contract; `service.yaml`; and Observatory extension manifests. | [Plugin API](../../reference/plugin-api.md), [Capabilities and plugins](../../reference/capabilities-and-plugins.md), [Hooks and events](../../reference/hooks-and-events.md), [Observatory extensions](../../reference/observatory-extensions.md), ADR 0030, ADR 0033, ADR 0042 |
| Support manifest | The `phlo.support.v1` document shape and its closed vocabularies. | `registry/support/schema/v1.json` |
| Run-evidence store | The persisted run-evidence schema version recorded in `release_set.schemas.database` (currently `5`) and the records it defines. | `src/phlo/run_evidence/models.py` |
| Configuration schema | The configuration schema version recorded in `release_set.schemas.configuration` (currently `"1"`). | `src/phlo/config/settings.py` |

The 1.0 promise does not cover:

- `preview` and `development_only` packages, services, and capabilities, including `phlo-mcp`, `phlo-testing`, and `phlo-transform`.
- `supported_optional` packages, including `phlo-sling` and `phlo.ingest.sling`, until a later amendment admits them with passed gates.
- Observatory internal component APIs: frontend modules, data-adapter helpers, component names, and the Node-side API adapters.
- Generated Compose file contents under `.phlo/`, including `docker-compose.yml`, generated service directories, and generated environment-file contents. The file locations in the project layout reference remain covered.
- Service image tags and digests. The release BOM (ADR 0050) records them per release, and they change between releases.
- Human-readable CLI text, log messages, and error wording. Reason codes and JSON fields are covered.
- Underscore-prefixed modules and names, and modules not listed above.
- Non-`/api/v1` HTTP families other than `/health`. Decision 3 removes `/api/observatory`. The remaining legacy families are outside the promise.
- `phlo.transform.sql`, which requires providers that no shipped package supplies.
- Provider-native shapes that Phlo passes through, such as Dagster GraphQL fields and raw provider records.

### 3. Legacy `/api/observatory` routes

All 68 `/api/observatory` endpoints are removed by the `1.0.0` release commit. Amended 2026-10-31: the deletion may ship in `1.0.0` itself, provided that the preceding release announced every route with its replacement; the exit criterion is then evaluated at the release commit rather than before it. The routes are not frozen under the promise.

Removal follows the [phase-0 classification](../unified-api-phase-0.md) and the ["Remaining legacy consumers"](../../reference/unified-api-remaining-acceptance.md#remaining-legacy-consumers) inventory:

1. Each capability classified as `Keep` or `Migrate` gets a canonical `/api/v1` resource first. This includes capabilities that have no v1 resource today: extension manifests, extension assets and settings, package installation, operations and agent context, aggregate quality, asset partitions, asset-graph neighbours, schema diff, and search.
2. Every first-party consumer migrates to those resources. This includes `phlo-mcp` (`packages/phlo-mcp/src/phlo_mcp/api_client.py`) and every URL producer, such as the `assets_base_path` that `observatory_api/extensions.py` advertises.
3. The legacy routes are removed. The `security_manifest.py` classification and the generated HTTP inventory are updated, and the release notes list each removed route with its replacement.

An in-repository caller search does not show that external clients are absent. Each removal is therefore announced in a release before the route is deleted, with the replacement and the existing usage counters where they exist. The `/api/observatory/pipelines` removal already scheduled for 0.19.0 proceeds as published.

Maintainer decision, 2026-10-31: after #1233, #1232 and #1236, every capability with a first-party caller has a `/api/v1` resource and every first-party caller uses it. The 35 legacy routes that remain without a `/api/v1` resource (listed as "None" in the [legacy route replacements](../../reference/http-api.md#legacy-route-replacements) table: settings, preferences, logs, tables and table preview, row journey, stage diff, workflow wizard, dataset and governance reads, contributing rows, storage, observability, API and BI integrations, capability discovery, generic actions, and the remaining extension and operation detail reads) have no caller in the bundled Observatory, in `phlo-mcp`, or in any other package. They are removed at the `1.0.0` release commit without a replacement, and the release notes say so for each route. Adding a `/api/v1` resource for one of these capabilities later is an additive change that decision 4 permits; it does not need to happen before 1.0. This supersedes the phase-0 statement that no absence of an in-repository caller authorises removing a public capability: for a project with one user, the maintainer's own inventory is the complete inventory.

### 4. Versioning and deprecation after 1.0

From `1.0.0`, every package in the release set shares one version. Amended 2026-10-31: the shared version applies to the whole workspace, not only to `blessed_core`, because the release tooling applies one `--next-version` to every package in the release set (#1231) and a `blessed_core`-only group would need release-tool support that has no consumer yet. ADR 0054's per-package versioning applies to releases below `1.0.0` only. The version number does not extend the promise to `preview`, `development_only`, or `supported_optional` packages; the support manifest profiles define coverage. The `blessed_core` packages follow [Semantic Versioning 2.0.0](https://semver.org/spec/v2.0.0.html) across the covered surfaces:

- A **patch** release fixes defects without changing a covered surface.
- A **minor** release may add to a covered surface and may deprecate a name. It never removes or incompatibly changes one.
- A **major** release may remove or incompatibly change a covered name, but only one that was deprecated as described below.

A covered public name is removed only after a documented deprecation that meets all of these conditions:

- The deprecation ships in a release at least one minor release before the removing major release.
- The deprecation names the replacement and the removal release, both in the runtime warning or OpenAPI `deprecated` marker and in the [removal schedule](../../reference/python-api.md#removal-schedule).
- The deprecation cites usage evidence: a compatibility counter, as in the [public-name compatibility inventory](../../reference/public-name-compatibility.md), or a recorded consumer migration. Disabled telemetry or a zero counter alone does not prove that no consumers remain.

A change to the run-evidence or configuration schema version ships with a supported forward migration (`phlo migrate`, `phlo config upgrade`, or the ADR 0049 upgrade path) in the same release. A schema change that 1.x cannot read is a major-release change.

The deprecations already scheduled for 0.19.0 complete before `1.0.0`, so `1.0.0` carries no name with a pre-1.0 removal deadline.

### 5. Platform matrix

| Dimension | 1.0 support | Required evidence |
| --- | --- | --- |
| Python | 3.12 and 3.13 | Core and package test lanes run on both versions in required CI. |
| Linux | Supported for the full stack and the quickstart path | The existing `ubuntu-latest` lanes, plus the clean-host qualifying runs under ADR 0050. |
| macOS | Supported for the quickstart path with Docker Desktop or another Compose v2 provider | Amended 2026-10-31: a recorded manual quickstart run on the maintainer's Apple-silicon Mac for the `1.0.0` candidate and for each later minor release, from `phlo init` through a materialised table viewed in Observatory, noting the architecture and Compose provider. A required CI lane is deferred until a second user depends on macOS. |
| Windows | Supported only through WSL 2, as the install guide documents | The existing Windows Compose portability lane, unchanged. |

`runtime` in the support manifest moves Python 3.13 and macOS from `unverified` to `supported` only when that evidence exists. The `pyproject.toml` classifiers then list 3.13.

Apple silicon macOS requires `linux/arm64` first-party images, or a quickstart qualified under documented emulation. The manifest records which one applies, because non-x86 image architectures are currently `unverified`.

### 6. Feature freeze on `blessed_core`

From the date this ADR is accepted until `1.0.0` ships, no new capability enters the `blessed_core` profile. During the freeze:

- No component is added to `profiles.blessed_core`.
- `blessed_core` packages add no new CLI command, `/api/v1` resource, public Python name, entry-point group, hook event type, or `phlo.yaml` section, except as permitted below.
- Work on `blessed_core` is limited to closing the five gates, fixing defects, security fixes, documentation, the platform work in decision 5, completing the scheduled deprecations, and the `/api/v1` resources and migrations that decision 3 requires.

The freeze does not apply to `preview`, `development_only`, or `supported_optional` packages, provided that their changes need no new `blessed_core` surface. A change that needs an exception requires a recorded maintainer decision that names this ADR.

### 7. Exit criteria

Amended 2026-10-31. Phlo has one maintainer who is also its only user. The criteria are therefore the evidence that protects that one deployment and freezes the surfaces it depends on. Criteria that exist to satisfy an audience that does not exist yet are deferred, and the tooling already built for them stays in place to record what happened rather than to add steps. Items are checked as they are met, with the merged change in brackets.

`1.0.0` may be promoted when every item below is checked, with evidence linked from the release evidence bundle.

Operate it:

- [ ] The maintainer's deployment has run the release line for at least 7 consecutive days of scheduled Nightscout ingestion and downstream materialisations, with Observatory run evidence for every scheduled run, no manual intervention, and no platform patch, and the run-evidence export for that period is in the evidence bundle.
- [ ] The five gates (`security`, `run_evidence`, `maintenance`, `upgrade_restore`, `golden_path`) report `passed` on that deployment, with the evidence that ADRs 0047–0050 define. In particular, `services start --production` passes Stage A and Stage B there; one supported upgrade from the previous minor release to the candidate kept the maintainer's data intact; and one backup was restored to a fresh host and verified.
- [ ] The candidate is promoted under ADR 0050 in production scope. The qualifying runs are automated and may execute on one host on one day (ADR 0050, amended 2026-10-15).

Freeze it:

- [x] The compatibility surface in decision 2 has a checked inventory: generated CLI and HTTP references, `phlo.__all__`, the entry-point families, and the two schema versions (#1222).
- [x] The 0.19.0 removal schedule is complete, and `phlo.__all__` contains no deprecated name (#1216, #1223, #1224).
- [ ] All 68 `/api/observatory` routes are removed, and every first-party consumer uses `/api/v1` (decision 3). The 0.19.0 release notes announce each route and its replacement; the deletion ships in the following release, which may be `1.0.0`, and this item is evaluated at the release commit.
- [x] Python 3.12 and 3.13 lanes are green and required, and `runtime` in the manifest reflects them (#1227).
- [ ] The macOS quickstart is recorded on the maintainer's Mac for the `1.0.0` candidate (decision 5).

Declare it:

- [x] The support manifest schema permits `production_ready: true`, a non-alpha maturity, published artifact pinning, and `production.status: passed`, and the validator ties each to the gates (#1221).
- [x] A release-tooling check rejects a version >= 1.0.0 unless the manifest reports `production_ready: true`, with a test (#1221).
- [x] The release tooling versions the release set in lockstep from `1.0.0` (#1231).
- [ ] One reviewed readiness commit on `main` before the release PR sets `production_ready: true`, maturity `supported`, `artifact_pinning: published` and `production.status: passed`, regenerates the references, and removes the alpha wording from the README, the documentation home page, and the `Development Status` classifier.

Deferred until a second user exists, and not exit criteria: a required macOS CI lane; `blessed_core`-only lockstep versioning, which needs a ReleaseX version group; and external-consumer usage evidence for the legacy routes. Adding any of them back is an amendment to this ADR.

## Consequences

- 1.0 is an operator outcome, not a version bump. It may arrive later than an API-only definition would allow, and the project stays on `0.x` until the live-deployment evidence exists.
- Users get one checkable list of what is covered and one deprecation rule. Reviewers judge each change to a covered surface against decision 4.
- The `/api/observatory` removal requires new `/api/v1` resources for retained capabilities. This is the largest implementation item that the freeze does not block.
- `phlo-mcp` must migrate before 1.0 even though it is a `preview` package, because it is a first-party consumer of the removed routes.
- Lockstep versioning replaces per-package conventional-commit versions for the whole release set from `1.0.0` (#1231). Decisions 4 and 5 take effect at 1.0; until then, ADR 0054 and ADR 0059 describe the current baseline.
- The macOS quickstart evidence is a recorded manual run on the maintainer's Mac. A CI lane would need a host that can run a Compose v2 engine, which GitHub-hosted macOS runners do not provide by default; it is deferred until a second user depends on macOS.
- The 7-day operation criterion makes `1.0.0` wait on calendar time on the maintainer's deployment, not only on work. Seven days covers a full week of the maintainer's own CGM schedule, the shortest period over which a scheduled 5–15 minute feed shows its weekly pattern and any day-boundary defect.
- The deferred criteria return by amendment when a second user appears. Until then, the project does not spend effort proving properties that nobody consumes.
- Python 3.13 support depends on upstream dependency compatibility. A blocking upstream defect delays 1.0. It does not drop 3.13 silently.
- Contributors cannot add `blessed_core` features until 1.0 ships. Preview work continues.

## Alternatives Considered

### 1.0 as API, CLI, and layout stability while production gates stay blocked

Rejected. This would ship a compatibility promise sooner and let users depend on stable names. However, the README and documentation already describe 1.0 as the point where Phlo is safe to rely on. A 1.0 that is not production-ready would contradict ADR 0047's operator outcome and force a second "production" milestone that users could confuse with 1.0.

### Freeze both `/api/v1` and the legacy `/api/observatory` routes under the promise

Rejected. Keeping both would avoid migration work for `phlo-mcp` and unknown external clients. However, it would double the covered HTTP surface, keep two authorisation regimes (legacy routes can run with optional authorisation outside regulated and production modes), and commit 1.x to routes that the phase-0 plan already marks for retirement.

### Python 3.12 on Linux only

Rejected. This matches today's tested matrix and would remove the macOS and 3.13 work from the critical path. However, macOS is a common developer platform for the quickstart path, and Python 3.13 is current. Declaring 1.0 without them would make the support boundary narrower than the documented install path implies.

### Allow removal in a minor release after one minor release of deprecation

Rejected. A shorter deprecation cycle would let names retire faster. However, removal in a minor release would break Semantic Versioning for covered surfaces. Requiring removal in a major release keeps the version number meaningful to users who pin compatible ranges.

### Keep the exit criteria as accepted on 2026-10-30

Rejected on 2026-10-31. The original list required a macOS CI lane, `blessed_core`-only lockstep versioning, and external-consumer evidence for legacy routes. Each proves a property to an audience that does not exist: Phlo has one maintainer and one user, and no external consumer has been observed. Keeping them would have delayed `1.0.0` without making the one real deployment safer. The gates, the inventory, and the readiness guard stay, because they protect that deployment and the surfaces it depends on.

### Continue feature work on `blessed_core` in parallel with gate closure

Rejected. Parallel work would deliver features sooner. However, each new capability adds to the surface that the gates, the compatibility inventory, and the deprecation rule must cover. That moves the exit criteria while the project is trying to meet them.

## Related

- ADR 0006: Maintain explicit public exports
- ADR 0030: Unified plugin system with registry
- ADR 0033: Hook-based capability plugins
- ADR 0042: Observatory extension manifests and native UI plugins
- ADR 0044: CLI command ownership by package
- ADRs 0047–0050: the v1 production, evidence, continuity, and promotion contracts
- ADR 0054: Workspace monorepo released as one release set. Decision 4 changes its per-package versioning: the whole release set becomes lockstep at 1.0.0.
- ADR 0059: Python 3.12 and generated Docker Compose as the runtime baseline. Decision 5 widens its interpreter and platform baseline to Python 3.13 and macOS for 1.0.
- ADR 0065: Deprecations name a removal release, a replacement and a usage measure. Decision 4 keeps this rule and adds major-only removal for covered names.
- `registry/support/v1.json` and `registry/support/schema/v1.json`
- [Support matrix](../../reference/support-matrix.md)
- [Unified API phase 0](../unified-api-phase-0.md) and [remaining acceptance](../../reference/unified-api-remaining-acceptance.md)
- [Public name compatibility](../../reference/public-name-compatibility.md)
