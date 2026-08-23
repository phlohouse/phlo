"""Pgweb package for Phlo: a web-based PostgreSQL browser service plugin,
managed through Phlo's plugin system.

Example:
    The plugin is automatically discovered by Phlo's plugin system:

    >>> from phlo.plugins import discover_plugins
    >>> plugins = discover_plugins()
    >>> pgweb = plugins.get("pgweb")

"""

__version__ = "0.1.0"
