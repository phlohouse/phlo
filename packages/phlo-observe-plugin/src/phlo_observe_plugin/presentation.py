"""Phlo's presentation rules for the observe-core ``PrettyRenderer``.

observe-core owns the rendering mechanics — layout, status vocabulary,
field formatting, terminal behaviour. This module owns what each canonical
event Phlo emits *means* to a human: its display label, the fields worth
showing, and whether it is routine (``primary``), detail-level
(``secondary``), or suppressed (``hidden``). Unregistered events still
render via the renderer's generic fallback.

``PHLO_PRESENTATION`` covers the events this plugin emits through
``hooks_plugin``/``dagster_ext`` plus the ones the phlo-observe SDK
integrations emit directly (dlt, dbt, Dagster, Iceberg, Trino). It is
imported lazily — importing requires a ``PrettyRenderer``-capable
observe-core, which is guaranteed only where the SDK is installed.
"""

from __future__ import annotations

import contextlib
import dataclasses
import importlib
import json
import sys
from collections.abc import Mapping, Sequence
from typing import IO, Any

# observe_core is an optional runtime dep — this module is imported lazily
# by design (telemetry attaches PrettyDrain only where the SDK is present),
# so it resolves via importlib like the rest of the optional SDK surface.
_observe_core = importlib.import_module("observe_core")
ContextField = _observe_core.ContextField
EventPresentation = _observe_core.EventPresentation
Field = _observe_core.Field
PrettyRenderer = _observe_core.PrettyRenderer
Visibility = _observe_core.Visibility
format_value = _observe_core.format_value

PHLO_CONTEXT: list[Any] = [
    # Run-level values that identify the group an event belongs to. Kept to
    # values uniform within a run: ``correlation.pipeline`` is set only by
    # dlt-scope events and ``branch``/``asset`` are per-event fields — a
    # sparse context value would fragment the run into alternating groups.
    ContextField("correlation.root_run_id", label="Run", format="identifier"),
    ContextField("correlation.partition_key", label="Partition"),
    # ``correlation.trace_id`` is deliberately absent: a single run spans
    # several trace scopes (the step scope, materialize's own scope,
    # scopeless sensor emits), so grouping on it would fragment the run.
    # It is a per-event secondary field on the rules below instead.
]


def _anonymous_job_name(value: Any) -> bool:
    """True for Dagster's implicit job names — executor plumbing, not a
    user-meaningful job identity (``__anonymous_asset_job__``...)."""
    return str(value).startswith("__anonymous")


