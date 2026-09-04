"""Data quality public API for Phlo.

This module provides comprehensive data quality validation primitives for Phlo.
It discovers and loads quality provider plugins (primarily ``phlo-pandera``)
to offer a rich set of validation checks that can be applied to data assets.

Quality checks ensure data integrity, consistency, and compliance with business
rules before data is committed to the lakehouse. The checks can be applied
automatically during the Write-Audit-Publish lifecycle or manually for
data exploration.

Key Components:
    - :func:`pandera` via :func:`provider`: Primary decorator path for applying quality checks
    - :func:`phlo_quality`: Deprecated third name for the Pandera decorator (emits a DeprecationWarning)
    - :func:`get_quality_checks`: Retrieve registered quality checks
    - :func:`clear_quality_checks`: Clear the quality check registry
    - Quality check classes: Validation primitives for various data checks

Quality Check Types:
    - :class:`QualityCheck`: Base class for all quality checks
    - :class:`NullCheck`: Validate absence/presence of null values
    - :class:`RangeCheck`: Validate numeric value ranges
    - :class:`FreshnessCheck`: Validate data recency
    - :class:`UniqueCheck`: Validate column uniqueness
    - :class:`CountCheck`: Validate row counts
    - :class:`SchemaCheck`: Validate schema compliance
    - :class:`PatternCheck`: Validate regex patterns
    - :class:`CustomSQLCheck`: Validate with custom SQL queries
    - :class:`ReconciliationCheck`: Compare datasets across sources
    - :class:`AggregateConsistencyCheck`: Validate aggregate values
    - :class:`KeyParityCheck`: Validate key presence across tables
    - :class:`MultiAggregateConsistencyCheck`: Multi-table aggregate validation
    - :class:`ChecksumReconciliationCheck`: Row-level checksum validation

Provider Discovery:
    The module uses plugin discovery to load the quality provider. The primary
    provider is ``phlo-pandera``, which uses Pandera schemas for validation.

Note:
    This module requires a quality provider to be installed. Install with:
    ``pip install phlo[defaults]`` or ``pip install phlo-pandera``.

Example:
    ```python
    from phlo.quality import pandera, NullCheck, RangeCheck
    import pandera as pa

    # Define a schema with validation rules
    class UserSchema(pa.DataFrameModel):
        id: int = pa.Field(nullable=False, unique=True)
        email: str = pa.Field(nullable=False)
        age: int = pa.Field(ge=0, le=150)

    # Apply quality checks to an asset
    @pandera(schema=UserSchema)
    def validated_users():
        return load_user_data()
    ```

See Also:
    - :mod:`phlo.ingestion`: Data ingestion operations
    - :mod:`phlo.hooks.events.QualityResultEvent`: Quality result events
    - ``phlo-pandera`` package for Pandera integration

Raises:
    ModuleNotFoundError: If no quality provider is installed.

"""

from __future__ import annotations

import functools
import importlib
import warnings
from collections.abc import Callable
from typing import Any, cast

from phlo.logging import get_logger
from phlo.plugins.base.quality_provider import QualityProviderPlugin

logger = get_logger(__name__)

# These are type-checker declarations only. The names have no module-level
# value until _load_quality_provider() binds them onto the module globals, so
# touching them before that runs raises AttributeError (handled by
# __getattr__ below).
get_quality_checks: Callable[[], list[Any]] | None
clear_quality_checks: Callable[[], None] | None
QualityCheck: type | None
NullCheck: type | None
RangeCheck: type | None
FreshnessCheck: type | None
UniqueCheck: type | None
CountCheck: type | None
SchemaCheck: type | None
CustomSQLCheck: type | None
PatternCheck: type | None
ReconciliationCheck: type | None
AggregateConsistencyCheck: type | None
AggregateSpec: type | None
KeyParityCheck: type | None
MultiAggregateConsistencyCheck: type | None
ChecksumReconciliationCheck: type | None
PANDERA_CONTRACT_CHECK_NAME: str | None
QualityCheckContract: type | None
dbt_check_name: Callable[[str, str], str] | None
phlo_quality: Callable | None

