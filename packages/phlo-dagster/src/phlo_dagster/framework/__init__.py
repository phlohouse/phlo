"""Dagster framework helpers for Phlo projects.

This module provides the main entry point for Dagster-based Phlo projects.
It exports the core definitions building functionality that discovers and
loads user workflows from the configured workflows directory.

Exported Components:
    - build_definitions: Function to build merged Dagster definitions from
      user workflows and framework resources
    - defs: Global Definitions instance for Dagster to load

Architecture:
    The framework module serves as the bridge between Phlo's capability-based
    plugin system and Dagster's execution model. It handles:
    - Workflow discovery from user project directories
    - Resource injection and configuration
    - WAP (Write-Audit-Publish) sensor registration
    - Executor selection based on platform

Usage:
    In workspace.yaml::

        load_from:
          - python_module:
              module_name: phlo_dagster.framework.definitions

    Or programmatically::

        from phlo_dagster.framework import build_definitions

        defs = build_definitions(workflows_path="custom_workflows")

Placement: this module is the Dagster code location for Phlo projects; it is
loaded by Dagster via workspace.yaml load_from rather than imported by other
phlo modules.
"""

__all__ = ["build_definitions", "defs"]


def __getattr__(name: str):
    """Load framework definitions only when the public facade is requested.

    Adapter imports use framework submodules such as ``asset_diagnostics``.
    Eagerly importing definitions here starts plugin discovery while the
    adapter is still being imported, which makes its entry point partial.
    """
    if name not in __all__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    from phlo_dagster.framework.definitions import build_definitions, defs

    return {"build_definitions": build_definitions, "defs": defs}[name]
