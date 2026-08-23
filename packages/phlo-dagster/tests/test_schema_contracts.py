"""Tests for schema-contract refresh integration hooks.

Covers the env-gated behaviour of maybe_refresh_contracts: the refresh runs
forced, with the configured selection, only when PHLO_AUTO_REFRESH_CONTRACTS
is enabled, and no refresh events are emitted otherwise.
"""

from __future__ import annotations

from pathlib import Path

from phlo_dagster.framework import schema_contracts


class _LoggerStub:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []

    def info(self, event: str, **kwargs: object) -> None:
        self.events.append((event, kwargs))

    def warning(self, event: str, **kwargs: object) -> None:
        self.events.append((event, kwargs))


def test_maybe_refresh_contracts_runs_when_enabled(monkeypatch) -> None:
    """Refresh is executed when env gate is enabled."""
    logger = _LoggerStub()
    monkeypatch.setenv("PHLO_AUTO_REFRESH_CONTRACTS", "1")
    monkeypatch.setenv("PHLO_CONTRACT_REFRESH_SELECTION", "dlt_orders")

    calls: list[str | None] = []

    def _fake_refresh(*, selection: str | None, force: bool = True) -> int:
        assert force is True
        calls.append(selection)
        return 1

    monkeypatch.setattr(
        "phlo.cli.commands.schema_migrate.refresh_contracts_for_selection",
        _fake_refresh,
    )

    schema_contracts.maybe_refresh_contracts(Path("workflows"), logger)
    assert calls == ["dlt_orders"]
    assert any(name == "schema_contract_refresh_completed" for name, _ in logger.events)


def test_maybe_refresh_contracts_skips_when_disabled(monkeypatch) -> None:
    """Refresh is skipped when env gate is not enabled."""
    logger = _LoggerStub()
    monkeypatch.delenv("PHLO_AUTO_REFRESH_CONTRACTS", raising=False)

    schema_contracts.maybe_refresh_contracts(Path("workflows"), logger)
    assert logger.events == []
