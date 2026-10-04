"""Fail-closed Iceberg schema conflict preparation for Nessie merges."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote
from uuid import uuid4

from phlo_api.errors import ConflictError, UnprocessableInputError

NessieRequest = Callable[..., Awaitable[dict[str, Any] | list[Any] | None]]


@dataclass(frozen=True)
class PreparedSchemaResolutions:
    temporary_refs: tuple[tuple[str, str], ...]
    key_merge_modes: tuple[dict[str, Any], ...]
    expected_fields: Mapping[str, tuple[dict[str, Any], ...]]

    async def cleanup(self, nessie_request: NessieRequest) -> None:
        for temporary_ref, temporary_hash in reversed(self.temporary_refs):
            await cleanup_schema_resolutions(
                temporary_ref=temporary_ref,
                temporary_hash=temporary_hash,
                nessie_request=nessie_request,
            )


def _reference(value: Any, message: str) -> dict[str, Any]:
    response = _object(value, message)
    return _object(response.get("reference", response), message)


async def _create_temporary_ref(
    *, ref: str, ref_hash: str, nessie_request: NessieRequest
) -> tuple[str, str]:
    temporary_ref = f"phlo-schema-resolution-{uuid4().hex}"
    value = await nessie_request(
        "POST",
        "/api/v2/trees",
        params={"name": temporary_ref, "type": "branch"},
        body={"type": "BRANCH", "name": ref, "hash": ref_hash},
    )
    reference = _reference(value, "Nessie returned an invalid temporary reference.")
    if reference.get("name") != temporary_ref:
        raise ConflictError("Nessie did not create the requested temporary reference.")
    created_hash = reference.get("hash")
    if not isinstance(created_hash, str) or not created_hash:
        raise ConflictError("Nessie returned an invalid temporary reference hash.")
    return temporary_ref, created_hash


def _key_parts(key: str) -> tuple[str, ...]:
    parts = tuple(key.split("."))
    if not parts or any(not part for part in parts):
        raise UnprocessableInputError(f"Invalid Nessie content key: {key!r}.")
    return parts


def _content_path(ref: str, ref_hash: str, key: str) -> str:
    encoded_ref = quote(ref, safe="")
    encoded_hash = quote(ref_hash, safe="")
    encoded_key = quote(".".join(_key_parts(key)), safe="")
    return f"/api/v2/trees/{encoded_ref}@{encoded_hash}/contents/{encoded_key}"


def _object(value: Any, message: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConflictError(message)
    return value


async def _content(
    nessie_request: NessieRequest, ref: str, ref_hash: str, key: str
) -> dict[str, Any]:
    payload = _object(
        await nessie_request("GET", _content_path(ref, ref_hash, key)),
        "Nessie returned invalid table content.",
    )
    content = payload.get("content", payload)
    return _object(content, "Nessie returned invalid table content.")


def _validate_content_pair(key: str, source: dict[str, Any], target: dict[str, Any]) -> None:
    if source.get("type") != "ICEBERG_TABLE" or target.get("type") != "ICEBERG_TABLE":
        raise ConflictError(f"Schema resolution is only supported for Iceberg tables: {key}.")
    source_id, target_id = source.get("id"), target.get("id")
    if not isinstance(source_id, str) or not source_id or source_id != target_id:
        raise ConflictError(f"Iceberg content IDs differ for {key}.")
    source_snapshot = source.get("snapshotId")
    target_snapshot = target.get("snapshotId")
    if not isinstance(source_snapshot, int) or source_snapshot != target_snapshot:
        raise ConflictError(f"Iceberg data snapshots differ for {key}.")


def _field_definition(field: Any) -> dict[str, Any]:
    field_type = field.field_type
    if not getattr(field_type, "is_primitive", False):
        raise UnprocessableInputError("Nested Iceberg fields cannot be schema-resolved.")
    return {
        "field_id": field.field_id,
        "name": field.name,
        "field_type": field_type,
        "type": str(field_type),
        "required": field.required,
        "doc": field.doc,
    }


def _fields(table: Any) -> dict[str, dict[str, Any]]:
    definitions = [_field_definition(field) for field in table.schema().fields]
    names = [field["name"] for field in definitions]
    ids = [field["field_id"] for field in definitions]
    if len(names) != len(set(names)) or len(ids) != len(set(ids)):
        raise UnprocessableInputError("Duplicate Iceberg field identities are unsupported.")
    return {field["name"]: field for field in definitions}


def _different_fields(
    source: Mapping[str, dict[str, Any]], target: Mapping[str, dict[str, Any]]
) -> set[str]:
    comparable = ("field_id", "type", "required", "doc")
    return {
        name
        for name in source.keys() | target.keys()
        if name not in source
        or name not in target
        or any(source[name][item] != target[name][item] for item in comparable)
    }


def _validate_field_identities(
    source: Mapping[str, dict[str, Any]], target: Mapping[str, dict[str, Any]]
) -> None:
    source_names = {field["field_id"]: name for name, field in source.items()}
    target_names = {field["field_id"]: name for name, field in target.items()}
    if any(
        name in target and target[name]["field_id"] != field["field_id"]
        for name, field in source.items()
    ) or any(
        field_id in target_names and target_names[field_id] != name
        for field_id, name in source_names.items()
    ):
        raise UnprocessableInputError(
            "Changed or duplicate Iceberg field identities are unsupported."
        )


def _decision_for_key(
    *,
    env: str,
    key: str,
    source_ref: str,
    target_ref: str,
    source_hash: str,
    target_hash: str,
    decisions: Sequence[Any],
) -> Any:
    matches = [decision for decision in decisions if decision.table_key == key]
    if len(matches) != 1:
        raise UnprocessableInputError(f"Exactly one schema decision is required for {key}.")
    decision = matches[0]
    binding = (
        decision.env,
        decision.source_ref,
        decision.target_ref,
        decision.source_hash,
        decision.target_hash,
    )
    if binding != (env, source_ref, target_ref, source_hash, target_hash):
        raise ConflictError(f"Schema decision is stale or unbound for {key}.")
    return decision


def _apply_schema(table: Any, desired: Mapping[str, dict[str, Any]]) -> None:
    current = _fields(table)
    with table.update_schema(allow_incompatible_changes=True) as update:
        for name in sorted(current.keys() - desired.keys()):
            update.delete_column(name)
        for name in sorted(desired.keys() - current.keys()):
            field = desired[name]
            update.add_column(
                name,
                field["field_type"],
                doc=field["doc"],
                required=field["required"],
            )
        for name in sorted(current.keys() & desired.keys()):
            before, after = current[name], desired[name]
            if before["type"] != after["type"] or before["doc"] != after["doc"]:
                update.update_column(name, field_type=after["field_type"], doc=after["doc"])
            if before["required"] != after["required"]:
                if after["required"]:
                    make_required = getattr(update, "make_column_required", None)
                    if make_required is not None:
                        make_required(name)
                    else:
                        update.update_column(name, required=True)
                else:
                    update.make_column_optional(name)


def _load_tables(ref: str, keys: Iterable[str]) -> dict[str, Any]:
    from phlo_iceberg.catalog import get_catalog

    catalog = get_catalog(ref=ref)
    return {key: catalog.load_table(tuple(_key_parts(key))) for key in keys}


async def cleanup_schema_resolutions(
    *, temporary_ref: str | None, temporary_hash: str | None, nessie_request: NessieRequest
) -> None:
    """Delete only the adapter-owned temporary ref; tolerate absent/partially-created refs."""
    if not temporary_ref:
        return
    reference = await nessie_request(
        "GET",
        f"/api/v2/trees/{quote(temporary_ref, safe='')}",
        allow_not_found=True,
    )
    if reference is None:
        return
    payload = _reference(reference, "Nessie returned an invalid temporary reference.")
    current_hash = payload.get("hash") or temporary_hash
    if not isinstance(current_hash, str) or not current_hash:
        raise ConflictError("Nessie returned an invalid temporary reference hash.")
    await nessie_request(
        "DELETE",
        f"/api/v2/trees/{quote(temporary_ref, safe='')}@{quote(current_hash, safe='')}",
        params={"type": "branch"},
    )


async def verify_schema_resolutions(
    *,
    prepared: PreparedSchemaResolutions,
    target_ref: str,
    resulting_hash: str,
    nessie_request: NessieRequest,
) -> None:
    """Verify the merged ref points at each exact resolved Iceberg table value."""
    for mode in prepared.key_merge_modes:
        key = mode.get("key")
        elements = key.get("elements") if isinstance(key, dict) else None
        if (
            not isinstance(elements, list)
            or not elements
            or not all(isinstance(part, str) and part for part in elements)
        ):
            raise ConflictError("Prepared schema resolution has an invalid content key.")
        resolved = mode.get("resolvedContent")
        if not isinstance(resolved, dict):
            raise ConflictError("Prepared schema resolution has invalid table content.")
        actual = await _content(nessie_request, target_ref, resulting_hash, ".".join(elements))
        if actual != resolved:
            raise ConflictError(
                f"Nessie merge did not apply the verified per-column schema for {'.'.join(elements)}."
            )


async def prepare_schema_resolutions(
    *,
    env: str,
    source_ref: str,
    target_ref: str,
    source_hash: str,
    target_hash: str,
    requested_keys: Sequence[str],
    decisions: Sequence[Any],
    nessie_request: NessieRequest,
) -> PreparedSchemaResolutions:
    """Prepare resolved Iceberg metadata on an isolated branch for a Nessie merge."""
    keys = tuple(requested_keys)
    if not keys or len(keys) != len(set(keys)):
        raise UnprocessableInputError("Requested schema keys must be non-empty and unique.")
    requested = set(keys)
    if any(decision.table_key not in requested for decision in decisions):
        raise UnprocessableInputError("Schema decisions contain an unrequested table key.")

    selected: dict[str, Mapping[str, str]] = {}
    source_contents: dict[str, dict[str, Any]] = {}
    target_contents: dict[str, dict[str, Any]] = {}
    for key in keys:
        decision = _decision_for_key(
            env=env,
            key=key,
            source_ref=source_ref,
            target_ref=target_ref,
            source_hash=source_hash,
            target_hash=target_hash,
            decisions=decisions,
        )
        selected[key] = decision.columns
        source_contents[key], target_contents[key] = await asyncio.gather(
            _content(nessie_request, source_ref, source_hash, key),
            _content(nessie_request, target_ref, target_hash, key),
        )
        _validate_content_pair(key, source_contents[key], target_contents[key])

    temporary_refs: list[tuple[str, str]] = []
    try:
        source_snapshot_ref = await _create_temporary_ref(
            ref=source_ref,
            ref_hash=source_hash,
            nessie_request=nessie_request,
        )
        temporary_refs.append(source_snapshot_ref)
        target_snapshot_ref = await _create_temporary_ref(
            ref=target_ref,
            ref_hash=target_hash,
            nessie_request=nessie_request,
        )
        temporary_refs.append(target_snapshot_ref)
        source_snapshot_name, _ = source_snapshot_ref
        target_snapshot_name, target_snapshot_hash = target_snapshot_ref
        source_tables, temporary_tables = await asyncio.gather(
            asyncio.to_thread(_load_tables, source_snapshot_name, keys),
            asyncio.to_thread(_load_tables, target_snapshot_name, keys),
        )
        desired: dict[str, dict[str, dict[str, Any]]] = {}
        for key in keys:
            source_fields, target_fields = (
                _fields(source_tables[key]),
                _fields(temporary_tables[key]),
            )
            _validate_field_identities(source_fields, target_fields)
            differing = _different_fields(source_fields, target_fields)
            choices = selected[key]
            if set(choices) != differing or any(
                side not in {"source", "target"} for side in choices.values()
            ):
                raise UnprocessableInputError(
                    f"Schema decision fields must exactly match differing top-level fields for {key}."
                )
            resolved = dict(target_fields)
            for name, side in choices.items():
                chosen = source_fields if side == "source" else target_fields
                if name in chosen:
                    resolved[name] = chosen[name]
                else:
                    resolved.pop(name, None)
            desired[key] = resolved

        for key in keys:
            await asyncio.to_thread(_apply_schema, temporary_tables[key], desired[key])
        expected_fields = {
            key: tuple(
                {item: field[item] for item in ("field_id", "name", "type", "required", "doc")}
                for field in _fields(temporary_tables[key]).values()
            )
            for key in keys
        }
        current = _reference(
            await nessie_request("GET", f"/api/v2/trees/{quote(target_snapshot_name, safe='')}"),
            "Nessie returned an invalid temporary reference.",
        )
        temporary_hash = current.get("hash")
        if not isinstance(temporary_hash, str) or not temporary_hash:
            raise ConflictError("Nessie returned an invalid temporary reference hash.")
        resolved_contents = await asyncio.gather(
            *(_content(nessie_request, target_snapshot_name, temporary_hash, key) for key in keys)
        )
        modes = tuple(
            {
                "key": {"elements": list(_key_parts(key))},
                "mergeBehavior": "NORMAL",
                "expectedTargetContent": target_contents[key],
                "resolvedContent": content,
            }
            for key, content in zip(keys, resolved_contents, strict=True)
        )
        return PreparedSchemaResolutions(
            temporary_refs=tuple(
                (name, temporary_hash if name == target_snapshot_name else hash_)
                for name, hash_ in temporary_refs
            ),
            key_merge_modes=modes,
            expected_fields=expected_fields,
        )
    except BaseException:
        for temporary_ref, temporary_hash in reversed(temporary_refs):
            await cleanup_schema_resolutions(
                temporary_ref=temporary_ref,
                temporary_hash=temporary_hash,
                nessie_request=nessie_request,
            )
        raise
