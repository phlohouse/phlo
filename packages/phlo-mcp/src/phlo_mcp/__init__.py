"""Phlo MCP package root.

Exposes only the package version; implementation lives in submodules such as
server, models, and config.
"""

__all__ = ["__version__"]

from importlib.metadata import version

__version__ = version("phlo-mcp")
