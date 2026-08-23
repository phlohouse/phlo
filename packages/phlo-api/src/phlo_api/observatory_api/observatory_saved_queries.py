"""Saved-query read model persistence for Observatory.

Saved queries live in the durable-state collection under
.phlo/observatory. Only simple SELECT preview queries can be saved;
anything else is rejected. Unreadable stored state raises
StorageCorruptionError rather than surfacing validation details.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import re
from uuid import uuid4

from fastapi import HTTPException

from phlo_api.observatory_api.observatory_metadata import safe_metadata
from phlo_api.observatory_api.observatory_durable_state import (
    load_collection,
    mutate_collection,
)
from phlo_api.observatory_api.observatory_models import (
    ObservatorySavedQuery,
    ObservatorySavedQueryRequest,
)

READ_QUERY_RE = re.compile(
    r"^\s*select\s+\*\s+from\s+(?P<table>[A-Za-z0-9_.:-]+)(?:\s+limit\s+(?P<limit>\d+))?\s*;?\s*$",
    re.IGNORECASE,
)


def saved_queries_path(project_root: Path) -> Path:
    """Return the saved-queries file path, creating the state directory if needed."""
    state_dir = project_root / ".phlo" / "observatory"
    state_dir.mkdir(parents=True, exist_ok=True)
    return state_dir / "saved_queries.json"


def load_saved_queries(project_root: Path) -> list[ObservatorySavedQuery]:
    """Load and validate saved queries from durable state."""
    return _validate_queries(
        load_collection(project_root, "saved_queries", saved_queries_path(project_root))
    )


def dedupe_saved_queries(queries: list[ObservatorySavedQuery]) -> list[ObservatorySavedQuery]:
    """Return queries deduplicated by casefolded name, whitespace-collapsed SQL, and branch."""
    unique: dict[tuple[str, str, str], ObservatorySavedQuery] = {}
    # Identity is casefolded name + whitespace-collapsed SQL + branch. Sorting
    # newest-first and keeping the first occurrence per identity means the most
    # recently updated duplicate wins.
    for query in sorted(queries, key=lambda item: item.updated_at, reverse=True):
        key = (
            query.name.strip().casefold(),
            " ".join(query.sql.split()).casefold(),
            (query.branch or "main").strip().casefold(),
        )
        unique.setdefault(key, query)
    return list(unique.values())


def write_saved_queries(project_root: Path, queries: list[ObservatorySavedQuery]) -> None:
    """Replace the stored saved queries with the given list."""
    mutate_collection(
        project_root,
        "saved_queries",
        saved_queries_path(project_root),
        lambda _items: [query.model_dump(mode="json") for query in queries],
    )


def save_query(project_root: Path, request: ObservatorySavedQueryRequest) -> ObservatorySavedQuery:
    """Validate and persist a saved query, keeping the newest 100.

    Raises: HTTPException when the name is empty or the SQL is not a
    simple SELECT preview query."""
    if not request.name.strip():
        raise HTTPException(status_code=400, detail="Saved query name is required.")
    validate_error = validate_saved_query_sql(request.sql)
    if validate_error:
        raise HTTPException(status_code=400, detail=validate_error)

    now = datetime.now(UTC).isoformat()
    query = ObservatorySavedQuery(
        id=f"query-{uuid4().hex[:12]}",
        name=request.name.strip(),
        sql=request.sql.strip(),
        branch=request.branch,
        created_at=now,
        updated_at=now,
        metadata=safe_metadata(request.metadata),
    )
    # dedupe returns entries sorted newest-first, so truncating to 100 drops the
    # oldest rather than an arbitrary subset.
    mutate_collection(
        project_root,
        "saved_queries",
        saved_queries_path(project_root),
        lambda items: [
            item.model_dump(mode="json")
            for item in dedupe_saved_queries([query, *_validate_queries(items)])[:100]
        ],
    )
    return query


def _validate_queries(items: list[dict[str, object]]) -> list[ObservatorySavedQuery]:
    try:
        return dedupe_saved_queries([ObservatorySavedQuery.model_validate(item) for item in items])
    except Exception as exc:
        from phlo.plugins.observatory_settings import StorageCorruptionError

        raise StorageCorruptionError("Observatory durable state is unavailable") from exc


def validate_saved_query_sql(sql: str) -> str | None:
    """Return an error message when the SQL is not a simple SELECT preview, else None."""
    if READ_QUERY_RE.match(sql):
        return None
    return "Only simple SELECT preview queries can be saved."
