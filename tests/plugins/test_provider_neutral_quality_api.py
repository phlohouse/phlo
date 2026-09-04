"""Tests for the provider-neutral quality public API.

Verifies that top-level rule factories return neutral QualityRule
descriptors and reject unbounded inputs, that the Pandera provider
translates them (with safe SQL quoting) or fails on unknown rules at
decoration time, and that phlo.quality.provider/rules resolve named
providers and fail clearly when translation is unsupported.
"""

from __future__ import annotations

import importlib
import sys
from typing import Any

import pytest

pytestmark = pytest.mark.core_regression


def test_quality_rule_factories_are_provider_neutral() -> None:
    """Top-level rule helpers should return QualityRule descriptors without provider imports."""
    import phlo
    from phlo.helpers.quality import QualityRule

    rules = [
        phlo.not_null("id", "email"),
        phlo.unique("id"),
        phlo.freshness("updated_at", hours=24),
        phlo.range_between("score", min_value=0, max_value=100),
        phlo.accepted_values("status", ["active", "paused"]),
    ]

    assert all(isinstance(rule, QualityRule) for rule in rules)
    assert rules[0].kind == "not_null"
    assert rules[0].columns == ["id", "email"]
    assert rules[1].kind == "unique"
    assert rules[2].parameters == {"max_age_hours": 24}
    assert rules[3].parameters == {"min_value": 0, "max_value": 100}
    assert rules[4].parameters == {"values": ["active", "paused"]}


def test_quality_rule_factories_reject_invalid_unbounded_rules() -> None:
    """Rule factories should reject inputs that cannot produce executable checks."""
    import phlo

    with pytest.raises(ValueError, match="range_between requires"):
        phlo.range_between("score")

    with pytest.raises(ValueError, match="accepted_values requires"):
        phlo.accepted_values("status", [])


def test_pandera_quality_provider_builds_checks_from_neutral_rules() -> None:
    """Pandera provider should translate supported neutral rules into Pandera checks."""
    from phlo_pandera.checks import FreshnessCheck, NullCheck, RangeCheck, UniqueCheck
    from phlo_pandera.plugin import PanderaQualityProvider

    from phlo.helpers.quality import QualityRule

    provider = PanderaQualityProvider()
    checks = provider.build_checks_from_rules(
        [
            QualityRule("not_null", ["id", "email"], {}),
            QualityRule("unique", ["id"], {}),
            QualityRule("freshness", ["updated_at"], {"max_age_hours": 24}),
            QualityRule("range", ["score"], {"min_value": 0, "max_value": 100}),
        ]
    )

    assert [type(check) for check in checks] == [
        NullCheck,
        UniqueCheck,
        FreshnessCheck,
        RangeCheck,
    ]


def test_pandera_quality_provider_quotes_accepted_values_sql() -> None:
    """Accepted-values translation should quote identifiers and escape literals."""
    from phlo_pandera.checks_extra import CustomSQLCheck
    from phlo_pandera.plugin import PanderaQualityProvider

    from phlo.helpers.quality import QualityRule

    provider = PanderaQualityProvider()
    checks = provider.build_checks_from_rules(
        [QualityRule("accepted_values", ['status"col'], {"values": ["can't", "done"]})]
    )

    assert len(checks) == 1
    assert isinstance(checks[0], CustomSQLCheck)
    assert checks[0].sql == """SELECT ("status""col" IN ('can''t', 'done')) AS is_valid FROM data"""


def test_pandera_quality_provider_rejects_unknown_neutral_rule() -> None:
    """Unknown neutral rules should fail during decoration, not at runtime."""
    from phlo_pandera.plugin import PanderaQualityProvider

    from phlo.helpers.quality import QualityRule

    provider = PanderaQualityProvider()

    with pytest.raises(ValueError) as exc_info:
        provider.build_checks_from_rules([QualityRule("mystery", ["id"], {})])

    assert "Unsupported neutral quality rule: mystery" in str(exc_info.value)


class _FakeQualityProvider:
    def __init__(self, name: str = "fake") -> None:
        self.name = name

    def get_decorator(self):
        def _decorator(**kwargs: Any):
            def _wrap(fn):
                fn._quality_provider_name = self.name
                fn._quality_kwargs = kwargs
                return fn

            return _wrap

        return _decorator

    def build_checks_from_rules(self, rules: list[Any]) -> list[Any]:
        return [f"native:{rule.kind}" for rule in rules]


class _FakeQualityRegistry:
    def __init__(self, providers: dict[str, _FakeQualityProvider]) -> None:
        self.providers = providers

    def get(self, plugin_type: str, name: str):
        assert plugin_type == "quality_provider"
        return self.providers.get(name)

    def list(self, plugin_type: str) -> list[str]:
        assert plugin_type == "quality_provider"
        return list(self.providers)


class _DecoratorOnlyQualityProvider(_FakeQualityProvider):
    """Provider exposing only the decorator, so global hydration completes."""

    def get_check_classes(self) -> dict[str, Any]:
        return {}

    def get_reconciliation_checks(self) -> dict[str, Any]:
        return {}


