"""Orchestrator event streams reshape into run diagnostics for evidence-less runs."""

from __future__ import annotations

from phlo_api.observatory_api.observatory_mission_control_sources import (
    _dagster_event_error_message,
    _dagster_timestamp,
    _orchestrator_event_rows,
    _orchestrator_failure_summary,
    _orchestrator_stage_rows,
)

_STEP_FAILURE = {
    "eventType": "STEP_FAILURE",
    "stepKey": "dlt_retail_products",
    "message": "Execution of step 'dlt_retail_products' failed.",
    "timestamp": "1789733920800",
    "level": "ERROR",
    "error": {
        "message": "Exceeded max_retries of 1",
        "causes": [
            {"message": "PhloConfigError (PHLO-005): Missing partition key for ingestion asset"}
        ],
    },
}


def test_error_message_prefers_deepest_cause() -> None:
    assert (
        _dagster_event_error_message(_STEP_FAILURE)
        == "PhloConfigError (PHLO-005): Missing partition key for ingestion asset"
    )


def test_error_message_falls_back_to_top_level() -> None:
    event = {"error": {"message": "boom", "causes": []}}
    assert _dagster_event_error_message(event) == "boom"
    assert _dagster_event_error_message({"error": "not-a-dict"}) is None
    assert _dagster_event_error_message({}) is None


def test_dagster_timestamp_parses_ms_epoch() -> None:
    assert _dagster_timestamp({"timestamp": "1789733920800"}).startswith("2026-")
    assert _dagster_timestamp({"timestamp": None}) is None
    assert _dagster_timestamp({}) is None


def test_event_rows_prefix_step_key_and_use_error_cause() -> None:
    rows = _orchestrator_event_rows([_STEP_FAILURE])

    assert len(rows) == 1
    row = rows[0]
    assert row["event_type"] == "STEP_FAILURE"
    assert row["payload"]["level"] == "ERROR"
    assert row["payload"]["message"].startswith("dlt_retail_products ·")
    assert "PHLO-005" in row["payload"]["message"]


def test_stage_rows_fold_step_events_into_terminal_outcomes() -> None:
    rows = _orchestrator_stage_rows(
        [
            {"eventType": "STEP_START", "stepKey": "a", "timestamp": "1789733920000"},
            {"eventType": "STEP_UP_FOR_RETRY", "stepKey": "a", "timestamp": "1789733920100"},
            {**_STEP_FAILURE, "stepKey": "a", "timestamp": "1789733920200"},
            {"eventType": "STEP_START", "stepKey": "b", "timestamp": "1789733920300"},
            {"eventType": "STEP_SUCCESS", "stepKey": "b", "timestamp": "1789733920400"},
        ]
    )

    assert [row["stage_id"] for row in rows] == ["a", "b"]
    assert rows[0]["status"] == "failed"
    assert "PHLO-005" in (rows[0]["error"] or "")
    assert rows[1]["status"] == "success"
    assert rows[1]["started_at"] is not None and rows[1]["finished_at"] is not None


def test_failure_summary_leads_with_first_step_failure() -> None:
    events = [
        {"eventType": "PIPELINE_STARTING", "message": "started"},
        _STEP_FAILURE,
        {"eventType": "PIPELINE_FAILURE", "message": "run failed"},
    ]

    summary = _orchestrator_failure_summary(events)

    assert summary is not None
    assert summary.startswith("dlt_retail_products:")
    assert "PHLO-005" in summary


def test_failure_summary_falls_back_to_run_level_failure() -> None:
    events = [{"eventType": "PIPELINE_FAILURE", "message": "run failed hard"}]
    assert _orchestrator_failure_summary(events) == "run failed hard"
    assert _orchestrator_failure_summary([]) is None
    assert _orchestrator_failure_summary([{"eventType": "STEP_START"}]) is None
