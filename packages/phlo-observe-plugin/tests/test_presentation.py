"""Tests for Phlo's observe-core pretty-rendering presentation rules.

These cover the mapping only — generic renderer mechanics live in
observe-core's own suite. The golden sequence mirrors the canonical history
a WAP-launched run leaves behind.
"""

from __future__ import annotations

import io
import json

import pytest

pytest.importorskip("observe_core", reason="phlo-observe SDK not installed")

from observe_core.drains.base import CanonicalEvent  # noqa: E402
from observe_core.models import Delivery  # noqa: E402

from phlo_observe_plugin.presentation import (  # noqa: E402
    PHLO_CONTEXT,
    PHLO_PRESENTATION,
    PrettyDrain,
    pretty_renderer,
    render_jsonl,
)

RUN = "ca98f384-d3cc-4733-8603-6ac761bac073"
BRANCH = "pipeline-run-abc123"


def _wap_history() -> list[dict]:
    """A minimal canonical history in the shape phlo actually emits."""
    corr = {"run_id": RUN}
    return [
        {
            "event": "wap.branch.create",
            "outcome": "success",
            "severity": "info",
            "observed_at": "2026-09-18T10:03:28.871Z",
            "correlation": {**corr, "branch": BRANCH},
            "attributes": {
                "branch": BRANCH,
                "strategy": "branch",
                "catalog_system": "nessie",
                "project_id": "demo",
            },
        },
        {
            "event": "ingestion.extract",
            "outcome": "success",
            "severity": "info",
            "observed_at": "2026-09-18T10:03:30.543Z",
            "correlation": {**corr, "branch": BRANCH, "table": "bronze.users"},
            "attributes": {
                "table_name": "bronze.users",
                "branch_name": BRANCH,
                "group_name": "bronze",
                "catalog_system": "nessie",
                "phlo_event_type": "ingestion.start",
            },
        },
        {
            "event": "ingestion.load",
            "outcome": "success",
            "severity": "info",
            "observed_at": "2026-09-18T10:03:30.544Z",
            "correlation": {**corr, "branch": BRANCH, "table": "bronze.users"},
            "attributes": {
                "table_name": "bronze.users",
                "branch_name": BRANCH,
                "group_name": "bronze",
                "catalog_system": "nessie",
                "rows_processed": 12481,
                "phlo_event_type": "ingestion.end",
            },
        },
        {
            "event": "wap.validate",
            "outcome": "success",
            "severity": "info",
            "observed_at": "2026-09-18T10:03:31.300Z",
            "correlation": corr,
            "attributes": {"check_name": "wap.aggregate", "decision": "passed"},
        },
        {
            "event": "wap.promote",
            "outcome": "success",
            "severity": "info",
            "observed_at": "2026-09-18T10:03:31.367Z",
            "correlation": {**corr, "branch": BRANCH},
            "attributes": {
                "catalog_ref": "main",
                "merge_outcome": "promoted",
                "catalog_system": "nessie",
                "source_hash": "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4",
                "target_hash": "f6e5d4c3b2a1f6e5d4c3b2a1f6e5d4c3",
            },
        },
        {
            "event": "pipeline.run",
            "outcome": "success",
            "severity": "info",
            "observed_at": "2026-09-18T10:03:31.384Z",
            "correlation": {**corr, "job_id": "ingest_job", "branch": BRANCH},
            "attributes": {"job_name": "ingest_job", "dagster_status": "SUCCESS"},
            "duration_ms": 8466.3,
        },
    ]


def test_presentation_covers_phlo_event_vocabulary() -> None:
    """Every canonical name the plugin emits has a presentation rule."""
    for name in (
        "pipeline.run",
        "pipeline.step",
        "asset.materialize",
        "ingestion.extract",
        "ingestion.load",
        "transform.execute",
        "quality.check",
        "wap.branch.create",
        "wap.validate",
        "wap.promote",
        "wap.reject",
        "wap.cleanup",
        "dlt.pipeline.run",
        "dbt.invocation",
        "dbt.model.execute",
        "dbt.test.execute",
        "iceberg.commit",
        "trino.query",
        "phlo.publish",
        "phlo.observation",
        "phlo.lineage",
        "phlo.service",
        "phlo.schema_migration",
        "phlo.data_migration",
    ):
        assert name in PHLO_PRESENTATION, f"{name} missing a presentation rule"


