"""Observatory service plugin registration.

Declares the Observatory UI as a service plugin tagged for
observability. The class object is created at import time via
service_plugin_class so plugin discovery can pick it up without
instantiation.

Loaded through the phlo plugin entry-point mechanism at startup rather than
imported directly.
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
