"""Tests for Loki response normalization and URL override rejection.

Covers run-id correlation on plain container logs, legacy and current
function metadata parsing, and server-configuration-only URL resolution.
Regex filtering must reject oversized or invalid patterns before fetching,
time out as a structured 422, and never echo log content in errors.
"""

from __future__ import annotations

import asyncio
import json
from time import monotonic

import httpx
import pytest
from fastapi import FastAPI, HTTPException

from phlo_api.observatory_api import loki
from phlo_api.observatory_api.loki import (
    LogEntry,
    LogQueryResult,
    build_log_query,
    fetch_run_log_entries,
    parse_loki_response,
    reject_request_loki_url,
    resolve_loki_url,
)


def test_build_log_query_matches_dagster_run_id_in_plain_container_logs() -> None:
    """Run correlation must work before JSON parsing structured application logs."""
    assert build_log_query(run_id="run-123") == ('{container=~".+"} |= "run-123" | json')


def test_parse_loki_response_emits_function_and_legacy_fn_metadata() -> None:
    response = {
        "data": {
            "result": [
                {
                    "stream": {},
                    "values": [
                        [
                            "1700000000000000000",
                            json.dumps(
                                {
                                    "level": "info",
                                    "message": "hello",
                                    "function": "run_step",
                                }
                            ),
                        ]
                    ],
                }
            ]
        }
    }

    entries = parse_loki_response(response)

    assert entries[0].metadata["function"] == "run_step"
    assert entries[0].metadata["fn"] == "run_step"


def test_parse_loki_response_reads_legacy_fn_metadata() -> None:
    response = {
        "data": {
            "result": [
                {
                    "stream": {},
                    "values": [
                        [
                            "1700000000000000000",
                            json.dumps({"level": "info", "message": "hello", "fn": "run_step"}),
                        ]
                    ],
                }
            ]
        }
    }

    entries = parse_loki_response(response)

    assert entries[0].metadata["function"] == "run_step"
    assert entries[0].metadata["fn"] == "run_step"


def test_reject_request_loki_url_override() -> None:
    reject_request_loki_url(None)

    with pytest.raises(HTTPException) as exc:
        reject_request_loki_url("http://169.254.169.254/latest/meta-data/#")

    assert exc.value.status_code == 422
    assert exc.value.detail["error"] == "loki_url_override_not_allowed"


def test_resolve_loki_url_uses_server_configuration_only(monkeypatch) -> None:
    monkeypatch.setattr(
        "phlo_api.observatory_api.loki.resolve_url",
        lambda url, *, port_env_var=None: url,
    )
    monkeypatch.setattr(
        "phlo_api.observatory_api.loki.project_env_value",
        lambda key, default=None: "http://loki.internal:3100" if key == "LOKI_URL" else default,
    )

    assert resolve_loki_url() == "http://loki.internal:3100"
    with pytest.raises(TypeError):
        resolve_loki_url("http://attacker.example")  # type: ignore[call-arg]


@pytest.mark.anyio
async def test_run_log_regex_preserves_documented_filtering(monkeypatch) -> None:
    entries = [
        LogEntry(timestamp="1", level="info", message="Started job 42", metadata={}),
        LogEntry(timestamp="2", level="info", message="Completed job 42", metadata={}),
    ]

    async def fetch_entries(**_: object) -> LogQueryResult:
        return LogQueryResult(entries=entries, has_more=False)

    monkeypatch.setattr(loki, "fetch_log_entries", fetch_entries)

    result = await fetch_run_log_entries("run-1", regex=r"^Started\s+job\s+\d+$")

    assert [entry.message for entry in result.entries] == ["Started job 42"]


@pytest.mark.anyio
async def test_run_log_regex_rejects_oversized_pattern_before_fetch(monkeypatch) -> None:
    async def fetch_entries(**_: object) -> LogQueryResult:
        raise AssertionError(
            "oversized regex must be rejected before fetching or iterating messages"
        )

    monkeypatch.setattr(loki, "fetch_log_entries", fetch_entries)

    with pytest.raises(HTTPException) as exc:
        await fetch_run_log_entries("run-1", regex="a" * 513)

    assert exc.value.status_code == 400
    assert exc.value.detail == {"error": "invalid_regex"}


@pytest.mark.anyio
async def test_run_log_regex_rejects_invalid_syntax_without_log_content(monkeypatch) -> None:
    secret_message = "credential=do-not-echo"

    async def fetch_entries(**_: object) -> LogQueryResult:
        return LogQueryResult(
            entries=[LogEntry(timestamp="1", level="info", message=secret_message, metadata={})],
            has_more=False,
        )

    monkeypatch.setattr(loki, "fetch_log_entries", fetch_entries)

    with pytest.raises(HTTPException) as exc:
        await fetch_run_log_entries("run-1", regex="(")

    assert exc.value.status_code == 400
    assert exc.value.detail == {"error": "invalid_regex"}
    assert secret_message not in str(exc.value.detail)


@pytest.mark.anyio
async def test_run_log_regex_timeout_does_not_stall_heartbeat_request(monkeypatch) -> None:
    secret_message = "a" * 30 + "!"

    async def fetch_entries(**_: object) -> LogQueryResult:
        return LogQueryResult(
            entries=[LogEntry(timestamp="1", level="info", message=secret_message, metadata={})],
            has_more=False,
        )

    monkeypatch.setattr(loki, "fetch_log_entries", fetch_entries)
    app = FastAPI()
    app.include_router(loki.router, prefix="/api/loki")

    @app.get("/heartbeat")
    async def heartbeat() -> dict[str, bool]:
        return {"alive": True}

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        started = monotonic()
        filter_request = asyncio.create_task(
            client.get("/api/loki/runs/run-1", params={"regex": r"(a+)+$"})
        )
        await asyncio.sleep(0.02)
        heartbeat_response = await client.get("/heartbeat")
        filter_response = await filter_request

    assert heartbeat_response.json() == {"alive": True}
    assert monotonic() - started < 0.5
    assert filter_response.status_code == 422
    assert filter_response.json() == {"detail": {"error": "regex_evaluation_timeout"}}
    assert secret_message not in filter_response.text
