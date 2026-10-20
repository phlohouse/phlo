"""Phlo Transform integration for Phlo.

Provides the ``transform`` asset and transformation providers, the
``phlo transform`` CLI, and typed access to a Phlo Transform workspace. The
Rust engine is the source of truth for compilation, planning, execution, and
tests; this package adapts its JSON reports to Phlo capability specs.
"""

from __future__ import annotations

from importlib.metadata import version

from phlo_transform.assets import build_transform_asset_specs
from phlo_transform.workspace import TransformWorkspace, load_workspace

__all__ = [
    "TransformWorkspace",
    "__version__",
    "build_transform_asset_specs",
    "load_workspace",
]

__version__ = version("phlo-transform")
