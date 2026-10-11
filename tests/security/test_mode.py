"""Production authorization and regulated-mode precedence contracts."""

from __future__ import annotations

import logging

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
    monkeypatch.setattr("phlo.config.project.get_regulated_config", lambda: file_value)

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


def test_legacy_counter_reaches_canonical_metric_summaries(
    tmp_path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Only fallback uses count; canonical/config overrides and absence do not."""
    from observe_core import flush_metrics
    from observe_core.runtime import get_runtime

    from phlo import telemetry

    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    monkeypatch.setenv("PHLO_REGULATED_MODE", "true")
    monkeypatch.setenv("PHLO_OBSERVE_PRETTY", "false")
    telemetry.reset_for_tests()
    try:
        assert telemetry.configure(enabled=True, runtime_backend="capture", drains=[])
        caplog.set_level(logging.WARNING, logger="phlo.security.mode")
        monkeypatch.setenv("PHLO_REGULATED", "false")
        assert not is_regulated()
        monkeypatch.delenv("PHLO_REGULATED")
        assert not is_regulated(False)
        assert is_regulated()
        assert is_regulated()
        monkeypatch.delenv("PHLO_REGULATED_MODE")
        assert not is_regulated()
        flush_metrics()

        payloads = get_runtime()._backend.payloads()
        summaries = [
            record["attributes"]
            for record in payloads
            if record["event"] == "metric.summary"
            and record["attributes"]["metric"] == "phlo.legacy.regulated_mode_env.uses"
        ]
        assert sum(summary["sum"] for summary in summaries) == 2
        assert sum(summary["count"] for summary in summaries) == 2
        assert all(summary["dimensions"] == {"unit": "uses"} for summary in summaries)
        notices = [
            record["attributes"]
            for record in payloads
            if record["event"] == "application.log"
            and record["attributes"]["logger"] == "phlo.security.mode"
        ]
        assert len(notices) == 2
        for notice in notices:
            assert notice["old"] == "PHLO_REGULATED_MODE"
            assert notice["new"] == "PHLO_REGULATED"
            assert notice["removal_version"] == "0.19.0"
            assert notice["message"] == (
                "PHLO_REGULATED_MODE is deprecated and will be removed in 0.19.0; "
                "use PHLO_REGULATED instead"
            )
        assert [record.msg["event"] for record in caplog.records] == ["deprecated_env_var"] * 2
    finally:
        telemetry.reset_for_tests()