def test_wap_run_golden() -> None:
    """Default output carries the operational milestones; field-dense
    events stack rather than running to a very long line."""
    renderer = pretty_renderer(color="never", symbols="unicode", stream=io.StringIO())
    assert renderer.render_many(_wap_history()) == (
        f"── Run: {RUN[:16]}…\n"
        f"10:03:28.871 ✓ WAP branch  Branch: {BRANCH}  Strategy: branch  Catalog: nessie\n"
        "10:03:30.543 ✓ Extract  Table: bronze.users  Group: bronze\n"
        "10:03:30.544 ✓ Load  bronze.users\n"
        "    Rows   12,481\n"
        "    Group  bronze\n"
        "10:03:31.300 ✓ WAP validate  Decision: passed  Check: wap.aggregate\n"
        f"10:03:31.367 ✓ WAP promote  {BRANCH}\n"
        "    Target   main\n"
        "    Merge    promoted\n"
        "    From     a1b2c3d4e5f6a1b2…\n"
        "    To       f6e5d4c3b2a1f6e5…\n"
        "    Catalog  nessie\n"
        f"10:03:31.384 ✓ Run  Job: ingest_job  Status: SUCCESS  Branch: {BRANCH}  8.47s"
    )


def test_wap_run_golden_verbose() -> None:
    """Verbose keeps the secondary detail: extra fields join the stacked
    blocks, secondary events indent."""
    out = pretty_renderer(mode="verbose", color="never", stream=io.StringIO()).render_many(
        _wap_history()
    )
    # Extract is a primary operational event, not indented.
    assert "10:03:30.543 ✓ Extract  Table: bronze.users  Group: bronze" in out
    # Secondary fields join the stacked blocks only in verbose.
    assert "    Catalog  nessie" in out
    assert "    Hook     ingestion.end" in out


def test_default_hides_internal_bookkeeping() -> None:
    """Evidence receipts and observer stages are not run output — they
    stay in the canonical stream but never render; lineage edges are
    diagnostic detail kept for verbose."""
    internal = [
        {"event": name, "outcome": "success", "severity": "info", "correlation": {"run_id": RUN}}
        for name in (
            "phlo.observation",
            "phlo.lineage",
            "observer.ingest",
            "observer.normalize",
            "observer.correlate",
            "observer.export",
        )
    ]
    assert pretty_renderer(color="never", stream=io.StringIO()).render_many(internal) == ""
    verbose = pretty_renderer(mode="verbose", color="never", stream=io.StringIO()).render_many(
        internal
    )
    assert "Lineage" in verbose
    for hidden in ("Observation", "Ingest", "Normalize", "Correlate", "Export"):
        assert hidden not in verbose


def test_internal_failure_still_surfaces() -> None:
    """A hidden bookkeeping event that fails escalates to primary."""
    event = {
        "event": "phlo.observation",
        "outcome": "failure",
        "severity": "error",
        "correlation": {"run_id": RUN},
        "error": {"message": "promotion evidence incomplete"},
    }
    out = pretty_renderer(color="never", stream=io.StringIO()).render(event)
    assert out.startswith("✕ Observation")


def test_secondary_events_visible_in_verbose() -> None:
    """Plumbing-level events show only in verbose, indented."""
    events = [
        {
            "event": name,
            "outcome": "success",
            "severity": "info",
            "correlation": {"run_id": RUN},
        }
        for name in (
            "nessie.branch.create",
            "nessie.commit",
            "phlo.publish",
            "phlo.service",
            "phlo.lineage",
            "trino.query",
        )
    ]
    pretty = pretty_renderer(color="never", stream=io.StringIO())
    assert pretty.render_many(events) == ""
    out = pretty_renderer(mode="verbose", color="never", stream=io.StringIO()).render_many(events)
    for label in ("Nessie branch", "Nessie commit", "Publish", "Service", "Lineage", "Query"):
        assert f"  ✓ {label}" in out, label


