"""Read Iceberg field differences only when provider metadata matches pinned Nessie content."""

from __future__ import annotations

import asyncio
from typing import Any

from phlo_api.branch_schema_resolution import NessieRequest, _content, _fields, _key_parts
from phlo_api.errors import BackendUnavailableError, ConflictError


def _read_fields(ref: str, key: str, content: dict[str, Any]) -> dict[str, dict[str, Any]]:
    if content.get("type") != "ICEBERG_TABLE":
        raise ConflictError("Schema comparison requires Iceberg table content on both branches.")
    location = content.get("metadataLocation")
    if not isinstance(location, str) or not location:
        raise ConflictError("Pinned table metadata location is unavailable.")
    parts = tuple(_key_parts(key))
    try:
        from phlo_iceberg.catalog import get_catalog

        table = get_catalog(ref=ref).load_table(parts)
    except Exception as exc:
        raise BackendUnavailableError("The scoped branch table schema is unavailable.") from exc
    if table.metadata_location != location:
        raise ConflictError("Provider table metadata no longer matches the pinned branch revision.")
    return {
        name: {key: value for key, value in field.items() if key != "field_type"}
        for name, field in _fields(table).items()
    }


async def read_schema_comparison(
    *,
    source: str,
    target: str,
    source_hash: str,
    target_hash: str,
    key: str,
    nessie_request: NessieRequest,
) -> list[dict[str, Any]]:
    """Fail closed if a provider read drifts from either immutable content observation."""
    source_content, target_content = await asyncio.gather(
        _content(nessie_request, source, source_hash, key),
        _content(nessie_request, target, target_hash, key),
    )
    source_fields, target_fields = await asyncio.wait_for(
        asyncio.gather(
            asyncio.to_thread(_read_fields, source, key, source_content),
            asyncio.to_thread(_read_fields, target, key, target_content),
        ),
        timeout=5,
    )
    columns = [
        {"name": name, "source": source_fields.get(name), "target": target_fields.get(name)}
        for name in sorted(source_fields.keys() | target_fields.keys())
        if source_fields.get(name) != target_fields.get(name)
    ]

    if len(columns) > 500:
        raise BackendUnavailableError("Schema differences exceed the 500-column decision limit.")
    return columns