PHLO_PRESENTATION: dict[str, Any] = {
    # -- Run/step boundaries -------------------------------------------------
    "pipeline.run": EventPresentation(
        label="Run",
        fields=[
            Field("attributes.job_name", label="Job", suppress=_anonymous_job_name),
            Field("attributes.dagster_status", label="Status"),
            Field("attributes.attempt", label="Attempt"),
            Field("correlation.branch", label="Branch", visibility="secondary"),
            Field("attributes.asset_keys", label="Assets", visibility="secondary"),
            Field(
                "attributes.phlo_run_id",
                label="Phlo run",
                format="identifier",
                visibility="secondary",
            ),
            Field("source.producer", label="Producer", visibility="secondary"),
            Field(
                "correlation.trace_id", label="Trace", format="identifier", visibility="secondary"
            ),
        ],
    ),
    "pipeline.step": EventPresentation(
        label="Step",
        fields=[
            Field("correlation.asset_key", label="Asset"),
            Field("attributes.dagster_op", label="Op"),
            Field("correlation.job_id", label="Job", visibility="secondary"),
            Field("attributes.attempt", label="Attempt", visibility="secondary"),
            Field("source.producer", label="Producer", visibility="secondary"),
            Field(
                "correlation.trace_id", label="Trace", format="identifier", visibility="secondary"
            ),
        ],
    ),
    "asset.materialize": EventPresentation(
        label="Materialize",
        fields=[
            Field("correlation.asset_key", label="Asset"),
            Field("attributes.rows_out", label="Rows", format="integer"),
            # The emit wraps a bookkeeping scope, not real work — its
            # duration_ms is structurally ~0, so the field is not selected.
            # The measured duration lives on pipeline.step.
            Field("duration_ms", visibility="hidden"),
            Field(
                "attributes.bytes_written", label="Written", format="bytes", visibility="secondary"
            ),
            Field("attributes.dagster_op", label="Op", visibility="secondary"),
            Field("source.producer", label="Producer", visibility="secondary"),
            Field(
                "correlation.trace_id", label="Trace", format="identifier", visibility="secondary"
            ),
        ],
    ),
    # -- Ingestion / transform -------------------------------------------------
    "ingestion.extract": EventPresentation(
        label="Extract",
        fields=[
            Field("attributes.table_name", label="Table"),
            Field("attributes.group_name", label="Group"),
            Field("attributes.catalog_system", label="Catalog", visibility="secondary"),
            Field("attributes.status", label="Status", visibility="secondary"),
            Field("attributes.attempt", label="Attempt", visibility="secondary"),
            Field("attributes.phlo_event_type", label="Hook", visibility="secondary"),
            Field(
                "attributes.phlo_event_id",
                label="Event",
                format="identifier",
                visibility="secondary",
            ),
            Field(
                "correlation.trace_id", label="Trace", format="identifier", visibility="secondary"
            ),
        ],
    ),
    "ingestion.load": EventPresentation(
        label="Load",
        fields=[
            Field("attributes.table_name", label="Table"),
            Field("attributes.rows_processed", label="Rows", format="integer"),
            Field("attributes.rows_inserted", label="Rows", format="integer"),
            Field("attributes.group_name", label="Group"),
            Field("attributes.catalog_system", label="Catalog", visibility="secondary"),
            Field("attributes.status", label="Status", visibility="secondary"),
            Field("attributes.attempt", label="Attempt", visibility="secondary"),
            Field("attributes.phlo_event_type", label="Hook", visibility="secondary"),
            Field(
                "attributes.phlo_event_id",
                label="Event",
                format="identifier",
                visibility="secondary",
            ),
            Field(
                "correlation.trace_id", label="Trace", format="identifier", visibility="secondary"
            ),
        ],
    ),
    "transform.execute": EventPresentation(
        label="Transform",
        fields=[
            Field("attributes.tool", label="Tool"),
            Field("attributes.target", label="Target"),
            Field("attributes.model_names", label="Models"),
            Field("attributes.status", label="Status", visibility="secondary"),
            Field(
                "attributes.phlo_event_id",
                label="Event",
                format="identifier",
                visibility="secondary",
            ),
            Field(
                "correlation.trace_id", label="Trace", format="identifier", visibility="secondary"
            ),
        ],
    ),
    # -- Quality ----------------------------------------------------------------
    "quality.check": EventPresentation(
        label="Check",
        fields=[
            Field("attributes.check_name", label="Check"),
            Field("correlation.asset_key", label="Asset"),
            Field("attributes.check_type", label="Type"),
            Field("attributes.dagster_op", label="Op", visibility="secondary"),
            Field("attributes.attempt", label="Attempt", visibility="secondary"),
            Field("attributes.phlo_event_type", label="Hook", visibility="secondary"),
            Field(
                "attributes.phlo_event_id",
                label="Event",
                format="identifier",
                visibility="secondary",
            ),
            Field(
                "correlation.trace_id", label="Trace", format="identifier", visibility="secondary"
            ),
        ],
    ),
    "quality.validate": EventPresentation(
        label="Validate",
        fields=[
            Field("attributes.check_name", label="Check"),
            Field("correlation.asset_key", label="Asset"),
            Field("attributes.check_type", label="Type", visibility="secondary"),
        ],
    ),
    # -- Write-audit-publish ------------------------------------------------------
    "wap.branch.create": EventPresentation(
        label="WAP branch",
        fields=[
            Field("correlation.branch", label="Branch"),
            Field("attributes.strategy", label="Strategy"),
            Field("attributes.catalog_system", label="Catalog"),
            Field("attributes.base_branch", label="Base", visibility="secondary"),
            Field("attributes.project_id", label="Project", visibility="secondary"),
            Field(
                "correlation.trace_id", label="Trace", format="identifier", visibility="secondary"
            ),
        ],
    ),
    "wap.validate": EventPresentation(
        label="WAP validate",
        fields=[
            Field("attributes.decision", label="Decision"),
            Field("attributes.check_name", label="Check"),
            Field("attributes.attempt", label="Attempt", visibility="secondary"),
            Field("attributes.phlo_event_type", label="Hook", visibility="secondary"),
            Field(
                "attributes.phlo_event_id",
                label="Event",
                format="identifier",
                visibility="secondary",
            ),
            Field(
                "correlation.trace_id", label="Trace", format="identifier", visibility="secondary"
            ),
        ],
    ),
    "wap.promote": EventPresentation(
        label="WAP promote",
        fields=[
            Field("correlation.branch", label="Branch"),
            Field("attributes.catalog_ref", label="Target"),
            Field("attributes.merge_outcome", label="Merge"),
            Field("attributes.source_hash", label="From", format="identifier"),
            Field("attributes.target_hash", label="To", format="identifier"),
            Field("attributes.catalog_system", label="Catalog"),
            Field("attributes.operation", label="Op", visibility="secondary"),
            Field("attributes.attempt", label="Attempt", visibility="secondary"),
            Field("attributes.phlo_event_type", label="Hook", visibility="secondary"),
            Field(
                "attributes.phlo_event_id",
                label="Event",
                format="identifier",
                visibility="secondary",
            ),
            Field(
                "attributes.quality_decision_id",
                label="Decision",
                format="identifier",
                visibility="secondary",
            ),
            Field(
                "correlation.trace_id", label="Trace", format="identifier", visibility="secondary"
            ),
        ],
    ),
    "wap.reject": EventPresentation(
        label="WAP reject",
        fields=[
            Field("correlation.branch", label="Branch"),
            Field("attributes.merge_outcome", label="Merge"),
            Field("attributes.quality_decision_id", label="Decision", format="identifier"),
            Field("attributes.catalog_system", label="Catalog", visibility="secondary"),
            Field(
                "attributes.phlo_event_id",
                label="Event",
                format="identifier",
                visibility="secondary",
            ),
            Field(
                "correlation.trace_id", label="Trace", format="identifier", visibility="secondary"
            ),
        ],
    ),
    "wap.cleanup": EventPresentation(
        label="WAP cleanup",
        fields=[
            Field("correlation.branch", label="Branch"),
            Field("attributes.operation", label="Op", visibility="secondary"),
            Field("attributes.catalog_system", label="Catalog", visibility="secondary"),
            Field(
                "attributes.phlo_event_id",
                label="Event",
                format="identifier",
                visibility="secondary",
            ),
            Field(
                "correlation.trace_id", label="Trace", format="identifier", visibility="secondary"
            ),
        ],
    ),
    # -- Sources: dlt / dbt --------------------------------------------------------
    "dlt.pipeline.run": EventPresentation(
        label="Stage",
        fields=[
            Field("attributes.pipeline_name", label="Pipeline"),
            Field("attributes.destination", label="Destination"),
            Field("attributes.dataset_name", label="Dataset"),
            Field("attributes.rows_loaded", label="Rows", format="integer"),
            Field("attributes.tables", label="Tables", visibility="secondary"),
            Field("attributes.load_id", label="Load", visibility="secondary"),
            Field(
                "attributes.started_at", label="Started", format="timestamp", visibility="secondary"
            ),
            Field(
                "attributes.finished_at",
                label="Finished",
                format="timestamp",
                visibility="secondary",
            ),
            Field("source.producer", label="Producer", visibility="secondary"),
            Field(
                "correlation.trace_id", label="Trace", format="identifier", visibility="secondary"
            ),
        ],
    ),
    "dbt.invocation": EventPresentation(
        label="dbt run",
        fields=[
            Field("attributes.dbt_version", label="dbt"),
            Field("attributes.results_count", label="Results", format="integer"),
            Field("attributes.elapsed_time_s", label="Elapsed", format="float"),
        ],
    ),
    "dbt.model.execute": EventPresentation(
        label="dbt model",
        fields=[
            Field("attributes.name", label="Model"),
            Field("attributes.status", label="Status"),
            Field("attributes.relation_name", label="Relation", visibility="secondary"),
            Field(
                "attributes.rows_affected", label="Rows", format="integer", visibility="secondary"
            ),
        ],
    ),
    "dbt.test.execute": EventPresentation(
        label="dbt test",
        fields=[
            Field("attributes.name", label="Test"),
            Field("attributes.status", label="Status"),
            Field("attributes.failures", label="Failures", format="integer"),
        ],
    ),
    # -- Storage / query ------------------------------------------------------------
    "iceberg.commit": EventPresentation(
        label="Commit",
        fields=[
            Field("correlation.table", label="Table"),
            Field("attributes.operation", label="Op"),
            Field("attributes.rows_added", label="Rows +", format="integer"),
            Field("attributes.files_added", label="Files +", format="integer"),
            Field(
                "attributes.rows_removed", label="Rows -", format="integer", visibility="secondary"
            ),
            Field(
                "attributes.files_removed",
                label="Files -",
                format="integer",
                visibility="secondary",
            ),
            Field("attributes.catalog", label="Catalog", visibility="secondary"),
            Field(
                "attributes.snapshot_id_after",
                label="Snapshot",
                format="identifier",
                visibility="secondary",
            ),
            Field(
                "attributes.commit_hash",
                label="Commit",
                format="identifier",
                visibility="secondary",
            ),
            Field("source.producer", label="Producer", visibility="secondary"),
            Field(
                "correlation.trace_id", label="Trace", format="identifier", visibility="secondary"
            ),
        ],
    ),
    "iceberg.snapshot.create": EventPresentation(
        label="Snapshot",
        visibility="secondary",
        fields=[Field("correlation.branch", label="Branch")],
    ),
    "nessie.commit": EventPresentation(
        label="Nessie commit",
        visibility="secondary",
        fields=[
            Field("correlation.branch", label="Branch"),
            Field("attributes.merged_branch", label="Merged"),
            Field("source.producer", label="Producer"),
            Field(
                "correlation.trace_id", label="Trace", format="identifier", visibility="secondary"
            ),
        ],
    ),
    "nessie.branch.create": EventPresentation(
        label="Nessie branch",
        visibility="secondary",
        fields=[
            Field("correlation.branch", label="Branch"),
            Field("attributes.base_branch", label="Base"),
            Field("source.producer", label="Producer"),
            Field(
                "correlation.trace_id", label="Trace", format="identifier", visibility="secondary"
            ),
        ],
    ),
    "trino.query": EventPresentation(
        label="Query",
        visibility="secondary",
        fields=[
            Field("attributes.query_class", label="Class"),
            Field("attributes.query_hash", label="Hash", format="identifier"),
            Field("source.producer", label="Producer", visibility="secondary"),
        ],
    ),
    # -- Phlo lifecycle ----------------------------------------------------------------
    "phlo.publish": EventPresentation(
        label="Publish",
        # Successful publish-to-observatory is plumbing on a healthy run;
        # failures still escalate to primary.
        visibility="secondary",
        fields=[
            Field("attributes.target_system", label="Target"),
            Field("attributes.tables", label="Tables", visibility="secondary"),
            Field("attributes.status", label="Status", visibility="secondary"),
            Field(
                "attributes.phlo_event_id",
                label="Event",
                format="identifier",
                visibility="secondary",
            ),
            Field(
                "correlation.trace_id", label="Trace", format="identifier", visibility="secondary"
            ),
        ],
    ),
    "phlo.observation": EventPresentation(
        label="Observation",
        # Evidence receipts/stage bookkeeping — the canonical stream keeps
        # them for the observer; humans only see escalated failures.
        visibility="hidden",
        fields=[
            Field("attributes.observation_type", label="Type"),
            Field("attributes.stage_id", label="Stage"),
            Field(
                "attributes.phlo_event_id",
                label="Event",
                format="identifier",
                visibility="secondary",
            ),
            Field(
                "correlation.trace_id", label="Trace", format="identifier", visibility="secondary"
            ),
        ],
    ),
    "phlo.lineage": EventPresentation(
        label="Lineage",
        # Edge bookkeeping for the graph — verbose-only, since the edges
        # occasionally explain where a table came from.
        visibility="secondary",
        fields=[
            Field("attributes.asset_keys", label="Assets"),
            Field("attributes.edges", label="Edges"),
            Field(
                "attributes.phlo_event_id",
                label="Event",
                format="identifier",
                visibility="secondary",
            ),
            Field(
                "correlation.trace_id", label="Trace", format="identifier", visibility="secondary"
            ),
        ],
    ),
    "phlo.service": EventPresentation(
        label="Service",
        # Service lifecycle plumbing — verbose-only on a healthy run.
        visibility="secondary",
        fields=[
            Field("attributes.service_name", label="Service"),
            Field("attributes.phase", label="Phase"),
            Field("attributes.status", label="Status", visibility="secondary"),
        ],
    ),
    "phlo.schema_migration": EventPresentation(
        label="Schema migration",
        fields=[
            Field("attributes.table_name", label="Table"),
            Field("attributes.classification", label="Class"),
            Field("attributes.change_count", label="Changes", format="integer"),
        ],
    ),
    "phlo.data_migration": EventPresentation(
        label="Data migration",
        fields=[
            Field("attributes.migration_name", label="Migration"),
            Field("attributes.destination_table", label="Table"),
            Field("attributes.rows_read", label="Read", format="integer"),
            Field("attributes.rows_written", label="Written", format="integer"),
        ],
    ),
    # -- Application / observer-internal ------------------------------------------------
    "application.start": EventPresentation(label="Start"),
    "application.stop": EventPresentation(label="Stop"),
    "application.log": EventPresentation(
        label="Log",
        visibility="secondary",
        fields=[
            Field("attributes.message", label="Message"),
            Field("attributes.logger", label="Logger", visibility="secondary"),
            Field("attributes.level", label="Level", visibility="secondary"),
        ],
    ),
    # Observer-internal pipeline stages — bookkeeping for the service, not
    # run output a human follows. Escalated failures still surface.
    "observer.ingest": EventPresentation(label="Ingest", visibility="hidden"),
    "observer.normalize": EventPresentation(label="Normalize", visibility="hidden"),
    "observer.correlate": EventPresentation(label="Correlate", visibility="hidden"),
    "observer.export": EventPresentation(label="Export", visibility="hidden"),
}


