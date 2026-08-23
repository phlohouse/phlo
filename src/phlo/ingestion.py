"""Backward-compatible DLT ingestion alias.

New code should prefer ``phlo.ingest.dlt`` or ``phlo.ingest.provider(name)``.
This module remains callable so existing ``@phlo.ingestion(...)`` workflows keep
working while the public API moves toward provider-neutral ingestion.
"""

from __future__ import annotations

import sys
from types import ModuleType
from typing import Any


def phlo_ingestion(*args: Any, **kwargs: Any) -> Any:
    """Return the DLT ingestion decorator for compatibility."""
    from phlo import ingest

    return ingest.dlt(*args, **kwargs)


def get_ingestion_assets() -> list[Any]:
    """Return registered DLT ingestion assets for compatibility."""
    from phlo import ingest

    return ingest.assets("dlt")


class _CallableIngestionModule(ModuleType):
    """Module type that lets ``phlo.ingestion(...)`` call the DLT alias."""

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return phlo_ingestion(*args, **kwargs)


# Re-class the module object so ``phlo.ingestion(...)`` keeps working as a call
# while the name still resolves to a normal module with attributes. Only
# subclasses of ModuleType support this __class__ assignment.
sys.modules[__name__].__class__ = _CallableIngestionModule

__all__ = ["get_ingestion_assets", "phlo_ingestion"]
