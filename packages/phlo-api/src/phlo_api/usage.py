"""Observed query inputs from Trino 483 catalogs with explicit Nessie refs."""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
import re
import struct
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator, Literal

import psycopg2
from fastapi import APIRouter, HTTPException, Request
from pydantic import AwareDatetime

from phlo.config.process import get_process_settings as get_core_process_settings
from phlo_api.api.authentication import get_request_principal
from phlo_api.api.v1 import _targets
from phlo_api.errors import BackendUnavailableError
from phlo_api.settings import get_process_settings
from phlo_api.v1_contract import Environment, WireModel

router = APIRouter(tags=["v1 usage"])
_QUERY_ID = re.compile(r"[A-Za-z0-9_]{1,128}\Z")
_CATALOG = re.compile(r"[a-z][a-z0-9_-]{0,127}\Z")
_VERSION = re.compile(r"[0-9a-f]{64}\Z")
_MAX_BODY = 262_144
_RETENTION = timedelta(days=30)


class ObservedQuery(WireModel):
    query_id: str
    source_id: str
    occurred_at: AwareDatetime
    query_state: Literal["FINISHED"]


class QueryUsagePage(WireModel):
    env: Environment
    asset_id: str
    table_name: str | None
    nessie_ref: str
    status: Literal["partial", "unavailable"]
    source: Literal["trino_query_completed"] = "trino_query_completed"
    reason: Literal["no_verified_query_evidence", "no_asset_relation"] | None = None
    items: list[ObservedQuery]
    next_cursor: str | None