# Events whose default field count makes a single line unreadable: the first
# field becomes the subject on the status line, the rest stack beneath it as
# aligned ``Label  value`` rows. The layout change happens in
# ``pretty_renderer`` — ``PHLO_PRESENTATION`` itself stays declarative, so
# consumers rendering the map directly still get the standard layout.
_STACKED_EVENTS = frozenset(
    {
        "ingestion.load",
        "iceberg.commit",
        "dlt.pipeline.run",
        "wap.promote",
        "wap.reject",
    }
)


def _resolve_field(data: Mapping[str, Any], path: str) -> Any:
    """Walk a dot-delimited path over nested mappings; ``None`` on absence."""
    node: Any = data
    for segment in path.split("."):
        if not isinstance(node, Mapping) or segment not in node:
            return None
        node = node[segment]
    return node


def _stacked_formatter(fields: tuple[Any, ...], verbose: bool, ellipsis: str) -> Any:
    """A mode-aware ``formatter`` that stacks an event's declared fields.

    Custom formatters own the event's content — the renderer keeps the
    status glyph, indentation, and error block, but does not tier fields
    for them. The closure therefore applies the rule's own visibility and
    ``suppress`` predicates against the mode captured at construction, so
    secondary fields still join the block in verbose and only in verbose.
    """

    def _format(data: Mapping[str, Any]) -> list[str]:
        rows: list[tuple[str, str]] = []
        for fld in fields:
            if fld.visibility == Visibility.HIDDEN:
                continue
            if fld.visibility == Visibility.SECONDARY and not verbose:
                continue
            value = _resolve_field(data, fld.path)
            if value is None:
                continue
            if fld.suppress is not None and fld.suppress(value):
                continue
            rendered = format_value(value, fld.format, head=fld.head, ellipsis=ellipsis)
            rows.append((fld.display_label, str(rendered)))
        if not rows:
            return []
        # The first field is the subject — it rides the status line bare;
        # remaining fields stack as label-aligned rows beneath it.
        subject, rest = rows[0][1], rows[1:]
        width = max(len(label) for label, _ in rest) if rest else 0
        return [subject, *(f"{label:<{width}}  {value}" for label, value in rest)]

    return _format


