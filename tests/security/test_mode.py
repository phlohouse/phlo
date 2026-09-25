"""Production authorization and regulated-mode precedence contracts."""

from __future__ import annotations

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
