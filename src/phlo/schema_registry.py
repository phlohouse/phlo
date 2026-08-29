"""Schema snapshot registry.

Compatibility checking previously provided by check_compatibility() now lives in
the schema migration planner rather than this registry module.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, unquote, urlsplit

from phlo.capabilities.specs import FieldSpec, NormalizedSchema
from phlo.logging import get_logger

logger = get_logger(__name__)

_REGISTRY_DB_KEYS = (
    "PHLO_REGISTRY_DB_URL",
    "PHLO_LINEAGE_DB_URL",
    "DAGSTER_PG_DB_CONNECTION_STRING",
)


def _load_psycopg2() -> Any:
    try:
        import psycopg2
    except ImportError as exc:
        raise RuntimeError(
            "SchemaRegistry requires the runtime extra: install phlo[runtime]."
        ) from exc
    return psycopg2


# Importing psycopg2 is deferred to first attribute access so that importing
# this module never requires the runtime extra; only actual registry use does.
class _LazyPsycopg2:
    def __getattr__(self, name: str) -> Any:
        return getattr(_load_psycopg2(), name)


psycopg2 = _LazyPsycopg2()


def resolve_registry_db_url() -> str | None:
    """Resolve the registry database URL from environment variables."""
    for key in _REGISTRY_DB_KEYS:
        value = os.environ.get(key)
        if value:
            return value
    return None


def _canonical_schema_json(schema: NormalizedSchema) -> str:
    """Serialize a NormalizedSchema to canonical JSON (sorted keys, stable for hashing)."""
    data = {
        "fields": [
            {
                "name": f.name,
                "dtype": f.dtype,
                "nullable": f.nullable,
                "default": f.default,
            }
            for f in sorted(schema.fields, key=lambda f: f.name)
        ]
    }
    return json.dumps(data, sort_keys=True, separators=(",", ":"))


def _schema_hash(canonical_json: str) -> str:
    """Return a truncated SHA-256 hash of canonical schema JSON."""
    return hashlib.sha256(canonical_json.encode()).hexdigest()[:16]


def _normalized_connection_key(connection_string: str) -> str:
    """Hash the effective non-secret PostgreSQL target for initialization state."""
    parsed = urlsplit(connection_string.strip())
    if parsed.scheme in {"postgres", "postgresql"} and parsed.hostname:
        parameters = dict(parse_qsl(parsed.query, keep_blank_values=True))
        host = parameters.get("host") or parsed.hostname
        port = parameters.get("port") or parsed.port or 5432
        query_user = parameters.get("user")
        effective_user = query_user if query_user is not None else unquote(parsed.username or "")
        database = parameters.get("dbname") or unquote(parsed.path.strip("/")) or effective_user
        canonical = f"postgresql://{host.lower()}:{port}/{database}"
        return hashlib.sha256(canonical.encode()).hexdigest()
    return hashlib.sha256(connection_string.strip().encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class SchemaSnapshot:
    """Immutable record of a schema snapshot stored in the registry."""

    snapshot_id: str
    table_name: str
    schema_json: str
    schema_hash: str
    created_at: str | None = None
    run_id: str | None = None
    source: str | None = None


class SchemaRegistry:
    """PostgreSQL-backed schema snapshot registry."""

    _initialized_connections: set[str] = set()
    _initialization_lock = threading.Lock()

    def __init__(self, connection_string: str):
        self.connection_string = connection_string

    def _ensure_schema(self) -> None:
        # The flag is class-level on purpose: one successful initialization per
        # process covers every SchemaRegistry instance pointed at the same DB.
        # A concurrent-creator "already exists" error counts as initialized;
        # any other failure is only warned about, leaving the flag unset so
        # the next call retries setup before its first statement.
        connection_key = _normalized_connection_key(self.connection_string)
        with SchemaRegistry._initialization_lock:
            if connection_key in SchemaRegistry._initialized_connections:
                return
            try:
                self._setup_schema()
                SchemaRegistry._initialized_connections.add(connection_key)
            except Exception as e:
                if "already exists" in str(e).lower():
                    SchemaRegistry._initialized_connections.add(connection_key)
                else:
                    logger.warning("schema_registry_init_failed", error=str(e))

    def _setup_schema(self) -> None:
        sql_path = Path(__file__).parent / "sql" / "001_create_schema_registry.sql"
        with sql_path.open() as f:
            schema_sql = f.read()
        with psycopg2.connect(self.connection_string) as conn:
            with conn.cursor() as cur:
                cur.execute(schema_sql)
            conn.commit()
        logger.info("schema_registry_setup_complete")

    def snapshot_schema(
        self,
        table_name: str,
        schema: NormalizedSchema,
        *,
        run_id: str | None = None,
        source: str = "materialization",
    ) -> str:
        """Snapshot a schema. Returns snapshot_id.

        Rows are deduped by (table_name, schema_hash): re-snapshotting an
        unchanged schema upserts created_at/run_id/source and rotates the
        snapshot_id rather than inserting a second row.
        """
        canonical = _canonical_schema_json(schema)
        schema_hash = _schema_hash(canonical)
        snapshot_id = str(uuid.uuid4())

        self._ensure_schema()
        with psycopg2.connect(self.connection_string) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO phlo.schema_snapshots
                    (snapshot_id, table_name, schema, schema_hash, run_id, source)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (table_name, schema_hash) DO UPDATE
                        SET created_at = NOW(),
                            snapshot_id = EXCLUDED.snapshot_id,
                            run_id = EXCLUDED.run_id,
                            source = EXCLUDED.source
                    RETURNING snapshot_id
                    """,
                    (snapshot_id, table_name, canonical, schema_hash, run_id, source),
                )
                row = cur.fetchone()
            conn.commit()

        persisted_snapshot_id = row[0] if row else snapshot_id
        logger.info(
            "schema_snapshot_created",
            table_name=table_name,
            snapshot_id=persisted_snapshot_id,
        )
        return persisted_snapshot_id

    def get_latest_snapshots(self, table_name: str, limit: int = 2) -> list[SchemaSnapshot]:
        """Get most recent snapshots for a table."""
        self._ensure_schema()
        with psycopg2.connect(self.connection_string) as conn, conn.cursor() as cur:
            cur.execute(
                """
                    SELECT snapshot_id, table_name, schema, schema_hash,
                           created_at, run_id, source
                    FROM phlo.schema_snapshots
                    WHERE table_name = %s
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                (table_name, limit),
            )
            rows = cur.fetchall()
        return [
            SchemaSnapshot(
                snapshot_id=r[0],
                table_name=r[1],
                schema_json=r[2] if isinstance(r[2], str) else json.dumps(r[2]),
                schema_hash=r[3],
                created_at=r[4].isoformat() if r[4] else None,
                run_id=r[5],
                source=r[6],
            )
            for r in rows
        ]


def deserialize_schema(schema_json: str) -> NormalizedSchema:
    """Deserialize a canonical schema JSON string back to NormalizedSchema."""
    data = json.loads(schema_json)
    fields = [
        FieldSpec(
            name=f["name"],
            dtype=f["dtype"],
            nullable=f["nullable"],
            default=f.get("default"),
        )
        for f in data["fields"]
    ]
    return NormalizedSchema(fields=fields)
