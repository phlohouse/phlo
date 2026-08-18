"""Controls for scoped operation routes: auth scopes, audit, rate limits, idempotency."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sqlite3
import time
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request

from phlo.security import is_regulated
from phlo_api.api.authentication import get_request_principal

_TOKEN_CONFIG_ENV = "PHLO_API_TOKENS"
_DEFAULT_IDEMPOTENCY_RETENTION_HOURS = 24
# Busy timeout (ms) for SQLite write contention during atomic idempotency claims.
_IDEMPOTENCY_BUSY_TIMEOUT_MS = 5000
# Default Retry-After (seconds) surfaced for a live pending idempotency claim.
_IDEMPOTENCY_RETRY_AFTER_SECONDS = 2
_STATE_PENDING = "pending"
_STATE_COMPLETED = "completed"
_STATE_UNKNOWN = "unknown"
_RATE_LIMITS: dict[tuple[str, str], deque[float]] = defaultdict(deque)


class IdempotencyConflict(HTTPException):
    """Stable 409 raised when an idempotency key is pending or has an unknown outcome."""

    def __init__(self, detail: dict[str, Any], retry_after: int | None = None) -> None:
        headers: dict[str, str] = {}
        if retry_after is not None:
            headers["Retry-After"] = str(retry_after)
        super().__init__(status_code=409, detail=detail, headers=headers or None)


def project_root() -> Path:
    return Path(os.environ.get("PHLO_PROJECT_PATH", ".")).resolve()


def require_scope(request: Request, required_scope: str) -> dict[str, Any]:
    """Require a bearer token with the requested scope or admin."""
    if not is_regulated():
        return {"subject": "development:anonymous", "scopes": ["admin"]}

    principal = get_request_principal(request)
    if principal is not None:
        scopes = _principal_scopes(principal.claims, principal.attributes, principal.groups)
        if required_scope in scopes or "admin" in scopes:
            return {"subject": principal.subject, "scopes": sorted(scopes)}
        raise HTTPException(
            status_code=403, detail={"error": "forbidden", "required_scope": required_scope}
        )

    token = _bearer_token(request)
    token_config = _load_token_config()
    token_data = token_config.get(token) if token else None
    if token_data is None:
        raise HTTPException(
            status_code=401, detail={"error": "unauthorized", "reason": "missing_or_invalid_token"}
        )

    scopes = _principal_scopes(
        token_data.get("claims", {}), token_data.get("attributes", {}), token_data.get("scopes", ())
    )
    if required_scope not in scopes and "admin" not in scopes:
        raise HTTPException(
            status_code=403, detail={"error": "forbidden", "required_scope": required_scope}
        )
    return {"subject": str(token_data.get("subject") or "token"), "scopes": sorted(scopes)}


def enforce_rate_limit(subject: str, operation: str) -> None:
    """Enforce a per-subject, per-operation token bucket."""
    limit = _operation_limit(operation)
    now = time.monotonic()
    bucket = _RATE_LIMITS[(subject, operation)]
    while bucket and now - bucket[0] > 60:
        bucket.popleft()
    if len(bucket) >= limit:
        raise HTTPException(
            status_code=429, detail={"error": "rate_limited", "limit_per_minute": limit}
        )
    bucket.append(now)


def audit_operation(
    *,
    operation: str,
    target: str,
    dry_run: bool,
    auth: dict[str, Any],
    payload: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
) -> None:
    """Append an API-side mutation audit record."""
    audit_dir = project_root() / ".phlo" / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    audit_path = audit_dir / "operations.jsonl"
    _rotate_audit_log(audit_path)
    record = {
        "timestamp": datetime.now(UTC).isoformat(),
        "surface": "phlo-api",
        "operation": operation,
        "target": target,
        "dry_run": dry_run,
        "subject": auth["subject"],
        "scopes": auth["scopes"],
        "payload": payload or {},
        "result": result or {},
    }
    with audit_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")


def _rotate_audit_log(path: Path) -> None:
    max_bytes = int(os.environ.get("PHLO_API_AUDIT_MAX_BYTES", str(10 * 1024 * 1024)))
    max_files = int(os.environ.get("PHLO_API_AUDIT_MAX_FILES", "5"))
    if max_bytes <= 0 or max_files <= 0 or not path.exists() or path.stat().st_size < max_bytes:
        return
    oldest = path.with_name(f"{path.name}.{max_files}")
    if oldest.exists():
        oldest.unlink()
    for index in range(max_files - 1, 0, -1):
        candidate = path.with_name(f"{path.name}.{index}")
        if candidate.exists():
            candidate.replace(path.with_name(f"{path.name}.{index + 1}"))
    path.replace(path.with_name(f"{path.name}.1"))


def replay_or_execute(
    *,
    idempotency_key: str | None,
    operation: str,
    target: str,
    execute: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    """Return a previous idempotent response or execute and persist the new response.

    The idempotency key is claimed atomically *before* the provider runs, so
    concurrent callers with the same identity never execute the provider more
    than once. A contender either replays a completed response or receives a
    stable ``409`` (in-progress / unknown-outcome) without invoking the provider.
    """
    if not idempotency_key:
        return execute()

    key_hash = _idempotency_hash(idempotency_key)
    claim = _claim_idempotency_key(key_hash=key_hash, operation=operation, target=target)
    if claim.claimed:
        pass
    elif claim.state == _STATE_COMPLETED:
        return json.loads(claim.response_json)
    elif claim.state == _STATE_PENDING:
        raise IdempotencyConflict(
            {"error": "idempotency_in_progress"}, retry_after=_IDEMPOTENCY_RETRY_AFTER_SECONDS
        )
    elif claim.state == _STATE_UNKNOWN:
        raise IdempotencyConflict({"error": "idempotency_outcome_unknown"})

    try:
        response = execute()
    except BaseException:
        _mark_idempotency_unknown(key_hash=key_hash, operation=operation, target=target)
        raise
    _complete_idempotency_claim(
        key_hash=key_hash, operation=operation, target=target, response=response
    )
    return response


async def replay_or_execute_async(
    *,
    idempotency_key: str | None,
    operation: str,
    target: str,
    execute: Callable[[], Awaitable[dict[str, Any]]],
) -> dict[str, Any]:
    """Async variant of replay_or_execute.

    The atomic claim and completion run in a worker thread so the SQLite write
    transaction is held outside the event loop; only the provider await runs on
    the loop.
    """
    if not idempotency_key:
        return await execute()

    key_hash = _idempotency_hash(idempotency_key)
    claim = await asyncio.to_thread(
        _claim_idempotency_key, key_hash=key_hash, operation=operation, target=target
    )
    if claim.claimed:
        pass
    elif claim.state == _STATE_COMPLETED:
        return json.loads(claim.response_json)
    elif claim.state == _STATE_PENDING:
        raise IdempotencyConflict(
            {"error": "idempotency_in_progress"}, retry_after=_IDEMPOTENCY_RETRY_AFTER_SECONDS
        )
    elif claim.state == _STATE_UNKNOWN:
        raise IdempotencyConflict({"error": "idempotency_outcome_unknown"})

    try:
        response = await execute()
    except BaseException:
        # Provider raised after the claim: record an unknown outcome so later
        # callers receive a stable 409 and never re-invoke the provider.
        await asyncio.to_thread(
            _mark_idempotency_unknown, key_hash=key_hash, operation=operation, target=target
        )
        raise
    await asyncio.to_thread(
        _complete_idempotency_claim,
        key_hash=key_hash,
        operation=operation,
        target=target,
        response=response,
    )
    return response


@dataclass(slots=True)
class _IdempotencyClaim:
    """Result of an idempotency claim attempt for an existing identity."""

    claimed: bool
    state: str
    response_json: str


def _claim_idempotency_key(*, key_hash: str, operation: str, target: str) -> _IdempotencyClaim:
    """Atomically claim an idempotency identity or report an existing claim's state.

    A ``pending`` row is inserted before provider execution. If the identity
    already exists, the existing row's state (and completed response) is
    returned so the caller can replay or surface a stable conflict.
    """
    conn = _idempotency_connection()
    try:
        _delete_expired(conn)
        now = datetime.now(UTC)
        expires_at = now + timedelta(hours=_DEFAULT_IDEMPOTENCY_RETENTION_HOURS)
        # BEGIN IMMEDIATE acquires the write lock up front so two concurrent
        # claims serialize: exactly one INSERT succeeds and the other sees the
        # committed row rather than racing on the primary key.
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(
                """
                INSERT INTO operations(project, key_hash, operation, target, state, response_json, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(project_root()),
                    key_hash,
                    operation,
                    target,
                    _STATE_PENDING,
                    "",
                    now.isoformat(),
                    expires_at.isoformat(),
                ),
            )
            conn.commit()
            return _IdempotencyClaim(claimed=True, state=_STATE_PENDING, response_json="")
        except sqlite3.IntegrityError:
            conn.rollback()
            row = conn.execute(
                """
                SELECT state, response_json FROM operations
                WHERE project = ? AND key_hash = ? AND operation = ? AND target = ?
                """,
                (str(project_root()), key_hash, operation, target),
            ).fetchone()
            if row is None:
                # Expired and deleted between the INSERT and the read; retry once.
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    """
                    INSERT INTO operations(project, key_hash, operation, target, state, response_json, created_at, expires_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(project_root()),
                        key_hash,
                        operation,
                        target,
                        _STATE_PENDING,
                        "",
                        now.isoformat(),
                        expires_at.isoformat(),
                    ),
                )
                conn.commit()
                return _IdempotencyClaim(claimed=True, state=_STATE_PENDING, response_json="")
            return _IdempotencyClaim(claimed=False, state=str(row[0]), response_json=str(row[1]))
    finally:
        conn.close()


def _complete_idempotency_claim(
    *, key_hash: str, operation: str, target: str, response: dict[str, Any]
) -> None:
    """Mark a claimed identity completed and persist its response for replay."""
    conn = _idempotency_connection()
    try:
        conn.execute(
            """
            UPDATE operations
            SET state = ?, response_json = ?
            WHERE project = ? AND key_hash = ? AND operation = ? AND target = ?
            """,
            (
                _STATE_COMPLETED,
                json.dumps(response, sort_keys=True),
                str(project_root()),
                key_hash,
                operation,
                target,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _mark_idempotency_unknown(*, key_hash: str, operation: str, target: str) -> None:
    """Record an unknown outcome for a claimed identity after a provider failure."""
    conn = _idempotency_connection()
    try:
        conn.execute(
            """
            UPDATE operations
            SET state = ?
            WHERE project = ? AND key_hash = ? AND operation = ? AND target = ?
            """,
            (
                _STATE_UNKNOWN,
                str(project_root()),
                key_hash,
                operation,
                target,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _principal_scopes(claims: dict[str, Any], attributes: dict[str, Any], groups: Any) -> set[str]:
    raw_scopes: list[Any] = []
    for source in (claims, attributes):
        raw_scopes.extend(_scope_values(source.get("scope")))
        raw_scopes.extend(_scope_values(source.get("scopes")))
    raw_scopes.extend(_scope_values(groups))
    return {str(scope) for scope in raw_scopes if str(scope).strip()}


def _scope_values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [item for item in value.replace(",", " ").split() if item]
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value]
    return [str(value)]


def _bearer_token(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        return None
    return header[7:]


def _load_token_config() -> dict[str, dict[str, Any]]:
    raw = os.environ.get(_TOKEN_CONFIG_ENV)
    if not raw:
        return {}
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise HTTPException(status_code=500, detail=f"{_TOKEN_CONFIG_ENV} must be a JSON object")
    return {str(key): value for key, value in payload.items() if isinstance(value, dict)}


def _operation_limit(operation: str) -> int:
    if operation in {"materialize_asset", "backfill_asset"}:
        return int(os.environ.get("PHLO_API_RATE_LIMIT_MATERIALIZE", "10"))
    if operation == "retry_failed_run":
        return int(os.environ.get("PHLO_API_RATE_LIMIT_RETRY", "30"))
    if operation == "cancel_run":
        return int(os.environ.get("PHLO_API_RATE_LIMIT_CANCEL", "60"))
    return int(os.environ.get("PHLO_API_RATE_LIMIT_MUTATION", "60"))


def _idempotency_connection() -> sqlite3.Connection:
    state_dir = project_root() / ".phlo" / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(state_dir / "operations.sqlite")
    conn.execute(f"PRAGMA busy_timeout = {_IDEMPOTENCY_BUSY_TIMEOUT_MS}")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS operations(
            project TEXT NOT NULL,
            key_hash TEXT NOT NULL,
            operation TEXT NOT NULL,
            target TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'completed',
            response_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            PRIMARY KEY(project, key_hash, operation, target)
        )
        """
    )
    _migrate_operations_schema(conn)
    return conn


def _migrate_operations_schema(conn: sqlite3.Connection) -> None:
    """Add the ``state`` column, defaulting existing completed rows to ``completed``.

    The migration is backward-compatible: pre-existing rows (which only ever
    held successful, replayable responses) are treated as ``completed`` so they
    remain replayable after the schema change.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(operations)").fetchall()}
        if "state" not in columns:
            conn.execute(
                "ALTER TABLE operations ADD COLUMN state TEXT NOT NULL DEFAULT 'completed'"
            )
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def _delete_expired(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM operations WHERE expires_at < ?", (datetime.now(UTC).isoformat(),))
    conn.commit()


def _idempotency_hash(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()
