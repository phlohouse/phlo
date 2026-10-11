# Process settings and environment-read classification

This reference records the issue #1012 inventory against `origin/main` at
`adf06946d4a8bbf4da2e5b1629710f5ba0e489c4`, after #1098, #1099 and #1100.
It does not describe an older local `main` checkout.

## Supported settings and ownership

The original inventory is preserved in
[`phlo_env_reads_inventory.json`](../../tests/tooling/phlo_env_reads_inventory.json):
151 file/key pairs across 77 files, covering 103 distinct keys. All entries are
supported settings except the six test hooks listed below. Each supported key
has a package-owned settings field in the generated [settings reference](../reference/settings.md).
That reference also includes the additional dynamic keys discovered during this
migration. The completeness regression checks both sets against the generated
environment names, not a substring match on field names.

| Owner | Settings models | Scope |
| --- | --- | --- |
| `phlo` | `Settings`, `CoreProcessSettings`, `LineageDatabaseSettings`, `ObservatoryDatabaseSettings`, `JwtTimingSettings`, `RunEvidencePoolSettings`, `PluginDiscoverySettings`, `TelemetryProcessSettings` | Shared authentication, security, run evidence, project-root overrides, operational identity, database declarations, plugin bootstrap and telemetry |
| `phlo-api` | `ApiSettings`, `ApiDeploymentSettings`, `ApiProcessSettings` | API runtime controls, infrastructure assertions, operational JSON contracts, preview credentials, review and promotion overrides |
| `phlo-dagster` | `DagsterSettings`, `DagsterProcessSettings`, `DagsterOidcTimingSettings`, `DagsterWebsocketSettings`, `WapSensorSettings`, `WapBackfillSettings` | Executor, identity, sensor, CLI and websocket settings |
| `phlo-dbt` | `DbtSettings`, `DbtDescriptionSettings`, `DbtTranslatorSettings` | Project configuration and compiled SQL presentation |
| `phlo-mcp` | `McpSettings`, `McpTraceSettings` | MCP server controls and legacy debug trace destination |
| `phlo-pandera` | `PanderaSettings` | Schema discovery paths |
| `phlo-openmetadata` | `OpenMetadataSettings`, `OpenMetadataProcessSettings` | Integration configuration and publish-event schema fallback |
| `phlo-sling` | `SlingSettings`, `SlingProcessSettings` | Replication configuration and object-store selection |

Consumers in Polaris, OpenTelemetry and lineage use shared core process fields
for project roots, project identity and the shared lineage database fallback.
Existing project-scoped `LineageSettings` remain distinct from the process-only
database resolver: the latter skips blank values, reads fresh, and never loads
dotenv files. Likewise, resolved core `Settings` include dotenv logging defaults,
while security and telemetry sometimes require the process override alone.
These are different source contracts, not a new precedence rule.

Each PHLO environment name has one declaring owner. Process views of
`PHLO_ENVIRONMENT`, `PHLO_PROJECT` and `PHLO_LOG_LEVEL` read the existing `Settings`
field metadata through `EnvSettingsSource`, without redeclaring fields or applying
resolved defaults. `LineageSettings` inherits the shared `LineageDatabaseSettings`
declaration. Observatory UI and storage inherit `ObservatoryDatabaseSettings`,
which does not add backend validation to the UI. `McpSettings` inherits the trace
field from `McpTraceSettings`. The generator lists inherited fields under their
declaring model once, with references from the consuming models. A regression
rejects duplicate supported PHLO environment names across the whole inventory.

### Parsing and execution boundaries

Process-only models are case-sensitive and do not load `.phlo/.env` files.
Accessors construct a fresh model, so mutations between requests remain visible.
The existing project-scoped settings caches are unchanged.

Booleans, integers, floats and comma-separated lists use meaningful types where
parsing cannot fail an unrelated operation. Historical token sets are preserved:
`PHLO_AUTH_STATIC_ENABLED=false` still selects the static provider because that
override historically meant any nonempty string. Identity-authority and
maintenance-execution controls accept only exact `1`. Contract refresh accepts
stripped `1`, `true` and `yes`, but not `on`.

Potentially invalid numbers have their own execution-local models. Pool capacity
is parsed only when creating the first PostgreSQL pool, not for SQLite or injected
connections. Dagster OIDC timings are ignored while identity is unconfigured.
Websocket timeout validation remains at middleware construction. Sensor intervals
remain import-time values, and backfill timeouts are read when the wait begins.
dbt SQL limits are parsed only for metadata or enabled SQL descriptions; invalid
limits warn once and fall back to 64000, without warning for disabled descriptions.
Integer parsing still uses Python `int`: `3.0` fails, while ` +3 ` succeeds.

Strings remain strings for secrets, file paths, provider names and JSON wire
contracts. Secret whitespace and explicit empty values cannot be normalised
globally: some consumers use blank to clear YAML values, others use blank to fall
back, and HMAC consumers require the original bytes. JSON token maps, environment
contracts, job maps and maintenance windows retain their existing validators.
Those validators decide whether invalid JSON falls back, raises a configuration
error, or returns an unavailable API response. Parsing them eagerly in a shared
model would change both the error boundary and unrelated command behaviour.

The generated reference's `none` default means an absent process override.
Descriptions record the effective default or contextual precedence. The keyed
accessor is retained only for string overrides where missing and empty differ;
typed fields are accessed directly. It is not a claim that all contextual parsing
or default resolution now happens centrally.

## Internal and test hooks

These are the only remaining literal `PHLO_*` read exceptions. They are not
deployment configuration and have no announced removal release.

