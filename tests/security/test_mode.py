"""Production authorization and regulated-mode precedence contracts."""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from phlo.security.mode import is_regulated, requires_http_authorization


@pytest.mark.parametrize(
    ("canonical", "configured", "deprecated", "file_value", "expected"),
    [
        (" true ", False, "false", False, True),
        ("1", None, None, None, True),
        ("yes", None, None, None, True),
        ("on", False, None, False, True),
        (" OFF ", True, "true", True, False),
        ("0", None, None, True, False),
        ("no", None, None, True, False),
        ("false", True, None, True, False),
        (None, True, "false", False, True),
        (None, False, "true", True, False),
        (None, None, "yes", False, True),
        (None, None, "1", False, True),
        (None, None, "on", False, True),
        (None, None, "no", True, False),
        (None, None, "0", True, False),
        (None, None, "off", True, False),
        (None, None, "false", True, False),
        ("invalid", None, "true", False, True),
        ("invalid", False, "true", True, False),
        (None, None, None, True, True),
        (None, None, None, False, False),
        (None, None, None, None, False),
    ],
)
def test_is_regulated_precedence(
    monkeypatch: pytest.MonkeyPatch,
    canonical: str | None,
    configured: bool | None,
    deprecated: str | None,
    file_value: bool | None,
    expected: bool,
) -> None:
    for name, value in (("PHLO_REGULATED", canonical), ("PHLO_REGULATED_MODE", deprecated)):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    monkeypatch.setattr("phlo.infrastructure.config.get_regulated_config", lambda: file_value)

    assert is_regulated(configured) is expected


@pytest.mark.parametrize(
    ("environment", "regulated", "expected"),
    [
        ("prod", False, True),
        (" production ", False, True),
        ("STAGING", False, True),
        ("regulated", False, True),
        ("dev", False, False),
        ("development", False, False),
        ("test", False, False),
        ("", False, False),
        (None, False, False),
        ("dev", True, True),
    ],
)
def test_http_authorization_environment(
    monkeypatch: pytest.MonkeyPatch, environment: str | None, regulated: bool, expected: bool
) -> None:
    if environment is None:
        monkeypatch.delenv("PHLO_ENVIRONMENT", raising=False)
    else:
        monkeypatch.setenv("PHLO_ENVIRONMENT", environment)
    monkeypatch.setenv("PHLO_REGULATED", str(regulated).lower())

    assert requires_http_authorization() is expected


def test_legacy_env_counts_only_fallback_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    from unittest.mock import Mock

    counter = Mock()
    monkeypatch.setattr("phlo.security.mode.metric", counter)
    monkeypatch.setattr("phlo.infrastructure.config.get_regulated_config", lambda: False)
    monkeypatch.setenv("PHLO_REGULATED_MODE", "yes")
    monkeypatch.setenv("PHLO_REGULATED", "false")
    assert is_regulated() is False
    monkeypatch.delenv("PHLO_REGULATED")
    assert is_regulated(False) is False
    counter.assert_not_called()

    assert is_regulated() is True
    assert is_regulated() is True
    assert counter.call_count == 2
    counter.assert_called_with("phlo.legacy.regulated_mode_env.uses", 1, unit="uses")
    monkeypatch.delenv("PHLO_REGULATED_MODE")
    assert is_regulated() is False
    assert counter.call_count == 2


def test_legacy_counter_reaches_canonical_metric_summaries(tmp_path) -> None:
    """Two uses must aggregate to two, not overwrite a gauge with one."""
    path = tmp_path / "events.jsonl"
    subprocess.run(
        [
            sys.executable,
            "-c",
            "from phlo.security.mode import is_regulated; "
            "from observe_core import flush_metrics, flush; "
            "assert is_regulated(); assert is_regulated(); flush_metrics(); flush()",
        ],
        env={
            **os.environ,
            "PHLO_REGULATED": "",
            "PHLO_REGULATED_MODE": "true",
            "PHLO_OBSERVE_ENABLED": "true",
            "PHLO_OBSERVE_PRETTY": "false",
            "OBSERVE_DRAINS": "jsonl",
            "OBSERVE_JSONL_PATH": str(path),
        },
        check=True,
        capture_output=True,
        text=True,
    )
    summaries = [
        record["attributes"]
        for line in path.read_text(encoding="utf-8").splitlines()
        if (record := json.loads(line))["event"] == "metric.summary"
        and record["attributes"]["metric"] == "phlo.legacy.regulated_mode_env.uses"
    ]
    assert sum(summary["sum"] for summary in summaries) == 2
    assert sum(summary["count"] for summary in summaries) == 2
