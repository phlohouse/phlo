"""Governance read models derived from Phlo declarations.

Re-exports the governance surface built by phlo.governance.surface: read
only, no mutation of declarations happens here.
"""

from phlo.governance.surface import (
    GovernanceSurface,
    GovernanceWarning,
    GovernedTable,
    build_governance_surface,
)

__all__ = [
    "GovernedTable",
    "GovernanceSurface",
    "GovernanceWarning",
    "build_governance_surface",
]
