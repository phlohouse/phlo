"""Deprecated DLT ingestion compatibility alias.

New code must use ``phlo.ingest.dlt`` or ``phlo.ingest.provider(name)``. This
module remains callable so existing ``@phlo.ingestion(...)`` workflows keep
working until the alias is removed, but every call emits a DeprecationWarning.

Migrate with the bundled codemod::

    phlo migrate decorators-2026-05 PATH

which rewrites ``@phlo.ingestion(...)`` and ``@phlo_ingestion(...)`` to
``@phlo.ingest.dlt(...)``.
"""

from __future__ import annotations

import sys
import warnings
from types import ModuleType
from typing import Any

_DEPRECATION_MESSAGE = (
    "phlo.ingestion is deprecated and will be removed in an upcoming release; "
    "use phlo.ingest.dlt (or phlo.ingest.provider) instead. Migrate with: "
    "phlo migrate decorators-2026-05"
)


def phlo_ingestion(*args: Any, **kwargs: Any) -> Any:
    """Return the DLT ingestion decorator for compatibility.

    Deprecated: use ``phlo.ingest.dlt`` instead.
    """
    warnings.warn(_DEPRECATION_MESSAGE, DeprecationWarning, stacklevel=2)
    from phlo import ingest

    return ingest.dlt(*args, **kwargs)


def get_ingestion_assets() -> list[Any]:
    """Return registered DLT ingestion assets for compatibility.

    Deprecated: use ``phlo.ingest.assets("dlt")`` instead.
    """
    warnings.warn(_DEPRECATION_MESSAGE, DeprecationWarning, stacklevel=2)
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