def catalog_version(catalog: str, properties: dict[str, str]) -> str:
    """Trino 483 FileCatalogStore.computeCatalogVersion, using Guava's little-endian writes."""
    connector = properties["connector.name"]
    values = {key: value for key, value in properties.items() if key != "connector.name"}
    digest = hashlib.sha256()

    def text(value: str) -> None:
        encoded = value.encode("utf-16-le")
        digest.update(struct.pack("<i", len(encoded) // 2))
        digest.update(encoded)

    digest.update("catalog-hash".encode("utf-16-le"))
    text(catalog)
    text(connector)
    digest.update(struct.pack("<i", len(values)))
    for key, value in sorted(values.items()):
        text(key)
        text(value)
    return digest.hexdigest()


def _sources() -> dict[str, tuple[str, dict[tuple[str, str], tuple[Environment, str]]]]:
    """Build only bindings proven by a query-selected hash of direct Nessie properties."""
    try:
        config = json.loads(get_process_settings()["PHLO_V1_USAGE_TRINO_SOURCES"])
        targets = _targets()
        if not isinstance(config, dict) or not 1 <= len(config) <= 8:
            raise ValueError
        result: dict[str, tuple[str, dict[tuple[str, str], tuple[Environment, str]]]] = {}
        subjects: set[str] = set()
        for source_id, definition in config.items():
            if (
                not isinstance(source_id, str)
                or not _CATALOG.fullmatch(source_id)
                or not isinstance(definition, dict)
                or set(definition) != {"service_subject", "catalogs"}
            ):
                raise ValueError
            subject = definition["service_subject"]
            catalogs = definition["catalogs"]
            if (
                not isinstance(subject, str)
                or not subject
                or subject in subjects
                or not isinstance(catalogs, dict)
                or set(catalogs) != {"prod", "staging"}
            ):
                raise ValueError
            subjects.add(subject)
            bindings: dict[tuple[str, str], tuple[Environment, str]] = {}
            names: set[str] = set()
            for env, descriptor in catalogs.items():
                if not isinstance(descriptor, dict) or set(descriptor) != {"catalog", "properties"}:
                    raise ValueError
                name, properties = descriptor["catalog"], descriptor["properties"]
                if (
                    not isinstance(name, str)
                    or not _CATALOG.fullmatch(name)
                    or name in names
                    or not isinstance(properties, dict)
                    or not 4 <= len(properties) <= 50
                    or any(
                        not isinstance(k, str)
                        or not isinstance(v, str)
                        or not k
                        or not k.isascii()
                        or len(k) > 128
                        or not v
                        or len(v) > 2048
                        or "${" in v
                        or any(
                            word in k.lower()
                            for word in ("password", "secret", "token", "access-key")
                        )
                        for k, v in properties.items()
                    )
                ):
                    raise ValueError
                if (
                    properties.get("connector.name") != "iceberg"
                    or properties.get("iceberg.catalog.type") != "nessie"
                    or properties.get("iceberg.nessie-catalog.ref") != targets[env].nessie_ref
                    or not properties.get("iceberg.nessie-catalog.uri", "").startswith(
                        ("http://", "https://")
                    )
                ):
                    raise ValueError
                names.add(name)
                bindings[(name, catalog_version(name, properties))] = (
                    env,
                    targets[env].nessie_ref,
                )
            result[source_id] = subject, bindings
        return result
    except (KeyError, ValueError, TypeError, UnicodeError, OverflowError) as exc:
        raise BackendUnavailableError("Verified Trino usage sources are not configured.") from exc


@contextmanager
def _transaction() -> Iterator[Any]:
    dsn = get_core_process_settings().phlo_run_evidence_db_url
    if not dsn:
        raise BackendUnavailableError("Query usage storage is unavailable.")
    try:
        connection = psycopg2.connect(dsn)
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()
    except psycopg2.Error as exc:
        raise BackendUnavailableError("Query usage storage is unavailable.") from exc


def initialize_usage() -> None:
    """Explicit migration for a disposable or selected installation database."""
    with _transaction() as connection, connection.cursor() as cursor:
        cursor.execute(
            (Path(__file__).parent / "sql" / "002_query_usage.sql").read_text(encoding="utf-8")
        )


def _completed_event(
    event: Any, bindings: dict[tuple[str, str], tuple[Environment, str]]
) -> tuple[str, datetime, str, list[tuple[str, str, Environment, str, str]]]:
    if not isinstance(event, dict):
        raise ValueError
    metadata, context, io = event.get("metadata"), event.get("context"), event.get("ioMetadata")
    if not all(isinstance(item, dict) for item in (metadata, context, io)):
        raise ValueError
    query_id, state = metadata.get("queryId"), metadata.get("queryState")
    if (
        not isinstance(query_id, str)
        or not _QUERY_ID.fullmatch(query_id)
        or state not in {"FINISHED", "FAILED"}
        or context.get("serverVersion") != "483"
    ):
        raise ValueError
    try:
        occurred_at = datetime.fromisoformat(event["endTime"].replace("Z", "+00:00"))
    except (KeyError, AttributeError, TypeError, ValueError) as exc:
        raise ValueError from exc
    if occurred_at.tzinfo is None or occurred_at > datetime.now(UTC) + timedelta(minutes=5):
        raise ValueError
    inputs = io.get("inputs")
    if not isinstance(inputs, list) or len(inputs) > 100:
        raise ValueError
    matches: set[tuple[str, str, Environment, str, str]] = set()
    for item in inputs:
        if not isinstance(item, dict):
            raise ValueError
        name, version, schema, table = (
            item.get("catalogName"),
            item.get("catalogVersion"),
            item.get("schema"),
            item.get("table"),
        )
        if not all(isinstance(value, str) for value in (name, version, schema, table)):
            raise ValueError
        if not _CATALOG.fullmatch(name) or len(version) > 128:
            raise ValueError
        if not _VERSION.fullmatch(version):
            # REST and static-mode catalogs can report "default". They cannot
            # establish a ref, but must not hide other verified query inputs.
            continue
        scope = bindings.get((name, version))
        if scope is None:
            continue
        if not 1 <= len(schema) <= 256 or not 1 <= len(table) <= 256 or "/" in schema + table:
            raise ValueError
        if item.get("connectorName") in (None, "iceberg"):
            matches.add((name, version, scope[0], scope[1], f"{schema}/{table}"))
    return query_id, occurred_at, state, sorted(matches)


def _save_event(
    source_id: str,
    query_id: str,
    occurred_at: datetime,
    state: str,
    matches: list[tuple[str, str, Environment, str, str]],
) -> int:
    # The raw Trino event (SQL, user, session, failures) never reaches persistence.
    safe = json.dumps([occurred_at.isoformat(), state, matches], separators=(",", ":"))
    digest = hashlib.sha256(safe.encode()).hexdigest()
    stored = state == "FINISHED" and occurred_at >= datetime.now(UTC) - _RETENTION
    with _transaction() as connection, connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO phlo.query_usage_event(source_id,query_id,event_digest) "
            "VALUES (%s,%s,%s) ON CONFLICT DO NOTHING RETURNING query_id",
            (source_id, query_id, digest),
        )
        if cursor.fetchone() is None:
            cursor.execute(
                "SELECT event_digest FROM phlo.query_usage_event WHERE source_id=%s AND query_id=%s",
                (source_id, query_id),
            )
            if cursor.fetchone()[0] != digest:
                raise HTTPException(status_code=409, detail="Conflicting Trino query event replay.")
            return 0
        if stored:
            for _name, version, env, ref, table_id in matches:
                cursor.execute(
                    "INSERT INTO phlo.asset_query_usage "
                    "(source_id,query_id,catalog_version,env,nessie_ref,table_id,occurred_at,query_state) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,'FINISHED')",
                    (source_id, query_id, version, env, ref, table_id, occurred_at),
                )
        cursor.execute(
            "DELETE FROM phlo.asset_query_usage WHERE occurred_at < now() - interval '30 days'"
        )
        cursor.execute(
            "DELETE FROM phlo.query_usage_event WHERE received_at < now() - interval '30 days' "
            "AND NOT EXISTS (SELECT 1 FROM phlo.asset_query_usage AS input "
            "WHERE input.source_id=phlo.query_usage_event.source_id "
            "AND input.query_id=phlo.query_usage_event.query_id)"
        )
    return len(matches) if stored else 0


@router.post("/trino/query-completed", status_code=202)
async def v1_trino_query_completed(request: Request) -> dict[str, int]:
    """Accept only a pinned Trino coordinator's bounded completion evidence."""
    source_ids = request.query_params.getlist("source_id")
    if len(source_ids) != 1:
        raise HTTPException(status_code=422, detail="Provide one Trino source_id.")
    source_id = source_ids[0]
    source = _sources().get(source_id)
    principal = get_request_principal(request)
    if (
        source is None
        or principal is None
        or principal.principal_type != "service"
        or principal.subject != source[0]
    ):
        raise HTTPException(status_code=403, detail="Trino event source is not authorized.")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > _MAX_BODY:
            raise HTTPException(status_code=413, detail="Trino query event is too large.")
    try:
        query_id, occurred_at, state, matches = _completed_event(json.loads(body), source[1])
    except (ValueError, TypeError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=422, detail="Invalid Trino query event.") from exc
    count = await asyncio.to_thread(_save_event, source_id, query_id, occurred_at, state, matches)
    return {"observed_inputs": count}


def _cursor(env: Environment, ref: str, asset_id: str, table_id: str, key: list[Any]) -> str:
    return (
        base64.urlsafe_b64encode(
            json.dumps(
                [env, ref, asset_id, table_id, *key], default=str, separators=(",", ":")
            ).encode()
        )
        .decode()
        .rstrip("=")
    )


def read_query_usage(
    env: Environment, ref: str, asset_id: str, table_id: str, limit: int, cursor: str | None
) -> QueryUsagePage:
    """Read at most one page from the installation's retained observed inputs."""
    if cursor and len(cursor) > 1024:
        raise HTTPException(status_code=400, detail="Invalid query usage cursor.")
    key = None
    if cursor:
        try:
            values = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
            if (
                not isinstance(values, list)
                or len(values) != 8
                or values[:4] != [env, ref, asset_id, table_id]
            ):
                raise ValueError
            key = (datetime.fromisoformat(values[4]), *values[5:])
            if key[0].tzinfo is None or any(not isinstance(v, str) or not v for v in key[1:]):
                raise ValueError
        except (ValueError, TypeError, UnicodeDecodeError, binascii.Error) as exc:
            raise HTTPException(status_code=400, detail="Invalid query usage cursor.") from exc
    with _transaction() as connection, connection.cursor() as db:
        db.execute(
            "SELECT occurred_at,query_id,source_id,catalog_version FROM phlo.asset_query_usage "
            "WHERE env=%s AND nessie_ref=%s AND table_id=%s "
            "AND occurred_at >= now() - interval '30 days' "
            "AND (%s::timestamptz IS NULL OR (occurred_at,query_id,source_id,catalog_version) "
            "< (%s,%s,%s,%s)) "
            "ORDER BY occurred_at DESC,query_id DESC,source_id DESC,catalog_version DESC LIMIT %s",
            (env, ref, table_id, key[0] if key else None, *(key or (None,) * 4), limit + 1),
        )
        rows = db.fetchall()
    page = rows[:limit]
    return QueryUsagePage(
        env=env,
        asset_id=asset_id,
        table_name=table_id.replace("/", "."),
        nessie_ref=ref,
        status="partial" if page else "unavailable",
        reason=None if page else "no_verified_query_evidence",
        items=[
            ObservedQuery(
                query_id=row[1], source_id=row[2], occurred_at=row[0], query_state="FINISHED"
            )
            for row in page
        ],
        next_cursor=_cursor(env, ref, asset_id, table_id, list(page[-1]))
        if len(rows) > limit
        else None,
    )
