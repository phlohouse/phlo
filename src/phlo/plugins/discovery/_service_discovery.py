"""Compatibility exports for service discovery.

Re-exports ServiceDiscovery from phlo.plugins.discovery.services for the
legacy import path; the implementation lives in the services module.

Legacy import shim: retained for compatibility while the implementation lives in
phlo.plugins.discovery.services.
"""

from __future__ import annotations

from phlo.plugins.discovery.services import ServiceDiscovery

__all__ = ["ServiceDiscovery"]
