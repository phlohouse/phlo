"""Observatory service plugin registration.

Same service_plugin_class contract as before; build context moved to web/.
"""

from __future__ import annotations

from phlo.plugins import service_plugin_class


ObservatoryServicePlugin = service_plugin_class(
    "ObservatoryServicePlugin",
    name="observatory",
    version="0.1.0",
    description="Phlo Observatory UI",
    author="Phlo Team",
    tags=["ui", "observability"],
)
