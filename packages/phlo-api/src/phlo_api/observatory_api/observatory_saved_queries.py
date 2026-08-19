"""Saved-query read model persistence for Observatory."""

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
    state_dir = project_root / ".phlo" / "observatory"
    state_dir.mkdir(parents=True, exist_ok=True)
    return state_dir / "saved_queries.json"


def load_saved_queries(project_root: Path) -> list[ObservatorySavedQuery]:
    return _validate_queries(
        load_collection(project_root, "saved_queries", saved_queries_path(project_root))
    )


def dedupe_saved_queries(queries: list[ObservatorySavedQuery]) -> list[ObservatorySavedQuery]:
    unique: dict[tuple[str, str, str], ObservatorySavedQuery] = {}
    for query in sorted(queries, key=lambda item: item.updated_at, reverse=True):
        key = (
            query.name.strip().casefold(),
            " ".join(query.sql.split()).casefold(),
            (query.branch or "main").strip().casefold(),
        )
        unique.setdefault(key, query)
    return list(unique.values())


def write_saved_queries(project_root: Path, queries: list[ObservatorySavedQuery]) -> None:
    mutate_collection(
        project_root,
        "saved_queries",
        saved_queries_path(project_root),
        lambda _items: [query.model_dump(mode="json") for query in queries],
    )


def save_query(project_root: Path, request: ObservatorySavedQueryRequest) -> ObservatorySavedQuery:
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
    if READ_QUERY_RE.match(sql):
        return None
    return "Only simple SELECT preview queries can be saved."
