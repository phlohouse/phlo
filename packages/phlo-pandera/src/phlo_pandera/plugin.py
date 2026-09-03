"""Pandera quality provider plugin.

This module implements the PanderaQualityProvider plugin class that integrates
the Phlo Quality Framework with Phlo's plugin system. The provider exposes:

1. **Decorator**: The ``@phlo_pandera`` decorator for declarative quality checks
2. **Check Classes**: All built-in quality check implementations
3. **Schema Extractor**: PanderaSchemaExtractor for schema normalization
4. **Reconciliation Checks**: Cross-table validation checks

The plugin is registered via the ``phlo.quality_providers`` entry point and is
discovered automatically by Phlo's plugin system.

Example:
    The plugin is typically not used directly. Instead, users interact with
    the public API from ``phlo_pandera``:

    ```python
    from phlo_pandera import NullCheck, RangeCheck, phlo_pandera

    @phlo_pandera(
        table="bronze.events",
        checks=[
            NullCheck(columns=["event_id"]),
            RangeCheck(column="value", min_value=0, max_value=100),
        ],
    )
    def event_quality():
        pass
    ```

Plugin Registration:
    The plugin is registered in ``pyproject.toml``:

    ```toml
    [project.entry-points."phlo.quality_providers"]
    pandera = "phlo_pandera.plugin:PanderaQualityProvider"
    ```

See Also:
    - ``__init__.py``: Public API exports
    - ``decorator.py``: ``@phlo_pandera`` implementation
    - ``checks.py``: Core quality check classes
    - ``reconciliation.py``: Cross-table reconciliation checks


Loaded through the phlo plugin entry-point mechanism at startup rather than imported
directly; contributes the Pandera quality and schema-discovery providers.
"""

from __future__ import annotations

import hashlib
import importlib.util
import inspect
import os
from pathlib import Path
from typing import Any, Callable

from phlo.capabilities import (
    EvidenceProfileContributionSpec,
    SchemaDiscoverySpec,
    WorkflowValidationSpec,
    WorkflowContributionMode,
    WorkflowWizardContribution,
    WorkflowWizardField,
)
from phlo.plugins.base import PluginMetadata, QualityProviderPlugin


def get_workflow_wizard_contributions() -> list[WorkflowWizardContribution]:
    """Return provider-neutral workflow wizard contributions for Pandera."""

    return [
        WorkflowWizardContribution(
            id="pandera.quality-checks",
            package="phlo-pandera",
            stage="quality",
            label="Pandera quality checks",
            description="Generate table quality checks for schema, uniqueness, nulls, ranges, and freshness.",
            required_capabilities=["quality_backend"],
            fields=[
                WorkflowWizardField(
                    name="target_table",
                    label="Target table",
                    required=True,
                    description="Table or model relation to validate.",
                ),
                WorkflowWizardField(
                    name="check_name",
                    label="Check name",
                    required=True,
                    description="Generated Python function name for the quality check.",
                ),
                WorkflowWizardField(
                    name="unique_key",
                    label="Unique key",
                    required=True,
                    default="id",
                    description="Column expected to be unique and present.",
                ),
                WorkflowWizardField(
                    name="not_null_columns",
                    label="Not-null columns",
                    field_type="fields",
                    required=False,
                    description="One column per line that must not contain nulls.",
                ),
                WorkflowWizardField(
                    name="range_checks",
                    label="Range checks",
                    field_type="fields",
                    required=False,
                    description="Optional checks as column:min:max.",
                ),
                WorkflowWizardField(
                    name="freshness_column",
                    label="Freshness column",
                    required=False,
                    description="Optional timestamp column to monitor for freshness.",
                ),
                WorkflowWizardField(
                    name="freshness_hours",
                    label="Freshness hours",
                    required=False,
                    default="24",
                    description="Maximum age for the freshness column.",
                ),
                WorkflowWizardField(
                    name="min_rows",
                    label="Minimum rows",
                    required=False,
                    default="1",
                    description="Minimum row count expected after ingestion and transform.",
                ),
            ],
            modes={WorkflowContributionMode.PROPOSAL, WorkflowContributionMode.APPLY},
            metadata={"generator": "phlo-api workflow wizard Pandera scaffold"},
        )
    ]


