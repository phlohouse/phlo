"""Production authorization and regulated-mode precedence contracts."""

from __future__ import annotations

import pytest

from phlo.security.mode import is_regulated, requires_http_authorization


@pytest.mark.parametrize(
    ("canonical", "configured", "file_value", "expected"),
    [
        (" true ", False, False, True),
        ("1", None, None, True),
        ("yes", None, None, True),
        ("on", False, False, True),
        (" OFF ", True, True, False),
        ("0", None, True, False),
        ("no", None, True, False),
        ("false", True, True, False),
        (None, True, False, True),
        (None, False, True, False),
        ("invalid", None, True, True),
        ("invalid", False, True, False),
        (None, None, True, True),
        (None, None, False, False),
        (None, None, None, False),
    ],
)
def test_is_regulated_precedence(
    monkeypatch: pytest.MonkeyPatch,
    canonical: str | None,
    configured: bool | None,
    file_value: bool | None,
    expected: bool,
) -> None:
    if canonical is None:
        monkeypatch.delenv("PHLO_REGULATED", raising=False)
    else:
        monkeypatch.setenv("PHLO_REGULATED", canonical)
    monkeypatch.setattr("phlo.config.project.get_regulated_config", lambda: file_value)

    assert is_regulated(configured) is expected


@pytest.mark.parametrize("value", ["true", "false"])
def test_removed_regulated_mode_env_fails_closed(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.delenv("PHLO_REGULATED", raising=False)
    monkeypatch.setenv("PHLO_REGULATED_MODE", value)
    monkeypatch.setattr("phlo.config.project.get_regulated_config", lambda: None)

    with pytest.raises(RuntimeError, match="PHLO_REGULATED_MODE was removed in 0.19.0"):
        is_regulated()
    with pytest.raises(RuntimeError, match="set PHLO_REGULATED instead"):
        requires_http_authorization()
    assert is_regulated(False) is False
    monkeypatch.setenv("PHLO_REGULATED", "true")
    assert is_regulated() is True


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