def test_quality_provider_returns_named_provider_decorator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """phlo.quality.provider should resolve named quality providers."""
    import phlo.plugins.discovery as discovery

    monkeypatch.setattr(discovery, "discover_plugins", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        discovery,
        "get_global_registry",
        lambda: _FakeQualityRegistry({"pandera": _FakeQualityProvider("pandera")}),
    )
    monkeypatch.delitem(sys.modules, "phlo.quality", raising=False)

    quality = importlib.import_module("phlo.quality")

    @quality.provider("pandera")(table="bronze.users")
    def users_quality() -> None:
        return None

    assert users_quality._quality_provider_name == "pandera"
    assert users_quality._quality_kwargs == {"table": "bronze.users"}


def test_quality_rules_decorator_translates_rules_with_selected_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """phlo.quality.rules should translate neutral rules before calling provider decorator."""
    import phlo
    import phlo.plugins.discovery as discovery

    monkeypatch.setattr(discovery, "discover_plugins", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        discovery,
        "get_global_registry",
        lambda: _FakeQualityRegistry({"pandera": _FakeQualityProvider("pandera")}),
    )
    monkeypatch.delitem(sys.modules, "phlo.quality", raising=False)

    quality = importlib.import_module("phlo.quality")

    @quality.rules(table="bronze.users", rules=[phlo.not_null("id")], provider_name="pandera")
    def users_quality() -> None:
        return None

    assert users_quality._quality_provider_name == "pandera"
    assert users_quality._quality_kwargs["table"] == "bronze.users"
    assert users_quality._quality_kwargs["checks"] == ["native:not_null"]


def test_quality_rules_fails_when_provider_cannot_translate_rules(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Providers without neutral rule support should fail with a clear message."""
    import phlo
    import phlo.plugins.discovery as discovery

    class _NoRuleProvider(_FakeQualityProvider):
        def build_checks_from_rules(self, rules: list[Any]) -> None:
            return None

    monkeypatch.setattr(discovery, "discover_plugins", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        discovery,
        "get_global_registry",
        lambda: _FakeQualityRegistry({"basic": _NoRuleProvider("basic")}),
    )
    monkeypatch.delitem(sys.modules, "phlo.quality", raising=False)

    quality = importlib.import_module("phlo.quality")

    with pytest.raises(ValueError) as exc_info:
        quality.rules(table="bronze.users", rules=[phlo.not_null("id")], provider_name="basic")

    assert "Quality provider 'basic' cannot translate neutral quality rules" in str(exc_info.value)


def test_phlo_quality_alias_is_the_blessed_pandera_decorator() -> None:
    """The deprecated alias wraps the Pandera decorator without changing it.

    The alias emits a warning while ``__wrapped__`` retains the provider's
    decorator for introspection and compatibility.
    """
    from phlo_pandera import phlo_pandera
    from phlo_pandera.plugin import PanderaQualityProvider

    from phlo.quality import phlo_quality, provider

    assert phlo_quality.__wrapped__ is phlo_pandera
    assert provider("pandera") is phlo_pandera
    assert PanderaQualityProvider().get_decorator() is phlo_pandera


def test_phlo_quality_alias_warns_deprecation(monkeypatch: pytest.MonkeyPatch) -> None:
    """The deprecated alias warns and delegates to the provider unchanged."""
    import phlo.plugins.discovery as discovery

    sentinel_kwargs: dict[str, Any] = {}

    def _recording_decorator() -> Any:
        def _factory(**kwargs: Any) -> Any:
            sentinel_kwargs.update(kwargs)

            def _wrap(fn: Any) -> Any:
                fn._quality_provider_name = "pandera"
                return fn

            return _wrap

        return _factory

    provider = _DecoratorOnlyQualityProvider("pandera")
    monkeypatch.setattr(provider, "get_decorator", _recording_decorator)
    monkeypatch.setattr(discovery, "discover_plugins", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        discovery,
        "get_global_registry",
        lambda: _FakeQualityRegistry({"pandera": provider}),
    )
    monkeypatch.delitem(sys.modules, "phlo.quality", raising=False)

    quality = importlib.import_module("phlo.quality")

    with pytest.warns(DeprecationWarning, match="phlo migrate decorators-2026-05"):
        decorator = quality.phlo_quality(table="bronze.users")

    assert sentinel_kwargs == {"table": "bronze.users"}

    def users_quality() -> None:
        return None

    assert decorator(users_quality)._quality_provider_name == "pandera"


def test_quality_forwarders_route_through_pandera_provider_registry() -> None:
    """Quality forwarders resolve through the discovered Pandera provider."""
    from phlo.plugins.discovery import discover_plugins, get_global_registry

    discover_plugins(plugin_type="quality_provider", auto_register=True)

    assert get_global_registry().get("quality_provider", "pandera") is not None