_QUALITY_EXPORTS = {
    "pandera",
    "phlo_quality",
    "provider",
    "providers",
    "rules",
    "get_quality_checks",
    "clear_quality_checks",
    "QualityCheck",
    "NullCheck",
    "RangeCheck",
    "FreshnessCheck",
    "UniqueCheck",
    "CountCheck",
    "SchemaCheck",
    "CustomSQLCheck",
    "PatternCheck",
    "ReconciliationCheck",
    "AggregateConsistencyCheck",
    "AggregateSpec",
    "KeyParityCheck",
    "MultiAggregateConsistencyCheck",
    "ChecksumReconciliationCheck",
    "PANDERA_CONTRACT_CHECK_NAME",
    "QualityCheckContract",
    "dbt_check_name",
}
_quality_provider_loaded = False


def _provider_api_module(provider: QualityProviderPlugin) -> Any | None:
    """Resolve the provider package module that may expose helper exports."""
    provider_module = provider.__class__.__module__
    provider_package = provider_module.split(".", 1)[0]
    try:
        return importlib.import_module(provider_package)
    except ModuleNotFoundError:
        return None


def _deprecate_phlo_quality_alias(decorator: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap the provider decorator so the ``phlo_quality`` third name warns.

    The alias can be migrated with the ``decorators-2026-05`` codemod. The
    wrapped decorator stays reachable through ``phlo.quality.__wrapped__`` for
    introspection and compatibility.
    """

    @functools.wraps(decorator)
    def _phlo_quality_alias(*args: Any, **kwargs: Any) -> Any:
        warnings.warn(
            "phlo_quality is deprecated and will be removed in an upcoming "
            "release; use phlo.quality.pandera (or the provider package "
            "decorator) instead. Migrate with: phlo migrate decorators-2026-05",
            DeprecationWarning,
            stacklevel=2,
        )
        return decorator(*args, **kwargs)

    return _phlo_quality_alias


def _load_quality_provider() -> QualityProviderPlugin | None:
    """Load quality provider exports through plugin discovery."""
    global phlo_quality
    global get_quality_checks
    global clear_quality_checks
    global QualityCheck
    global NullCheck
    global RangeCheck
    global FreshnessCheck
    global UniqueCheck
    global CountCheck
    global SchemaCheck
    global CustomSQLCheck
    global PatternCheck
    global ReconciliationCheck
    global AggregateConsistencyCheck
    global AggregateSpec
    global KeyParityCheck
    global MultiAggregateConsistencyCheck
    global ChecksumReconciliationCheck
    global PANDERA_CONTRACT_CHECK_NAME
    global QualityCheckContract
    global dbt_check_name

    # Every failure mode here -- discovery problems, a missing pandera
    # provider, broken provider internals -- is surfaced as one actionable
    # ModuleNotFoundError telling users what to install.
    try:
        from phlo.plugins.discovery import discover_plugins, get_global_registry

        discover_plugins()
        provider = cast(
            QualityProviderPlugin | None,
            get_global_registry().get("quality_provider", "pandera"),
        )
        if provider is not None:
            provider_module = _provider_api_module(provider)
            phlo_quality = _deprecate_phlo_quality_alias(provider.get_decorator())
            check_classes = provider.get_check_classes()
            NullCheck = check_classes.get("null")
            RangeCheck = check_classes.get("range")
            FreshnessCheck = check_classes.get("freshness")
            UniqueCheck = check_classes.get("unique")
            CountCheck = check_classes.get("count")
            SchemaCheck = check_classes.get("schema")
            PatternCheck = check_classes.get("pattern")
            QualityCheck = check_classes.get("quality_check")
            CustomSQLCheck = check_classes.get("custom_sql") or (
                getattr(provider_module, "CustomSQLCheck", None) if provider_module else None
            )
            rec_classes = provider.get_reconciliation_checks() or {}
            ReconciliationCheck = rec_classes.get("reconciliation")
            AggregateConsistencyCheck = rec_classes.get("aggregate_consistency")
            AggregateSpec = rec_classes.get("aggregate_spec")
            KeyParityCheck = rec_classes.get("key_parity")
            MultiAggregateConsistencyCheck = rec_classes.get("multi_aggregate")
            ChecksumReconciliationCheck = rec_classes.get("checksum")
            get_quality_checks = (
                getattr(provider_module, "get_quality_checks", None) if provider_module else None
            )
            clear_quality_checks = (
                getattr(provider_module, "clear_quality_checks", None) if provider_module else None
            )
            PANDERA_CONTRACT_CHECK_NAME = (
                getattr(provider_module, "PANDERA_CONTRACT_CHECK_NAME", None)
                if provider_module
                else None
            )
            QualityCheckContract = (
                getattr(provider_module, "QualityCheckContract", None) if provider_module else None
            )
            dbt_check_name = (
                getattr(provider_module, "dbt_check_name", None) if provider_module else None
            )
            return provider
    except Exception as e:
        logger.warning("quality_provider_discovery_failed", exc_info=True, error=str(e))

    raise ModuleNotFoundError(
        "phlo.quality requires a quality provider. Install phlo[defaults] or phlo-pandera."
    )


def _ensure_quality_provider_loaded() -> None:
    """Load provider-backed quality exports on first use."""
    global _quality_provider_loaded

    if _quality_provider_loaded:
        return
    # The flag flips only after a successful load, so a failed attempt is
    # retried on the next access instead of caching the failure forever.
    _load_quality_provider()
    _quality_provider_loaded = True


def _discover_quality_providers() -> None:
    """Load installed quality providers into the plugin registry."""
    from phlo.plugins.discovery import discover_plugins

    discover_plugins(plugin_type="quality_provider", auto_register=True)


def providers() -> list[str]:
    """Return installed quality provider names."""
    from phlo.plugins.discovery import get_global_registry

    _discover_quality_providers()
    return get_global_registry().list("quality_provider")


def _missing_quality_provider_error(name: str) -> ModuleNotFoundError:
    installed = providers()
    installed_text = ", ".join(installed) if installed else "none"
    return ModuleNotFoundError(
        f"Quality provider '{name}' is not installed. "
        f"Installed quality providers: {installed_text}. "
        f"Install phlo-{name} or choose one of the installed providers."
    )


def _quality_provider_or_raise(name: str) -> QualityProviderPlugin:
    """Resolve a quality provider plugin or raise a public install error."""
    from phlo.plugins.discovery import get_global_registry

    _discover_quality_providers()
    provider_plugin = cast(
        QualityProviderPlugin | None,
        get_global_registry().get("quality_provider", name),
    )
    if provider_plugin is None:
        raise _missing_quality_provider_error(name)
    return provider_plugin


def provider(name: str) -> Callable:
    """Return the decorator factory for a named quality provider."""
    return _quality_provider_or_raise(name).get_decorator()


def pandera(*args: Any, **kwargs: Any) -> Any:
    """Return the Pandera quality decorator factory."""
    return provider("pandera")(*args, **kwargs)


def rules(
    *,
    table: str,
    rules: list[Any],
    provider_name: str = "pandera",
    **kwargs: Any,
) -> Callable:
    """Build a quality decorator from provider-neutral rules."""
    provider_plugin = _quality_provider_or_raise(provider_name)
    native_checks = provider_plugin.build_checks_from_rules(rules)
    if native_checks is None:
        raise ValueError(
            f"Quality provider '{provider_name}' cannot translate neutral quality rules"
        )
    return provider_plugin.get_decorator()(table=table, checks=native_checks, **kwargs)


def __getattr__(name: str) -> Any:
    """Lazily hydrate provider-backed public exports."""
    if name in _QUALITY_EXPORTS:
        _ensure_quality_provider_loaded()
        return globals()[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "pandera",
    "phlo_quality",
    "provider",
    "providers",
    "rules",
    "get_quality_checks",
    "clear_quality_checks",
    "QualityCheck",
    "NullCheck",
    "RangeCheck",
    "FreshnessCheck",
    "UniqueCheck",
    "CountCheck",
    "SchemaCheck",
    "CustomSQLCheck",
    "PatternCheck",
    "ReconciliationCheck",
    "AggregateConsistencyCheck",
    "AggregateSpec",
    "KeyParityCheck",
    "MultiAggregateConsistencyCheck",
    "ChecksumReconciliationCheck",
    "PANDERA_CONTRACT_CHECK_NAME",
    "QualityCheckContract",
    "dbt_check_name",
]
