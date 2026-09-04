"""Phlo - A modern data lakehouse platform.

Phlo is a decorator-driven data lakehouse framework that combines Apache Iceberg,
Project Nessie, Trino, dbt, and Dagster into an integrated platform.

This package provides the core Phlo API with lazy-loaded exports to avoid
circular dependencies during plugin discovery. All major functionality is
available through this top-level module.

Key Features:
    - Write-Audit-Publish pattern with Git-like branching
    - Type-safe data quality with automatic validation
    - Production-oriented building blocks; v1 production readiness remains gated
    - Schema-first development with Pandera

Lazy-Loaded Modules:
    The following modules are loaded on first access to avoid circular imports:
    - ``phlo.ingestion``: Data ingestion operations
    - ``phlo.quality``: Data quality validation
    - ``phlo.metrics``: Platform metrics collection

Direct Exports:
    - :class:`Consumer`: Data consumer contract
    - :class:`SLA`: Service level agreement contract
    - :func:`phlo_ingestion`: Ingestion decorator
    - :func:`get_ingestion_assets`: Retrieve ingestion assets
    - :func:`phlo_quality`: Quality decorator (deprecated third name; use ``phlo.quality.pandera``)
    - :func:`get_quality_checks`: Retrieve quality checks
    - Quality check classes: NullCheck, RangeCheck, FreshnessCheck, etc.

Plugin Entry Points:
    Phlo uses the following entry point groups for plugin discovery:
    - ``phlo.sources``: Data source connectors
    - ``phlo.quality``: Quality check implementations
    - ``phlo.ingestion_providers``: Ingestion providers
    - ``phlo.transformation_providers``: Transformation providers
    - ``phlo.transforms``: Data transformation tools
    - ``phlo.services``: Infrastructure services
    - ``phlo.cli_commands``: CLI command extensions
    - ``phlo.hooks``: Hook handlers
    - ``phlo.catalogs``: Metadata catalogs
    - ``phlo.asset_providers``: Asset definitions
    - ``phlo.resource_providers``: Resource definitions
    - ``phlo.orchestrators``: Orchestrator adapters

Version Information:
    - ``__version__``: Current Phlo version string

Example:
    ```python
    import phlo

    # Access ingestion decorator
    @phlo.ingest.dlt(source="api", table_name="events")
    def load_events():
        return fetch_events()

    # Access quality decorator
    @phlo.quality.pandera(schema=UserSchema)
    def validate_users():
        return load_users()

    # Access quality check classes
    from phlo import NullCheck, RangeCheck
    ```

See Also:
    - Documentation: https://docs.phlo.dev
    - Repository: https://github.com/phlohouse/phlo
    - Plugin API: :mod:`phlo.plugins.base`
    - Configuration: :mod:`phlo.config`

Note:
    This module uses ``__getattr__`` for lazy loading to prevent circular
    imports during plugin discovery. All public exports are listed in ``__all__``.

"""

from __future__ import annotations

from importlib import import_module
from importlib.metadata import version
from typing import Any

__version__ = version("phlo")