def test_stacked_layout_replaces_long_lines() -> None:
    """Field-dense events render the subject on the status line and the
    remaining fields as aligned rows — in both modes."""
    event = {
        "event": "iceberg.commit",
        "outcome": "success",
        "severity": "info",
        "correlation": {"run_id": RUN, "table": "bronze.users"},
        "attributes": {
            "operation": "append",
            "rows_added": 12481,
            "files_added": 3,
            "snapshot_id_after": 8675309123456789,
            "commit_hash": "9f8e7d6c5b4a3f2e1d0c9b8a7f6e5d4c",
        },
    }
    default = pretty_renderer(color="never", symbols="unicode", stream=io.StringIO()).render(event)
    assert "✓ Iceberg commit  bronze.users" in default
    assert "    Op       append" in default
    assert "    Rows +   12,481" in default
    # Secondary fields stay out of the default block.
    assert "Snapshot" not in default
    verbose = pretty_renderer(
        mode="verbose", color="never", symbols="unicode", stream=io.StringIO()
    ).render(event)
    assert "    Snapshot  8675309123456789" in verbose
    assert "    Commit    9f8e7d6c5b4a3f2e…" in verbose


def test_presentation_map_stays_declarative() -> None:
    """``PHLO_PRESENTATION`` carries no formatters — the stacked layout is
    attached by ``pretty_renderer``, so the mapping stays pure data."""
    for name, rule in PHLO_PRESENTATION.items():
        assert rule.formatter is None, name


def test_anonymous_job_name_suppressed() -> None:
    """Dagster's implicit job name is executor plumbing, not a Job."""
    event = {
        "event": "pipeline.run",
        "outcome": "success",
        "correlation": {"run_id": RUN},
        "attributes": {"job_name": "__anonymous_asset_job__", "dagster_status": "SUCCESS"},
    }
    out = pretty_renderer(color="never", stream=io.StringIO()).render(event)
    assert "Job:" not in out
    assert "__anonymous" not in out
    assert "Status: SUCCESS" in out


def test_named_job_still_shown() -> None:
    event = {
        "event": "pipeline.run",
        "outcome": "success",
        "correlation": {"run_id": RUN},
        "attributes": {"job_name": "ingest_job", "dagster_status": "SUCCESS"},
    }
    assert "Job: ingest_job" in pretty_renderer(color="never", stream=io.StringIO()).render(event)


def test_materialize_bookkeeping_duration_not_shown() -> None:
    """The materialize emit wraps a bookkeeping scope: its ~0ms duration
    is not selected for display (the real time lives on pipeline.step)."""
    event = {
        "event": "asset.materialize",
        "outcome": "success",
        "duration_ms": 0,
        "correlation": {"run_id": RUN, "asset_key": "bronze.users"},
        "attributes": {"rows_out": 12481},
    }
    out = pretty_renderer(color="never", stream=io.StringIO()).render(event)
    assert "0ms" not in out
    assert "Rows: 12,481" in out


def test_failure_escalation_and_error_block() -> None:
    event = {
        "event": "wap.promote",
        "outcome": "failure",
        "severity": "error",
        "correlation": {"run_id": RUN},
        "error": {"message": "merge conflict", "why": "schema drift on branch"},
    }
    out = pretty_renderer(color="never", stream=io.StringIO()).render(event)
    assert out.startswith("✕ WAP promote")
    assert "    merge conflict" in out
    assert "    schema drift on branch" in out


def test_context_fields_resolve() -> None:
    paths = {cf.path for cf in PHLO_CONTEXT}
    assert "correlation.run_id" in paths


def test_drain_protocol_surface() -> None:
    """PrettyDrain satisfies the observe-core Drain protocol shape."""
    stream = io.StringIO()
    drain = PrettyDrain(stream=stream, color="never", symbols="unicode")
    assert drain.name == "pretty"
    assert drain.is_remote is False
    canonical = [
        CanonicalEvent(data=e, payload=json.dumps(e).encode(), delivery=Delivery.TELEMETRY)
        for e in _wap_history()[:3]
    ]
    drain.emit_batch(canonical)
    drain.emit_raw([json.dumps(_wap_history()[3]).encode()])
    drain.flush()
    drain.close()
    out = stream.getvalue()
    assert "WAP branch" in out and "WAP validate" in out
    # Context tracked across batch boundaries: one Run header total.
    assert out.count("── Run:") == 1


def test_emit_raw_skips_malformed_payloads() -> None:
    stream = io.StringIO()
    drain = PrettyDrain(stream=stream, color="never")
    drain.emit_raw([b"not json"])
    assert stream.getvalue() == ""


def test_render_jsonl_round_trip(tmp_path) -> None:
    path = tmp_path / "events.jsonl"
    path.write_text("\n".join(json.dumps(e) for e in _wap_history()[:2]) + "\n\nnot json\n")
    out = render_jsonl(str(path), color="never", symbols="unicode")
    assert "WAP branch" in out
    assert "unrenderable" in out