class PanderaQualityProvider(QualityProviderPlugin):
    def get_evidence_profile_contributions(self) -> list[EvidenceProfileContributionSpec]:
        """Declare this provider's blessed run-evidence contribution."""
        from phlo.run_evidence.profiles import EvidenceProfileContribution
        from phlo.run_evidence.reconciliation import RequiredEvidenceRecord, RequiredEvidenceStage

        contribution = EvidenceProfileContribution(
            contribution_id="pandera.check",
            provider="pandera",
            profile_id="wap",
            profile_version="1",
            stages=(RequiredEvidenceStage(stage_type="check", provider="pandera"),),
            required_records=(RequiredEvidenceRecord(family="quality_result", minimum=1),),
        )
        return [EvidenceProfileContributionSpec(name="pandera.check", provider=contribution)]

    """Pandera-based quality provider for Phlo.

    This plugin class integrates the Phlo Quality Framework with Phlo's plugin
    system. It provides access to all quality check classes, the ``@phlo_pandera``
    decorator, schema extraction, and reconciliation checks.

    The plugin is automatically discovered by Phlo's plugin system via the
    ``phlo.quality_providers`` entry point.

    Example:
        The plugin is typically accessed through the plugin system:

        ```python
        from phlo.plugins.discovery import get_global_registry

        registry = get_global_registry()
        pandera_plugin = registry.get("quality_provider", "pandera")

        # Access decorator
        decorator = pandera_plugin.get_decorator()

        # Get check classes
        checks = pandera_plugin.get_check_classes()
        null_check_class = checks.get("null")
        ```

    """

    @property
    def metadata(self) -> PluginMetadata:
        """Return plugin metadata used during registration and discovery."""
        return PluginMetadata(
            name="pandera",
            version="0.1.0",
            description="Pandera-based quality provider with schema validation and checks",
        )

    def get_decorator(self) -> Callable:
        """Return the @phlo_pandera decorator, the main entry point for
        defining quality checks declaratively.

        Example:
            ```python
            provider = PanderaQualityProvider()
            phlo_pandera = provider.get_decorator()

            @phlo_pandera(table="bronze.events", checks=[...])
            def quality_check():
                pass
            ```

        """
        from phlo_pandera import phlo_pandera

        return phlo_pandera

    def get_check_classes(self) -> dict[str, type]:
        """Return built-in check classes as a name-to-class mapping covering
        null, range, freshness, unique, count, schema, pattern, and the
        quality_check base class.

        Example:
            ```python
            provider = PanderaQualityProvider()
            checks = provider.get_check_classes()

            NullCheck = checks["null"]
            RangeCheck = checks["range"]
            ```

        """
        from phlo_pandera import (
            CountCheck,
            FreshnessCheck,
            NullCheck,
            PatternCheck,
            QualityCheck,
            RangeCheck,
            SchemaCheck,
            UniqueCheck,
        )

        return {
            "null": NullCheck,
            "range": RangeCheck,
            "freshness": FreshnessCheck,
            "unique": UniqueCheck,
            "count": CountCheck,
            "schema": SchemaCheck,
            "pattern": PatternCheck,
            "quality_check": QualityCheck,
        }

    def get_schema_extractor(self) -> Any:
        """Return Pandera schema extractor.

        Returns the extractor class (not an instance) used to convert
        Pandera DataFrameModel schemas into normalized schemas for storage
        provider integration.

        Example:
            ```python
            provider = PanderaQualityProvider()
            Extractor = provider.get_schema_extractor()

            extractor = Extractor()
            normalized_schema = extractor.extract(MyPanderaSchema)
            ```

        """
        from phlo_pandera import PanderaSchemaExtractor

        return PanderaSchemaExtractor

    def get_workflow_validators(self) -> list[WorkflowValidationSpec]:
        """Expose Pandera validation through the neutral CLI capability."""
        return [WorkflowValidationSpec(name="pandera", provider=PanderaWorkflowValidator())]

    def get_schema_discovery_providers(self) -> list[SchemaDiscoverySpec]:
        """Expose Pandera schema discovery through the neutral CLI capability."""
        return [SchemaDiscoverySpec(name="pandera", provider=PanderaSchemaDiscoveryProvider())]

    def get_schema_base_import(self) -> tuple[str, str]:
        """Return the Phlo schema base class used by generated project schemas."""
        return ("phlo_pandera.schemas", "PhloSchema")

    def render_schema_field(
        self,
        *,
        name: str,
        type_name: str,
        nullable: bool,
        description: str | None = None,
    ) -> str:
        """Render a Pandera schema field for generated project schemas."""
        description_arg = f'description="{description}", ' if description else ""
        return f"    {name}: Series[{type_name}] = pa.Field({description_arg}nullable={nullable})"

    def render_schema_module(
        self,
        *,
        domain: str,
        schema_class: str,
        type_imports: str,
        schema_fields: str,
    ) -> str:
        """Render a Pandera-backed schema module for generated project schemas."""
        return f'''"""
Pandera schemas for {domain} domain.

Extend this schema with additional fields as you stabilize the source contract.
"""

import pandera as pa
from pandera.typing import Series
from phlo_pandera.schemas import PhloSchema

{type_imports}class {schema_class}(PhloSchema):
    """Raw {domain} {schema_class} records."""

{schema_fields}

    class Config:
        strict = False
        coerce = True
'''

    def get_reconciliation_checks(self) -> dict[str, type] | None:
        """Return reconciliation check classes.

        Returns a name-to-class mapping of checks that validate data
        consistency across tables: reconciliation (row count parity),
        aggregate_consistency, aggregate_spec, key_parity, multi_aggregate,
        and checksum.

        Example:
            ```python
            provider = PanderaQualityProvider()
            reconciliations = provider.get_reconciliation_checks()

            ReconciliationCheck = reconciliations["reconciliation"]
            KeyParityCheck = reconciliations["key_parity"]
            ```

        """
        from phlo_pandera import (
            AggregateConsistencyCheck,
            AggregateSpec,
            ChecksumReconciliationCheck,
            KeyParityCheck,
            MultiAggregateConsistencyCheck,
            ReconciliationCheck,
        )

        return {
            "reconciliation": ReconciliationCheck,
            "aggregate_consistency": AggregateConsistencyCheck,
            "aggregate_spec": AggregateSpec,
            "key_parity": KeyParityCheck,
            "multi_aggregate": MultiAggregateConsistencyCheck,
            "checksum": ChecksumReconciliationCheck,
        }

    def build_checks_from_rules(self, rules: list[Any]) -> list[Any]:
        """Translate provider-neutral QualityRule descriptors into Pandera checks."""
        from phlo.helpers.sql import literal, quote_identifier
        from phlo_pandera.checks import FreshnessCheck, NullCheck, RangeCheck, UniqueCheck
        from phlo_pandera.checks_extra import CustomSQLCheck

        checks: list[Any] = []
        for rule in rules:
            if rule.kind == "not_null":
                checks.append(NullCheck(columns=rule.columns))
            elif rule.kind == "unique":
                checks.append(UniqueCheck(columns=rule.columns))
            elif rule.kind == "freshness":
                checks.append(
                    FreshnessCheck(
                        timestamp_column=rule.columns[0],
                        max_age_hours=rule.parameters["max_age_hours"],
                    )
                )
            elif rule.kind == "range":
                checks.append(
                    RangeCheck(
                        column=rule.columns[0],
                        min_value=rule.parameters.get("min_value"),
                        max_value=rule.parameters.get("max_value"),
                    )
                )
            elif rule.kind == "accepted_values":
                values = rule.parameters["values"]
                column = rule.columns[0]
                quoted_column = quote_identifier(column)
                quoted_values = ", ".join(literal(value) for value in values)
                checks.append(
                    CustomSQLCheck(
                        name_=f"{column}_accepted_values",
                        sql=f"SELECT ({quoted_column} IN ({quoted_values})) AS is_valid FROM data",
                    )
                )
            else:
                raise ValueError(f"Unsupported neutral quality rule: {rule.kind}")
        return checks


