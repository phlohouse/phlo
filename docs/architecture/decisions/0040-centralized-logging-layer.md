# ADR 0040: Centralized Logging Layer and Log Routing

## Status

**Proposed**

## Context

Phlo logs are currently emitted via a mix of stdlib logging, Dagster
`context.log`, and ad-hoc telemetry events. Observatory uses Pino in TypeScript.
We already ship logs to Loki via Alloy, but there is no unified logging API or
middle layer for routing logs to additional sinks via plugins. This makes
correlation inconsistent and forces each package to invent its own logging
setup.

ADR-0028 established correlation field standards, but it did not define a
central logging layer or a routing mechanism for pluggable sinks.

## Decision

Adopt a centralized logging layer for Python packages with a routing handler
that forwards structured log events to the hook bus. Use `structlog` on top of
stdlib `logging` as the backend to preserve compatibility with Dagster and
third-party libraries.

Key elements:

- Introduce `phlo.logging` API with `setup_logging()` and `get_logger()`.
- Define a normalized `LogEvent` (`event_type="log.record"`) and emit it via a
  `LogRouterHandler` into the HookBus.
- Keep JSON to stdout as the default sink for Alloy/Loki ingestion.
- Support optional file logging via `phlo_log_file_template` (default
  `.phlo/logs/{YMD}.log`, empty disables).
- Allow hook plugins to subscribe to `log.record` for external sinks without
  changing core logging code.
- Align log schema fields with ADR-0028 correlation keys.

## Implementation

- Add `LogEvent` to `phlo.hooks.events` and `LogRouterHandler` in
  `phlo.logging.router`.
- Create `phlo.logging` module with `setup_logging()`, `get_logger()`, and
  context binding helpers.
- Update CLI/service entrypoints to call `setup_logging()` early.
- Migrate core packages to use `get_logger()` and standard fields.
- Document hook-based sinks for `log.record`.

## Consequences

### Positive

- Consistent logging API across packages.
- Logs always flow through a middle layer, enabling multiple sinks.
- Compatible with existing Loki/Alloy setup and Dagster logging.
- External sinks can be added via plugins without changing core code.

### Negative

- Adds a new dependency (`structlog`) and more configuration surface area.
- Requires incremental migration of existing package logging calls.

## Verification

- Unit tests verifying `LogRecord` -> `LogEvent` conversion.
- Integration test that logs appear in Loki and (optionally) a stub sink.
- Manual test in Dagster run to confirm correlation fields are attached.
