"""Application-owned run-evidence store dependency.

The store is created once during application lifespan startup and read
from app state per request. The default-store fallback exists only as a
test seam for routers used without the full application.
"""

from __future__ import annotations

from fastapi import Request

from phlo.run_evidence.store import (
    PostgresRunEvidenceStore,
    SQLiteRunEvidenceStore,
    default_run_evidence_store,
)

RunEvidenceStore = SQLiteRunEvidenceStore | PostgresRunEvidenceStore


def get_run_evidence_store(request: Request) -> RunEvidenceStore:
    """Return the store initialized during application startup."""
    store = getattr(request.app.state, "run_evidence_store", None)
    if store is None:
        # Starlette's TestClient does not run lifespan unless used as a context
        # manager. Production requests always receive the lifespan-owned store;
        # preserve the lightweight direct-router test seam for local callers.
        store = default_run_evidence_store()
    return store