def _stacked_rules(rules: Mapping[str, Any], mode: str, symbols: str) -> dict[str, Any]:
    """``rules`` with stacked-layout formatters attached to the dense events."""
    ellipsis = "..." if symbols == "ascii" else "…"
    verbose = mode == "verbose"
    stacked = {}
    for name in _STACKED_EVENTS:
        rule = rules.get(name)
        if rule is not None:
            stacked[name] = dataclasses.replace(
                rule, formatter=_stacked_formatter(rule.fields, verbose, ellipsis)
            )
    return {**rules, **stacked}


def pretty_renderer(**kwargs: Any) -> Any:
    """A ``PrettyRenderer`` pre-loaded with Phlo's presentation rules.

    ``timestamps`` defaults on: Phlo log readers want each event's
    ``observed_at`` as a leading time column. Callers may pass
    ``timestamps=False`` to suppress it. Dense events render their fields
    as a stacked block; field-heavy single lines are reserved for the
    canonical stream.
    """
    kwargs.setdefault("rules", PHLO_PRESENTATION)
    kwargs.setdefault("context", PHLO_CONTEXT)
    kwargs.setdefault("timestamps", True)
    if kwargs["rules"] is PHLO_PRESENTATION:
        kwargs["rules"] = _stacked_rules(
            PHLO_PRESENTATION,
            mode=kwargs.get("mode", "pretty"),
            symbols=kwargs.get("symbols", "auto"),
        )
    return PrettyRenderer(**kwargs)