_INGESTION_EXPORTS = {"phlo_ingestion", "get_ingestion_assets"}
_CONTRACT_EXPORTS = {"Consumer", "SLA"}
_QUALITY_EXPORTS = {
    "phlo_quality",
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
_QUALITY_RULE_EXPORTS = {
    "accepted_values",
    "freshness",
    "not_null",
    "range_between",
    "unique",
}
_FLOW_EXPORTS = {
    "access",
    "backfill",
    "clear_access_policies",
    "clear_backfill_assets",
    "clear_contract_specs",
    "clear_flow_declarations",
    "clear_observe_assets",
    "clear_publish_assets",
    "clear_schedules",
    "contract",
    "get_access_policies",
    "get_backfill_assets",
    "get_contract_specs",
    "get_observe_assets",
    "get_publish_assets",
    "get_schedules",
    "observe",
    "publish",
    "schedule",
}
_CONFIG_EXPORTS = {"settings"}
_REFERENCE_EXPORTS = {"LogicalRelation", "quote_identifier", "ref", "source"}
_SUBMODULE_EXPORTS = {"helpers", "ingest", "ingestion", "metrics", "quality", "transform"}
_HELPER_EXPORTS = {"read_dataframe", "synthetic_key"}

__all__ = [
    "__version__",
    *_SUBMODULE_EXPORTS,
    *_HELPER_EXPORTS,
    *_CONTRACT_EXPORTS,
    *_INGESTION_EXPORTS,
    *_QUALITY_EXPORTS,
    *_QUALITY_RULE_EXPORTS,
    *_FLOW_EXPORTS,
    *_CONFIG_EXPORTS,
    *_REFERENCE_EXPORTS,
]


def __getattr__(name: str) -> Any:
    """Resolve top-level exports without importing optional packages eagerly.

    Raises: AttributeError when the attribute is not exported by this module.

    """

    if name in _SUBMODULE_EXPORTS:
        module = import_module(f"{__name__}.{name}")
        globals()[name] = module
        return module
    if name in _HELPER_EXPORTS:
        from phlo.helpers import read_dataframe, synthetic_key

        globals().update({"read_dataframe": read_dataframe, "synthetic_key": synthetic_key})
        return globals()[name]
    if name in _CONTRACT_EXPORTS:
        from phlo.contracts import SLA, Consumer

        globals().update({"Consumer": Consumer, "SLA": SLA})
        return globals()[name]
    if name in _INGESTION_EXPORTS:
        from phlo.ingestion import get_ingestion_assets, phlo_ingestion

        globals().update(
            {
                "get_ingestion_assets": get_ingestion_assets,
                "phlo_ingestion": phlo_ingestion,
            }
        )
        return globals()[name]
    if name in _QUALITY_RULE_EXPORTS:
        from phlo.quality_rules import accepted_values, freshness, not_null, range_between, unique

        globals().update(
            {
                "accepted_values": accepted_values,
                "freshness": freshness,
                "not_null": not_null,
                "range_between": range_between,
                "unique": unique,
            }
        )
        return globals()[name]
    if name in _FLOW_EXPORTS:
        from phlo.flow import (
            access,
            backfill,
            clear_access_policies,
            clear_backfill_assets,
            clear_contract_specs,
            clear_flow_declarations,
            clear_observe_assets,
            clear_publish_assets,
            clear_schedules,
            contract,
            get_access_policies,
            get_backfill_assets,
            get_contract_specs,
            get_observe_assets,
            get_publish_assets,
            get_schedules,
            observe,
            publish,
            schedule,
        )

        globals().update(
            {
                "access": access,
                "backfill": backfill,
                "clear_access_policies": clear_access_policies,
                "clear_backfill_assets": clear_backfill_assets,
                "clear_contract_specs": clear_contract_specs,
                "clear_flow_declarations": clear_flow_declarations,
                "clear_observe_assets": clear_observe_assets,
                "clear_publish_assets": clear_publish_assets,
                "clear_schedules": clear_schedules,
                "contract": contract,
                "get_access_policies": get_access_policies,
                "get_backfill_assets": get_backfill_assets,
                "get_contract_specs": get_contract_specs,
                "get_observe_assets": get_observe_assets,
                "get_publish_assets": get_publish_assets,
                "get_schedules": get_schedules,
                "observe": observe,
                "publish": publish,
                "schedule": schedule,
            }
        )
        return globals()[name]
    if name in _CONFIG_EXPORTS:
        from phlo.config.workflow import workflow_settings

        globals()["settings"] = workflow_settings
        return globals()[name]
    if name in _REFERENCE_EXPORTS:
        from phlo.references import LogicalRelation, quote_identifier, ref, source

        globals().update(
            {
                "LogicalRelation": LogicalRelation,
                "quote_identifier": quote_identifier,
                "ref": ref,
                "source": source,
            }
        )
        return globals()[name]
    if name in _QUALITY_EXPORTS:
        from phlo.quality import (
            PANDERA_CONTRACT_CHECK_NAME,
            AggregateConsistencyCheck,
            AggregateSpec,
            ChecksumReconciliationCheck,
            CountCheck,
            CustomSQLCheck,
            FreshnessCheck,
            KeyParityCheck,
            MultiAggregateConsistencyCheck,
            NullCheck,
            PatternCheck,
            QualityCheck,
            QualityCheckContract,
            RangeCheck,
            ReconciliationCheck,
            SchemaCheck,
            UniqueCheck,
            clear_quality_checks,
            dbt_check_name,
            get_quality_checks,
            phlo_quality,
        )

        globals().update(
            {
                "AggregateConsistencyCheck": AggregateConsistencyCheck,
                "AggregateSpec": AggregateSpec,
                "ChecksumReconciliationCheck": ChecksumReconciliationCheck,
                "CountCheck": CountCheck,
                "CustomSQLCheck": CustomSQLCheck,
                "FreshnessCheck": FreshnessCheck,
                "KeyParityCheck": KeyParityCheck,
                "MultiAggregateConsistencyCheck": MultiAggregateConsistencyCheck,
                "NullCheck": NullCheck,
                "PANDERA_CONTRACT_CHECK_NAME": PANDERA_CONTRACT_CHECK_NAME,
                "PatternCheck": PatternCheck,
                "QualityCheck": QualityCheck,
                "QualityCheckContract": QualityCheckContract,
                "RangeCheck": RangeCheck,
                "ReconciliationCheck": ReconciliationCheck,
                "SchemaCheck": SchemaCheck,
                "UniqueCheck": UniqueCheck,
                "clear_quality_checks": clear_quality_checks,
                "dbt_check_name": dbt_check_name,
                "get_quality_checks": get_quality_checks,
                "phlo_quality": phlo_quality,
            }
        )
        return globals()[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    """Return the list of available attributes for dir()."""
    return sorted(set(__all__))
