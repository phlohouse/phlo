"""Phlo data lineage tracking and visualization package.

This package provides comprehensive data lineage tracking capabilities for Phlo,
including row-level provenance, column-level mappings, and graph-based asset
dependency visualization.

Modules:
    graph: LineageGraph implementation for asset dependency analysis.
    store: PostgreSQL-backed persistence for row and column lineage.
    settings: Configuration management for lineage features.
    dbt_column_lineage: dbt manifest extraction for column-level lineage.
    lineage_sink: Capability provider for the lineage system.
    hooks_plugin: Event-driven lineage graph updates.
    cli_lineage: CLI commands for lineage visualization and analysis.
    cli_plugin: CLI command registration.
    observatory_plugin: Observatory UI extension for lineage graphs.
    resource_provider: Capability provider for the plugin system.

Example:
    >>> from phlo_lineage import LineageStore, get_lineage_graph
    >>> store = LineageStore("postgresql://...")
    >>> graph = get_lineage_graph()

See Also:
    - Documentation: docs/reference/packages.md
    - Repository: https://github.com/phlohouse/phlo

"""

from phlo_lineage.graph import LineageGraph, get_lineage_graph
from phlo_lineage.store import ColumnLineage, LineageStore, generate_row_id
from phlo_lineage.settings import LineageSettings, get_settings

__all__ = [
    "ColumnLineage",
    "LineageGraph",
    "get_lineage_graph",
    "LineageStore",
    "generate_row_id",
    "LineageSettings",
    "get_settings",
]