def render_events(events: Sequence[Any], **kwargs: Any) -> str:
    """Render a sequence of canonical events/dicts with Phlo's rules."""
    return pretty_renderer(**kwargs).render_many(events)


def render_jsonl(path: str, **kwargs: Any) -> str:
    """Render a JSONL drain file (one canonical event per line).

    Blank lines are skipped; malformed lines render via the renderer's
    unrenderable-event fallback rather than aborting the render.
    """
    events: list[Any] = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except ValueError:
                events.append(line)
    return render_events(events, **kwargs)


class PrettyDrain:
    """Human-readable drain: renders canonical events via ``PrettyRenderer``.

    Implements the observe-core ``Drain`` protocol so it can sit beside
    ``console``/``jsonl``/``http`` drains on the runtime's drain list.
    Incremental ``emit_batch`` calls go through ``write_event`` so context
    headers track across batches instead of repeating per batch.
    """

    name = "pretty"
    is_remote = False

    def __init__(
        self,
        *,
        stream: IO[str] | None = None,
        mode: str = "pretty",
        color: str = "auto",
        symbols: str = "auto",
        timestamps: bool = True,
    ) -> None:
        self._stream = stream if stream is not None else sys.stderr
        self._renderer = pretty_renderer(
            mode=mode,
            color=color,
            symbols=symbols,
            timestamps=timestamps,
            stream=self._stream,
        )

    def _write(self, data: Any) -> None:
        """Render one event; a closed stream ends output quietly.

        The drain may hold ``sys.stderr`` past its usable life (interpreter
        or test-runner teardown replaces and closes it before the runtime's
        shutdown flush runs) — writing then raises ``ValueError``. There is
        nowhere else for human output to go, so the event is dropped.
        """
        try:
            self._renderer.write_event(data)
        except (ValueError, OSError):
            pass

    def emit_batch(self, events: Sequence[Any]) -> None:
        """Render each event, sharing context state across batches."""
        for event in events:
            self._write(event)

    def emit_raw(self, payloads: Sequence[bytes]) -> None:
        """Render pre-serialized canonical payloads (spool replay)."""
        for payload in payloads:
            try:
                data = json.loads(payload)
            except (TypeError, ValueError):
                continue
            self._write(data)

    def flush(self) -> None:
        """Flush the underlying stream; tolerate it already being closed."""
        with contextlib.suppress(ValueError, OSError):
            self._stream.flush()

    def close(self) -> None:
        """Flush; the stream itself is left open (it may be stderr/stdout)."""
        self.flush()
