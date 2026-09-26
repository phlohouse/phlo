"""Phase-0 contract and mounted-route inventory checks."""

from __future__ import annotations

import json
import re
from fnmatch import fnmatchcase
from pathlib import Path

import pytest
from pydantic import ValidationError
from fastapi import HTTPException

from phlo_api.incidents import (
    _decode_cursor,
    _decode_policy_cursor,
    _encode_cursor,
    _encode_policy_cursor,
)
from phlo_api.main import app
from phlo_api.v1_contract import EnvironmentSelection, EnvironmentTarget, ServicesResponse


def test_selection_rejects_missing_invalid_and_caller_supplied_target() -> None:
    for payload in (
        "{}",
        '{"env":"production"}',
        '{"env":"PROD"}',
        '{"env":"prod","nessie_ref":"other"}',
    ):
        with pytest.raises(ValidationError):
            EnvironmentSelection.model_validate_json(payload)
    assert EnvironmentSelection.model_validate_json('{"env":"prod"}').env == "prod"
    assert EnvironmentSelection.model_validate_json('{"env":"staging"}').env == "staging"
    with pytest.raises(ValidationError):
        EnvironmentTarget(dagster_location="", nessie_ref="staging")


def test_incident_cursor_binds_environment_and_collection():
    from datetime import UTC, datetime

    cursor = _encode_cursor("prod", "incidents", datetime.now(UTC), "incident-1")
    assert _decode_cursor(cursor, "prod", "incidents")[1] == "incident-1"
    for env, kind, malformed in (
        ("staging", "incidents", cursor),
        ("prod", "activity", cursor),
        ("prod", "incidents", "not-a-cursor"),
    ):
        with pytest.raises(HTTPException) as error:
            _decode_cursor(malformed, env, kind)
        assert error.value.status_code == 400


def test_asset_policy_cursor_binds_environment() -> None:
    cursor = _encode_policy_cursor("prod", "warehouse.orders")
    assert _decode_policy_cursor(cursor, "prod") == "warehouse.orders"
    with pytest.raises(HTTPException) as error:
        _decode_policy_cursor(cursor, "staging")
    assert error.value.status_code == 400


def test_service_wire_values_reject_display_labels_missing_evidence_and_bad_metrics() -> None:
    base = {
        "env": "staging",
        "items": [
            {
                "id": "dagster",
                "status": "healthy",
                "observed_at": "2026-09-25T09:31:04Z",
                "response_time_seconds": 0.25,
            }
        ],
        "next_cursor": None,
    }
    assert (
        ServicesResponse.model_validate_json(json.dumps(base)).items[0].response_time_seconds
        == 0.25
    )
    for changed in (
        {"status": "All systems operational"},
        {"status": "healthy", "observed_at": None},
        {"observed_at": "5 minutes ago"},
        {"observed_at": "2026-09-25T09:31:04"},
        {"response_time_seconds": -0.1},
        {"response_time_seconds": "250 ms"},
        {"duration": "fast"},
    ):
        with pytest.raises(ValidationError):
            ServicesResponse.model_validate_json(
                json.dumps({**base, "items": [{**base["items"][0], **changed}]})
            )
    missing = {
        **base,
        "items": [
            {
                **base["items"][0],
                "status": "unavailable",
                "observed_at": None,
                "response_time_seconds": None,
            }
        ],
    }
    assert (
        ServicesResponse.model_validate_json(json.dumps(missing)).items[0].status == "unavailable"
    )


def test_documented_route_decisions_partition_the_mounted_inventory() -> None:
    root = Path(__file__).resolve().parents[3]
    reference = json.loads(
        (root / "docs/reference/generated/http-api.json").read_text(encoding="utf-8")
    )
    documented = {(entry["method"], entry["path"]) for entry in reference["endpoints"]}
    mounted = {
        (method.upper(), path)
        for path, operations in app.openapi()["paths"].items()
        for method in operations
        if method.lower() in {"get", "post", "put", "patch", "delete"}
    }
    assert documented == mounted
    assert {path for _, path in mounted if path.startswith("/api/v1/")} == {
        "/api/v1/me",
        "/api/v1/environments",
        "/api/v1/services",
        "/api/v1/events",
        "/api/v1/assets",
        "/api/v1/assets/{asset_id}",
        "/api/v1/assets/{asset_id}/runs",
        "/api/v1/assets/{asset_id}/checks",
        "/api/v1/assets/{asset_id}/audits",
        "/api/v1/assets/{asset_id}/audits/{proposal_id}",
        "/api/v1/assets/{asset_id}/audits/{proposal_id}/pull-request",
        "/api/v1/assets/{asset_id}/preview",
        "/api/v1/assets/{asset_id}/materialize",
        "/api/v1/assets/{asset_id}/backfill",
        "/api/v1/assets/{asset_id}/materialization-estimate",
        "/api/v1/tables/{table_name}/snapshots",
        "/api/v1/tables/{table_name}/schema-history",
        "/api/v1/sources",
        "/api/v1/layers",
        "/api/v1/overview",
        "/api/v1/incidents",
        "/api/v1/incidents/stats",
        "/api/v1/incidents/{incident_id}",
        "/api/v1/incidents/{incident_id}/timeline",
        "/api/v1/incidents/{incident_id}/subscriptions",
        "/api/v1/incidents/{incident_id}/follow-ups",
        "/api/v1/incidents/{incident_id}/follow-ups/{follow_up_id}",
        "/api/v1/assets/{asset_id}/incident-policy",
        "/api/v1/incident-policies",
        "/api/v1/activity",
    }
    legacy = {(method, path) for method, path in documented if not path.startswith("/api/v1/")}

    text = (root / "docs/architecture/unified-api-phase-0.md").read_text(encoding="utf-8")
    family_table = text.split("| Mounted path family", 1)[1].split(
        "Within `/api/observatory/*`", 1
    )[0]
    families = [
        (pattern, int(count))
        for pattern, count in re.findall(r"`(/[^`]+)` \((\d+)\)", family_table)
    ]
    for pattern, count in families:
        assert sum(fnmatchcase(path, pattern) for _, path in legacy) == count, pattern
    assert all(
        sum(fnmatchcase(path, pattern) for pattern, _ in families) == 1 for _, path in legacy
    )

    detail_table = text.split("| Old suffixes after `/api/observatory/`", 1)[1].split(
        "The counts are", 1
    )[0]
    groups = []
    for line in detail_table.splitlines():
        match = re.match(r"\| ((?:`[^`]+`(?:, )?)+) \| (\d+) \|", line)
        if match:
            groups.append((re.findall(r"`([^`]+)`", match[1]), int(match[2])))
    suffixes = [
        path.removeprefix("/api/observatory/")
        for _, path in legacy
        if path.startswith("/api/observatory/")
    ]
    assert len(groups) == 7
    for patterns, count in groups:
        assert (
            sum(any(fnmatchcase(suffix, pattern) for pattern in patterns) for suffix in suffixes)
            == count
        ), patterns
    assert all(
        sum(any(fnmatchcase(suffix, pattern) for pattern in patterns) for patterns, _ in groups)
        == 1
        for suffix in suffixes
    )