class PanderaWorkflowValidator:
    """Validate workflow and schema files with Pandera's existing CLI helpers."""

    def validate_workflow_file(self, path: Path) -> None:
        """Validate a workflow file's quality checks, failing when the file
        does not define a workflow."""
        from phlo_pandera.cli_validate import validate_workflow_file

        validate_workflow_file(path, require_workflow=True)

    def validate_schema_file(self, path: Path) -> None:
        """Validate that a schema file parses as a Pandera schema module."""
        from phlo_pandera.cli_schema_utils import validate_schema_file

        validate_schema_file(path)


class PanderaSchemaDiscoveryProvider:
    """Discover and normalize Pandera schemas for schema migration commands."""

    def extract(self, native_schema: Any) -> Any:
        """Extract and normalize a Pandera schema into Phlo's schema form."""
        from phlo_pandera.schema_extractor import PanderaSchemaExtractor

        return PanderaSchemaExtractor().extract(native_schema)

    def discover_schemas(self) -> dict[str, Any]:
        """Return discovered Pandera schemas by name, merging registry entries
        with schemas found under project search paths."""
        from phlo_pandera.cli_schema_utils import discover_pandera_schemas

        schemas = discover_pandera_schemas()
        for name, schema in self._discover_schemas_from_files().items():
            schemas.setdefault(name, schema)
        return schemas

    @staticmethod
    def _discover_schemas_from_files() -> dict[str, type[Any]]:
        from pandera.pandas import DataFrameModel

        env_paths = os.getenv("PHLO_SCHEMA_SEARCH_PATHS")
        if env_paths:
            search_paths = [Path(path.strip()) for path in env_paths.split(",") if path.strip()]
        else:
            project_root = os.getenv("PHLO_PROJECT_PATH")
            root = Path(project_root) if project_root else Path()
            search_paths = [root / "examples", root / "workflows"]

        discovered: dict[str, type[Any]] = {}
        for root in search_paths:
            if not root.exists():
                continue
            for schema_file in root.glob("**/schemas/*.py"):
                if schema_file.name.startswith("_"):
                    continue
                module_name = (
                    "phlo_schema_fallback_"
                    f"{hashlib.sha256(str(schema_file.resolve()).encode()).hexdigest()[:16]}"
                )
                spec = importlib.util.spec_from_file_location(module_name, schema_file)
                if spec is None or spec.loader is None:
                    continue
                module = importlib.util.module_from_spec(spec)
                try:
                    spec.loader.exec_module(module)
                except Exception:
                    continue
                for name, obj in inspect.getmembers(module, inspect.isclass):
                    if issubclass(obj, DataFrameModel) and obj is not DataFrameModel:
                        discovered[name] = obj
        return discovered
