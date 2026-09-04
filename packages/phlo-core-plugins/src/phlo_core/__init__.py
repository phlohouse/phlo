"""Core plugins for Phlo: quality checks (null, uniqueness, freshness, schema)
and the generic REST API source connector, bundled and automatically available
with Phlo.

Example:
    To use these plugins in your Phlo project::

        from phlo_core import NullCheckPlugin, RestAPIPlugin

        null_check = NullCheckPlugin()
        rest_source = RestAPIPlugin()

"""

from phlo_core.quality.freshness_check import FreshnessCheckPlugin
from phlo_core.quality.null_check import NullCheckPlugin
from phlo_core.quality.schema_check import SchemaCheckPlugin
from phlo_core.quality.uniqueness_check import UniquenessCheckPlugin
from phlo_core.sources.rest_api import RestAPIPlugin

__all__ = [
    "NullCheckPlugin",
    "UniquenessCheckPlugin",
    "FreshnessCheckPlugin",
    "SchemaCheckPlugin",
    "RestAPIPlugin",
]
from importlib.metadata import version

__version__ = version("phlo-core-plugins")
