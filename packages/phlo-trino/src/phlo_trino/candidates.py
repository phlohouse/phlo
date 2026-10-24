"""Fresh, file-attested REST catalogs for explicitly owned candidate work."""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter, ValidationError
from phlo.config_schema import EnvironmentId
from trino.dbapi import connect
from phlo_trino.settings import get_settings

_ID = re.compile(r"^[a-f0-9]{32}$")
_ENVIRONMENT = TypeAdapter(EnvironmentId)
_PART = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _owned_ref(identity: str, ref: str) -> bool:
    parts = ref.rsplit("-", 2)
    if len(parts) != 3 or parts[1] not in {"upload", "transform"} or parts[2] != identity:
        return False
    try:
        _ENVIRONMENT.validate_python(parts[0])
    except ValidationError:
        return False
    return bool(_ID.fullmatch(identity))


def _properties(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", "!")):
            continue
        key, separator, value = line.partition("=")
        if not separator or key.strip() in result:
            raise ValueError("Candidate catalog requires unambiguous property files.")
        # Java's Properties writer escapes delimiters even in URI values.
        result[key.strip()] = re.sub(r"\\([:=#! ])", r"\1", value.strip())
    return result


def _catalog(root: Path, identity: str) -> tuple[str, Path]:
    if not _ID.fullmatch(identity):
        raise ValueError("Invalid candidate identity.")
    base = get_settings(root).trino_catalog
    if not _PART.fullmatch(base):
        raise ValueError("The configured base catalog is not supported.")
    name = f"{base}_candidate_{identity}"
    return name, root / ".phlo" / "trino" / "catalog" / f"{name}.properties"


def connection(root: Path) -> dict[str, str]:
    settings = get_settings(root)
    return {"endpoint": f"http://{settings.trino_host}:{settings.trino_port}", "user": "phlo"}


def _connect(root: Path, catalog: str = "system"):
    settings = get_settings(root)
    return connect(
        host=settings.trino_host,
        port=settings.trino_port,
        user="phlo",
        catalog=catalog,
        request_timeout=30,
        session_properties={"query_max_run_time": "20s", "query_max_planning_time": "5s"},
    )


def _digest(properties: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(properties, sort_keys=True).encode()).hexdigest()


def catalog_properties(root: Path) -> dict[str, str]:
    """Read the local writable REST catalog configuration without contacting Trino."""
    source = root / ".phlo" / "trino" / "catalog" / f"{get_settings(root).trino_catalog}.properties"
    properties = _properties(source)
    if (
        properties.get("connector.name") != "iceberg"
        or properties.get("iceberg.catalog.type") != "rest"
    ):
        raise ValueError("Candidate execution requires a configured Iceberg REST catalog.")
    if properties.get("iceberg.security") == "READ_ONLY":
        raise ValueError("Candidate execution requires a separate writable coordinator.")
    return properties


def create_catalog(root: Path, identity: str, ref: str) -> dict[str, str]:
    """Create, never adopt, a catalog; its generated file must attest the binding."""
    if not _owned_ref(identity, ref):
        raise ValueError("Candidate reference is not owned by this operation.")
    name, destination = _catalog(root, identity)
    properties = catalog_properties(root)
    if destination.exists():
        raise ValueError("An existing candidate catalog cannot be adopted.")
    properties["iceberg.rest-catalog.prefix"] = ref
    rendered = ", ".join(
        '"' + key.replace('"', '""') + '" = ' + "'" + value.replace("'", "''") + "'"
        for key, value in properties.items()
        if key != "connector.name"
    )
    client = _connect(root)
    try:
        cursor = client.cursor()
        cursor.execute(f'CREATE CATALOG "{name}" USING iceberg WITH ({rendered})')
        cursor.fetchall()
    finally:
        client.close()
    observed = _properties(destination)
    if observed != properties:
        raise ValueError("Created catalog configuration does not attest the requested reference.")
    return {"catalog": name, "catalog_properties_digest": _digest(observed)}


def verify_catalog(root: Path, identity: str, ref: str, catalog: str, digest: str) -> None:
    expected, path = _catalog(root, identity)
    if catalog != expected or not _owned_ref(identity, ref):
        raise ValueError("Candidate catalog identity changed.")
    properties = _properties(path)
    if properties.get("iceberg.rest-catalog.prefix") != ref or _digest(properties) != digest:
        raise ValueError("Candidate catalog binding changed.")
    client = _connect(root)
    try:
        cursor = client.cursor()
        cursor.execute(
            "SELECT catalog_name FROM system.metadata.catalogs WHERE catalog_name = ?", [catalog]
        )
        if cursor.fetchall() != [[catalog]]:
            raise ValueError("Candidate catalog is no longer registered.")
    finally:
        client.close()


def _wire(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, (float, Decimal)):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return [_wire(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _wire(item) for key, item in value.items()}
    raise ValueError("Candidate preview contains an unsupported value type.")


def preview(root: Path, catalog: str, relation: str, limit: int) -> dict[str, Any]:
    parts = relation.split(".")
    if len(parts) != 2 or not all(_PART.fullmatch(part) for part in parts) or not 1 <= limit <= 100:
        raise ValueError("Candidate preview requires a bounded namespace.table relation.")
    client = _connect(root, catalog)
    try:
        cursor = client.cursor()
        quoted = ".".join(f'"{part}"' for part in (catalog, *parts))
        cursor.execute(f"SELECT * FROM {quoted} LIMIT {limit + 1}")
        columns = [str(column[0]) for column in cursor.description or []]
        types = [str(column[1]) for column in cursor.description or []]
        rows = cursor.fetchall()
        return {
            "columns": columns,
            "column_types": types,
            "rows": [
                {key: _wire(value) for key, value in zip(columns, row, strict=True)}
                for row in rows[:limit]
            ],
            "has_more": len(rows) > limit,
            "query_id": getattr(cursor, "query_id", None),
        }
    finally:
        client.close()
