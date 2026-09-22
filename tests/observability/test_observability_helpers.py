"""Tests for the public workflow observability helpers."""

from __future__ import annotations

import pytest

from phlo.helpers.observability import emit_metric


def test_emit_metric_uses_phlo_observe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[tuple[str, float, str | None]] = []
    monkeypatch.setattr(
        "phlo.helpers.observability.phlo_observe.metric",
        lambda name, value, *, unit=None: observed.append((name, value, unit)),
    )
    monkeypatch.setattr("phlo.helpers.observability.resolve_capability", lambda _name: None)

    emit_metric("pipeline.duration", 12.5, unit="ms", payload={"asset": "raw.users"})

    assert observed == [("pipeline.duration", 12.5, "ms")]
