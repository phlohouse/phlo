"""Phlo Polaris catalog package.

Provides the Apache Polaris Iceberg REST catalog service, snapshot-based WAP
promotion, Trino catalog properties, PyIceberg configuration, the bootstrap
hook, and the Nessie migration command.
"""

from importlib.metadata import version

from phlo_polaris.plugin import PolarisServicePlugin

__all__ = ["PolarisServicePlugin"]
__version__ = version("phlo-polaris")
