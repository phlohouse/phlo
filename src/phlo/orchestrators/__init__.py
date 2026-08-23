"""Orchestrator adapter selection.

Re-exports get_active_orchestrator as the single entry point for
resolving which orchestrator backend the project uses.
"""

from phlo.orchestrators.selection import get_active_orchestrator

__all__ = ["get_active_orchestrator"]
