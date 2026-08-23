"""Schema contract refresh integration for Dagster materialization flows.

This module provides automatic schema contract refresh functionality
that integrates with Dagster asset materialization. When enabled via
environment variables, it refreshes Pandera schema contracts before
materializing assets to ensure data contracts stay synchronized with
the actual data.

Environment Variables:
    PHLO_AUTO_REFRESH_CONTRACTS: Enable automatic refresh (1/true/yes)
    PHLO_CONTRACT_REFRESH_SELECTION: Asset selection for contract refresh

Integration Point:
    Called during framework definitions building, before user workflows
    are discovered. This ensures contracts are fresh before any
    materialization occurs.

Schema Contract Purpose:
    Pandera schema contracts define expected data schemas and
    validation rules. Keeping them synchronized with actual table
    schemas helps catch schema drift and maintain data quality.

Example:
    Enabling auto-refresh::

        export PHLO_AUTO_REFRESH_CONTRACTS=1
        export PHLO_CONTRACT_REFRESH_SELECTION="tag:bronze"

        phlo materialize my_asset
        # Contracts will be refreshed before materialization

Sits at the materialization boundary: refreshes contracts ahead of asset runs,
building on phlo.cli.commands.schema_migrate, with no other module importing it.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any


def maybe_refresh_contracts(workflows_path: Path, logger: Any) -> None:
    """Refresh schema contracts when explicitly enabled via env vars.

    Resolves contracts against workflows_path; failures are logged as warnings
    rather than raised.
    """
    enabled = os.getenv("PHLO_AUTO_REFRESH_CONTRACTS", "").strip().lower()
    if enabled not in {"1", "true", "yes"}:
        return

    selection = os.getenv("PHLO_CONTRACT_REFRESH_SELECTION")

    try:
        from phlo.cli.commands.schema_migrate import refresh_contracts_for_selection
    except Exception:
        logger.warning(
            "schema_contract_refresh_unavailable",
            workflows_path=str(workflows_path),
            selection=selection,
            exc_info=True,
        )
        return

    try:
        refreshed_count = refresh_contracts_for_selection(selection=selection, force=True)
    except Exception:
        logger.warning(
            "schema_contract_refresh_failed",
            workflows_path=str(workflows_path),
            selection=selection,
            exc_info=True,
        )
        return

    logger.info(
        "schema_contract_refresh_completed",
        workflows_path=str(workflows_path),
        selection=selection,
        refreshed_count=refreshed_count,
    )
