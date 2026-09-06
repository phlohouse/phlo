"""Phlo Airbyte integration package.

Provides the self-managed Airbyte service descriptor, the Configuration API
client, and the ``AirbyteConnectionAsset`` that runs one Airbyte sync inside
Phlo's Dagster-owned lifecycle with job-state evidence.
"""

from importlib.metadata import version

from phlo_airbyte.plugin import AirbyteServicePlugin

__all__ = ["AirbyteServicePlugin"]
__version__ = version("phlo-airbyte")
