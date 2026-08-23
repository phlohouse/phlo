"""Orchestrator adapter plugin classes.

This module defines plugin types for orchestrator integration.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable
from typing import Any

from phlo.capabilities.specs import AssetCheckSpec, AssetSpec, ResourceSpec
from phlo.plugins.base.plugin import Plugin


class OrchestratorAdapterPlugin(Plugin, ABC):
    """Base class for orchestrator adapters."""

    def exec_service_name(self) -> str | None:
        """Return the primary service name for container-based CLI execution.

        Adapters that expose a long-running service container users can exec into
        should override this method. Adapters without a corresponding container
        can return ``None`` and callers should fall back to host execution.
        """
        return None

    @abstractmethod
    def build_definitions(
        self,
        *,
        assets: Iterable[AssetSpec],
        checks: Iterable[AssetCheckSpec],
        resources: Iterable[ResourceSpec],
    ) -> Any:
        """Build orchestrator-native definitions from normalized capability specs.

        Registers asset specs, asset-check specs, and the resources they require.
        """
        raise NotImplementedError