| Hook | Owner and source | Existing behaviour |
| --- | --- | --- |
| `PHLO_TEST_LOCAL` | `phlo-testing`, `local_mode.py` | Local fixture mode; lowercase `1` or `true` enables. The CLI `--test-local` option sets this hook. |
| `PHLO_FIXTURE_DIR` | `phlo-testing`, `local_mode.py` | Fixture directory override; blank falls back to caller defaults. |
| `PHLO_RUN_BUNDLED_STACK_CONTRACT` | `phlo-testing`, `profile_harness.py` | Explicit opt-in to the bundled-stack contract harness. |
| `PHLO_KEEP_BUNDLED_STACK` | `phlo-testing`, `profile_harness.py` | Keeps the disposable harness stack for inspection. |
| `PHLO_TEST_MINIO_ACCESS_KEY` | `phlo-testing`, `profile_harness.py` | Harness credential override before local MinIO settings and fixture defaults. |
| `PHLO_TEST_MINIO_SECRET_KEY` | `phlo-testing`, `profile_harness.py` | Harness secret override before local MinIO settings and fixture defaults. |

`PHLO_NO_AUTO_DISCOVER` is a supported typed bootstrap control, not an exception.
`phlo init` sets it before plugin discovery. `PHLO_QUIET` is written by the CLI's
quiet option but has no Python production reader in this inventory; that write
does not establish a supported setting contract.

## Temporary compatibility controls

No remaining deployment assertion or opt-in feature has a source-declared
temporary lifetime. Single-replica, single-process and ref/tag assertions are
persistent infrastructure contracts, not temporary flags. The inventory does not
assign an invented removal release to them.

Two legacy controls already have a declared removal release. Ownership below is
the owning package/module, not a newly assigned person or team.

| Control | Owner | Removal release | Source evidence |
| --- | --- | --- | --- |
| `PHLO_REGULATED_MODE` | `phlo`, `security/mode.py` | `0.19.0` | Existing `deprecated_env_var` warning with `removal_version`; `PHLO_REGULATED` and explicit configuration take precedence. |
| `PHLO_MCP_TRACE_FILE` | `phlo-mcp`, `tracing.py` | `0.19.0` | Existing deprecation warning for the JSONL drain; canonical `OBSERVE_DRAINS` or `OBSERVE_HTTP_ENDPOINT` replaces it. |

`PHLO_SERVICE_SECRET` is a development-only compatibility input. Production
rejects it. The source declares no removal release, so this inventory does not
classify it as a temporary feature flag or invent a deadline.

## Dynamic reads and environment transport

The literal guard alone is not exhaustive. The migration inspected dynamic
helpers, imported constants, whole-environment copies and iteration separately.

| Reader | Classification and disposition |
| --- | --- |
| Authentication JWT `value(name)` and security secret-name selection | Supported core fields; JWT timing fields are typed when authentication providers are configured, as before. |
| Dagster OIDC integer helpers and imported `OIDC_REQUIRED_ENV` | Supported Dagster fields; numeric helpers replaced by `DagsterOidcTimingSettings`. |
| API promotion `_configured()` name loop | Supported API worktree, ref and location fields; environment-contract validation unchanged. |
| Preview `PHLO_V1_PREVIEW_TRINO_PASSWORD_{env}` | Closed domain: `prod` and `staging`. Both credentials are explicit API fields; no dynamic process read remains. |
| dbt `_bool_env` and `_int_env` | Replaced by `DbtDescriptionSettings` and `DbtTranslatorSettings`; invalid byte limits still warn and use 64000 only when read. |
| Registry and lineage database-key loops | Explicit process fields preserve nonempty fallback chains; third-party Dagster and PostgreSQL keys remain outside this issue. |
| Telemetry flag helper and supplied run mapping | Supported typed telemetry fields; supplied mappings exclude process fallback, including when a mapping is empty. |
| Auto-discovery imported constant | Replaced by `PluginDiscoverySettings`; unknown nonempty tokens still disable discovery and warn. |
| `application/observability.py::_service_env_value(key)` | Closed callers use non-PHLO provider URL, path and port keys. Shared PHLO public host and scheme are settings fields. |
| `helpers/connections.py::resolve_database(env_prefix=...)` | Caller-defined connection namespace, not a finite Phlo setting family. No production caller supplies a PHLO prefix. URL precedes DSN, then capability resolution. |
| `telemetry.py::_hidden_env(name)` | Internal transport hook; only `OBSERVE_DRAINS` is hidden and restored verbatim. |
| MinIO, Nessie, Polaris, PostgreSQL and Trino `security_readiness.py` | Closed lists of third-party credential references, none with PHLO prefix. |
| OpenTelemetry exporter and endpoint selectors | Construct only third-party `OTEL_*` keys. |
| `phlo-testing/profile_harness.py` dynamic key snapshots | Internal harness setup/restore; carries test controls and temporary environment updates, not additional supported settings. |
| `config/env.py`, service init/utils/ports, production preflight | Environment composition and validation. Arbitrary project keys, including nested `PHLO_SETTINGS__*` workflow values, are caller configuration rather than new fixed setting names. Their precedence is unchanged. |
| Native process manager, dbt subprocesses, API subprocesses and test harnesses | Environment forwarding. Copies preserve unknown third-party and user workflow keys rather than coercing them through a fixed Phlo model. |
| Sling connection export and summary iteration | Caller-defined JSON connection names (`PHLO_POSTGRES`, `PHLO_S3` and custom names). These are connection payloads, not supported fixed process-setting declarations. |

[`phlo_env_dynamic_reads_allowlist.json`](../../tests/tooling/phlo_env_dynamic_reads_allowlist.json)
pins unresolved keyed readers for review. The guard also rejects new PHLO-prefixed
f-string reads. Neither guard claims whole-environment dataflow analysis.
