"""Resource provider for ClickStack observability capabilities.

ClickStackResourceProvider registers under the "clickstack" name and reports
ClickStack as an observability backend; it declares no standalone resources.
Loaded through the phlo plugin entry-point mechanism at startup rather than
imported directly; declares ClickStack as an observability backend via phlo.plugins.
"""

from __future__ import annotations

from phlo.plugins import PluginMetadata, ResourceProviderPlugin

from phlo_clickstack.observability_backend import build_clickstack_observability_spec


class ClickStackResourceProvider(ResourceProviderPlugin):
    """Expose ClickStack as the default observability backend capability."""

    @property
    def metadata(self) -> PluginMetadata:
        """Return the ClickStack provider metadata."""
        return PluginMetadata(
            name="clickstack",
            version="0.1.0",
            description="ClickStack observability capability provider",
            tags=["observability", "logs", "metrics", "traces"],
        )

    def get_resources(self) -> list:
        """Report no standalone resources."""
        return []

    def get_observability_backends(self):
        """Return the ClickStack observability backend spec."""
        return [build_clickstack_observability_spec()]
