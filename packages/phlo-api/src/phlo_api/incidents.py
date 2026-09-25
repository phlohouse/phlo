"""Durable, environment-scoped incident and activity API."""

from __future__ import annotations

import base64
import hashlib
import json
import os
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Iterator, Literal
from uuid import uuid4

import psycopg2
from fastapi import APIRouter, Header, HTTPException, Query, Request
from pydantic import Field

from phlo_api.api.authentication import get_request_principal
from phlo_api.errors import BackendUnavailableError
from phlo_api.v1_contract import Environment, WireModel
from phlo.run_evidence.redaction import redact_payload

router = APIRouter(tags=["v1 incidents"])
IncidentStatus = Literal["open", "acknowledged", "resolved"]
PageLimit = Annotated[int, Query(ge=1, le=500)]


class IncidentInput(WireModel):
    asset_id: str = Field(min_length=1, max_length=512)
    kind: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=500)
    evidence: dict[str, Any]
    evidence_id: str = Field(min_length=1, max_length=512)


class IncidentUpdate(WireModel):
    status: Literal["open", "acknowledged"] | None = None
    owner: str | None = Field(default=None, max_length=512)
    comment: str | None = Field(default=None, max_length=10000)


class FollowUpInput(WireModel):
    description: str = Field(min_length=1, max_length=4000)
    due_at: datetime | None = None


class FollowUpUpdate(WireModel):
    completed: bool


class AssetIncidentPolicyInput(WireModel):
    owner: str | None = Field(max_length=512)
    freshness_sla_seconds: int | None = Field(default=None, gt=0)


class IncidentView(WireModel):
    id: str
    asset_id: str
    kind: str
    title: str
    status: IncidentStatus
    owner: str | None
    version: int
    created_at: datetime
    updated_at: datetime


class IncidentPage(WireModel):
    env: Environment
    items: list[IncidentView]
    next_cursor: str | None


class ActivityPage(WireModel):
    env: Environment
    items: list[dict[str, Any]]
    next_cursor: str | None


class AssetIncidentPolicyPage(WireModel):
    env: Environment
    items: list[dict[str, Any]]
    next_cursor: str | None


def _connection():
    dsn = os.environ.get("PHLO_RUN_EVIDENCE_DB_URL")
    if not dsn:
        raise BackendUnavailableError("Durable incident storage is unavailable.")
    try:
        return psycopg2.connect(dsn)
    except psycopg2.Error as exc:
        raise BackendUnavailableError("Durable incident storage is unavailable.") from exc


@contextmanager
def _transaction() -> Iterator[Any]:
    connection = _connection()
    try:
        yield connection
        connection.commit()
    except psycopg2.Error as exc:
        connection.rollback()
        raise BackendUnavailableError("Incident storage is unavailable.") from exc
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def initialize_incidents() -> None:
    """Apply the additive incident schema to the configured Phlo database."""
    schema = Path(__file__).parent / "sql" / "001_incidents.sql"
    with _transaction() as connection, connection.cursor() as cursor:
        cursor.execute(schema.read_text(encoding="utf-8"))


def _actor(request: Request) -> str:
    principal = get_request_principal(request)
    if principal is None:
        raise HTTPException(status_code=401, detail="Authentication required.")
    return principal.subject


def _encode_cursor(env: Environment, kind: str, timestamp: datetime, identity: str) -> str:
    payload = json.dumps(
        {"env": env, "kind": kind, "at": timestamp.astimezone(UTC).isoformat(), "id": identity},
        separators=(",", ":"),
    ).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def _decode_cursor(cursor: str | None, env: Environment, kind: str) -> tuple[datetime, str] | None:
    if cursor is None:
        return None
    if len(cursor) > 1024:
        raise HTTPException(status_code=400, detail="Invalid or cross-environment cursor.")
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        payload = json.loads(raw)
        if payload["env"] != env or payload["kind"] != kind:
            raise ValueError
        timestamp = datetime.fromisoformat(payload["at"])
        identity = payload["id"]
        if timestamp.tzinfo is None or not isinstance(identity, str) or not identity:
            raise ValueError
        return timestamp, identity
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid or cross-environment cursor.") from exc


def _encode_policy_cursor(env: Environment, asset_id: str) -> str:
    payload = json.dumps({"env": env, "kind": "incident-policies", "asset_id": asset_id}).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def _decode_policy_cursor(cursor: str | None, env: Environment) -> str | None:
    if cursor is None:
        return None
    if len(cursor) > 1024:
        raise HTTPException(status_code=400, detail="Invalid or cross-environment cursor.")
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        payload = json.loads(raw)
        if (
            not isinstance(payload, dict)
            or payload["env"] != env
            or payload["kind"] != "incident-policies"
        ):
            raise ValueError
        asset_id = payload["asset_id"]
        if not isinstance(asset_id, str) or not asset_id:
            raise ValueError
        return asset_id
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid or cross-environment cursor.") from exc


def _row(row: tuple[Any, ...]) -> IncidentView:
    return IncidentView.model_validate(
        dict(
            zip(
                (
                    "id",
                    "asset_id",
                    "kind",
                    "title",
                    "status",
                    "owner",
                    "version",
                    "created_at",
                    "updated_at",
                ),
                row,
                strict=True,
            )
        )
    )


def _view_from_json(value: dict[str, Any]) -> IncidentView:
    return IncidentView.model_validate_json(json.dumps(value, default=str))


def _event(
    cursor: Any, incident_id: str, env: Environment, actor: str, kind: str, payload: dict[str, Any]
) -> None:
    safe_payload = redact_payload(payload)
    cursor.execute(
        "INSERT INTO phlo.incident_event(event_id,incident_id,env,actor,kind,payload) VALUES (%s,%s,%s,%s,%s,%s)",
        (uuid4().hex, incident_id, env, actor, kind, json.dumps(safe_payload, default=str)),
    )


def _idempotent(
    cursor: Any,
    env: Environment,
    actor: str,
    action_target: str,
    key: str,
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    if not key.strip():
        raise HTTPException(status_code=422, detail="Idempotency-Key cannot be blank.")
    digest = _digest(payload)
    cursor.execute(
        "INSERT INTO phlo.incident_command(env,actor,action_target,key,payload_sha256) VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
        (env, actor, action_target, key, digest),
    )
    cursor.execute(
        "SELECT payload_sha256,result FROM phlo.incident_command WHERE env=%s AND actor=%s AND action_target=%s AND key=%s FOR UPDATE",
        (env, actor, action_target, key),
    )
    old_digest, result = cursor.fetchone()
    if old_digest != digest:
        raise HTTPException(
            status_code=409, detail="Idempotency key was reused with different input."
        )
    return result


def _store_idempotent_result(
    cursor: Any, env: Environment, actor: str, action_target: str, key: str, result: dict[str, Any]
) -> None:
    cursor.execute(
        "UPDATE phlo.incident_command SET result=%s WHERE env=%s AND actor=%s AND action_target=%s AND key=%s",
        (json.dumps(result, default=str), env, actor, action_target, key),
    )


def _digest(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


@router.get("/incidents", response_model=IncidentPage)
def list_incidents(
    request: Request,
    env: Environment = Query(),
    limit: PageLimit = 100,
    cursor: str | None = None,
) -> IncidentPage:
    _actor(request)
    decoded = _decode_cursor(cursor, env, "incidents")
    with _transaction() as connection, connection.cursor() as cur:
        if decoded:
            cur.execute(
                """SELECT incident_id,asset_id,kind,title,status,owner,version,created_at,updated_at
                   FROM phlo.incident WHERE env=%s AND (updated_at,incident_id)<(%s,%s)
                   ORDER BY updated_at DESC,incident_id DESC LIMIT %s""",
                (env, *decoded, limit + 1),
            )
        else:
            cur.execute(
                """SELECT incident_id,asset_id,kind,title,status,owner,version,created_at,updated_at
                   FROM phlo.incident WHERE env=%s ORDER BY updated_at DESC,incident_id DESC LIMIT %s""",
                (env, limit + 1),
            )
        rows = cur.fetchall()
    has_more = len(rows) > limit
    page = rows[:limit]
    next_cursor = _encode_cursor(env, "incidents", page[-1][-1], page[-1][0]) if has_more else None
    return IncidentPage(env=env, items=[_row(row) for row in page], next_cursor=next_cursor)


@router.get("/incidents/stats")
def incident_stats(request: Request, env: Environment = Query()) -> dict[str, Any]:
    _actor(request)
    with _transaction() as connection, connection.cursor() as cur:
        cur.execute(
            "SELECT status,count(*) FROM phlo.incident WHERE env=%s GROUP BY status", (env,)
        )
        return {"env": env, "counts": dict(cur.fetchall())}


@router.post("/incidents", status_code=201, response_model=IncidentView)
def create_incident(
    request: Request,
    body: IncidentInput,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=200)],
    env: Environment = Query(),
) -> IncidentView:
    actor = _actor(request)
    with _transaction() as connection, connection.cursor() as cur:
        action_target = f"asset:{body.asset_id}:kind:{body.kind}"
        replay = _idempotent(
            cur, env, actor, action_target, idempotency_key, body.model_dump(mode="json")
        )
        if replay:
            return _view_from_json(replay)
        signal_digest = _digest(body.model_dump(mode="json"))
        cur.execute(
            "SELECT incident_id,payload_sha256 FROM phlo.incident_signal WHERE env=%s AND evidence_id=%s",
            (env, body.evidence_id),
        )
        prior_signal = cur.fetchone()
        if prior_signal:
            if prior_signal[1] != signal_digest:
                raise HTTPException(
                    status_code=409, detail="Evidence identity was reused with different content."
                )
            cur.execute(
                "SELECT incident_id,asset_id,kind,title,status,owner,version,created_at,updated_at FROM phlo.incident WHERE incident_id=%s",
                (prior_signal[0],),
            )
            result = _row(cur.fetchone()).model_dump(mode="json")
            _store_idempotent_result(cur, env, actor, action_target, idempotency_key, result)
            return _view_from_json(result)
        incident_id = uuid4().hex
        cur.execute(
            """INSERT INTO phlo.incident(incident_id,env,asset_id,kind,title,status)
               VALUES (%s,%s,%s,%s,%s,'open') ON CONFLICT(env,asset_id,kind)
               DO UPDATE SET updated_at=now() RETURNING incident_id""",
            (incident_id, env, body.asset_id, body.kind, body.title),
        )
        incident_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO phlo.incident_signal(env,evidence_id,incident_id,payload_sha256) VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING incident_id",
            (env, body.evidence_id, incident_id, signal_digest),
        )
        inserted_signal = cur.fetchone()
        if inserted_signal is None:
            cur.execute(
                "SELECT incident_id,payload_sha256 FROM phlo.incident_signal WHERE env=%s AND evidence_id=%s",
                (env, body.evidence_id),
            )
            existing_id, existing_digest = cur.fetchone()
            if existing_digest != signal_digest:
                raise HTTPException(
                    status_code=409, detail="Evidence identity was reused with different content."
                )
            incident_id = existing_id
        else:
            _event(
                cur,
                incident_id,
                env,
                actor,
                "signal",
                {"kind": body.kind, "evidence": body.evidence},
            )
        cur.execute(
            "SELECT incident_id,asset_id,kind,title,status,owner,version,created_at,updated_at FROM phlo.incident WHERE incident_id=%s",
            (incident_id,),
        )
        result = _row(cur.fetchone()).model_dump(mode="json")
        _store_idempotent_result(cur, env, actor, action_target, idempotency_key, result)
        return _view_from_json(result)


@router.get("/incidents/{incident_id}", response_model=IncidentView)
def incident_detail(request: Request, incident_id: str, env: Environment = Query()) -> IncidentView:
    _actor(request)
    with _transaction() as connection, connection.cursor() as cur:
        cur.execute(
            "SELECT incident_id,asset_id,kind,title,status,owner,version,created_at,updated_at FROM phlo.incident WHERE incident_id=%s AND env=%s",
            (incident_id, env),
        )
        row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Incident not found.")
    return _row(row)


@router.get("/incidents/{incident_id}/timeline")
def incident_timeline(
    request: Request, incident_id: str, env: Environment = Query()
) -> dict[str, Any]:
    _actor(request)
    with _transaction() as connection, connection.cursor() as cur:
        cur.execute(
            "SELECT event_id,actor,kind,payload,occurred_at FROM phlo.incident_event WHERE incident_id=%s AND env=%s ORDER BY occurred_at,event_id",
            (incident_id, env),
        )
        return {
            "items": [
                {"id": r[0], "actor": r[1], "kind": r[2], "payload": r[3], "occurred_at": r[4]}
                for r in cur.fetchall()
            ]
        }


@router.patch("/incidents/{incident_id}", response_model=IncidentView)
def update_incident(
    request: Request,
    incident_id: str,
    body: IncidentUpdate,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=200)],
    env: Environment = Query(),
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> IncidentView:
    actor = _actor(request)
    if not body.model_fields_set:
        raise HTTPException(status_code=422, detail="At least one incident field is required.")
    if "status" in body.model_fields_set and body.status is None:
        raise HTTPException(status_code=422, detail="Incident status cannot be null.")
    if if_match is None or not if_match.isdecimal():
        raise HTTPException(status_code=428, detail="A numeric If-Match version is required.")
    expected_version = int(if_match)
    with _transaction() as connection, connection.cursor() as cur:
        action_target = f"incident:{incident_id}"
        replay = _idempotent(
            cur,
            env,
            actor,
            action_target,
            idempotency_key,
            {
                "incident_id": incident_id,
                "version": expected_version,
                **body.model_dump(mode="json"),
            },
        )
        if replay:
            return _view_from_json(replay)
        cur.execute(
            "SELECT status,owner,version FROM phlo.incident WHERE incident_id=%s AND env=%s FOR UPDATE",
            (incident_id, env),
        )
        old = cur.fetchone()
        if old is None:
            raise HTTPException(status_code=404, detail="Incident not found.")
        if expected_version != old[2]:
            raise HTTPException(status_code=409, detail="Incident version is stale.")
        cur.execute(
            """UPDATE phlo.incident SET status=%s,owner=%s,version=version+1,updated_at=now()
               WHERE incident_id=%s AND env=%s""",
            (
                body.status if "status" in body.model_fields_set else old[0],
                body.owner if "owner" in body.model_fields_set else old[1],
                incident_id,
                env,
            ),
        )
        if body.comment is not None:
            _event(cur, incident_id, env, actor, "comment", {"text": body.comment})
        if body.status is not None or body.owner is not None:
            _event(
                cur,
                incident_id,
                env,
                actor,
                "updated",
                {
                    "before": {"status": old[0], "owner": old[1], "version": old[2]},
                    "after": {**body.model_dump(exclude_none=True), "version": old[2] + 1},
                },
            )
        cur.execute(
            "SELECT incident_id,asset_id,kind,title,status,owner,version,created_at,updated_at FROM phlo.incident WHERE incident_id=%s",
            (incident_id,),
        )
        result = _row(cur.fetchone()).model_dump(mode="json")
        _store_idempotent_result(cur, env, actor, action_target, idempotency_key, result)
        return _view_from_json(result)


@router.put("/incidents/{incident_id}/subscriptions")
def subscribe_incident(
    request: Request,
    incident_id: str,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=200)],
    env: Environment = Query(),
    subscribed: bool = True,
) -> dict[str, Any]:
    actor = _actor(request)
    action_target = f"incident:{incident_id}:subscription"
    payload = {"incident_id": incident_id, "subscribed": subscribed}
    with _transaction() as connection, connection.cursor() as cur:
        replay = _idempotent(cur, env, actor, action_target, idempotency_key, payload)
        if replay:
            return replay
        cur.execute(
            "SELECT 1 FROM phlo.incident WHERE incident_id=%s AND env=%s FOR KEY SHARE",
            (incident_id, env),
        )
        if cur.fetchone() is None:
            raise HTTPException(status_code=404, detail="Incident not found.")
        if subscribed:
            cur.execute(
                "INSERT INTO phlo.incident_subscription(env,incident_id,subject) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
                (env, incident_id, actor),
            )
        else:
            cur.execute(
                "DELETE FROM phlo.incident_subscription WHERE env=%s AND incident_id=%s AND subject=%s",
                (env, incident_id, actor),
            )
        if cur.rowcount:
            _event(cur, incident_id, env, actor, "subscription", {"subscribed": subscribed})
        result = {"incident_id": incident_id, "subscribed": subscribed}
        _store_idempotent_result(cur, env, actor, action_target, idempotency_key, result)
    return result


@router.get("/incidents/{incident_id}/follow-ups")
def list_follow_ups(
    request: Request, incident_id: str, env: Environment = Query()
) -> dict[str, Any]:
    _actor(request)
    with _transaction() as connection, connection.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM phlo.incident WHERE incident_id=%s AND env=%s", (incident_id, env)
        )
        if cur.fetchone() is None:
            raise HTTPException(status_code=404, detail="Incident not found.")
        cur.execute(
            "SELECT follow_up_id,description,due_at,completed_at FROM phlo.incident_follow_up WHERE incident_id=%s AND env=%s ORDER BY due_at NULLS LAST,follow_up_id LIMIT 500",
            (incident_id, env),
        )
        rows = cur.fetchall()
    return {
        "items": [
            {"id": row[0], "description": row[1], "due_at": row[2], "completed_at": row[3]}
            for row in rows
        ]
    }


@router.post("/incidents/{incident_id}/follow-ups", status_code=201)
def create_follow_up(
    request: Request,
    incident_id: str,
    body: FollowUpInput,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=200)],
    env: Environment = Query(),
) -> dict[str, Any]:
    actor = _actor(request)
    action_target = f"incident:{incident_id}:follow-up"
    with _transaction() as connection, connection.cursor() as cur:
        replay = _idempotent(
            cur, env, actor, action_target, idempotency_key, body.model_dump(mode="json")
        )
        if replay:
            return replay
        cur.execute(
            "SELECT 1 FROM phlo.incident WHERE incident_id=%s AND env=%s FOR KEY SHARE",
            (incident_id, env),
        )
        if cur.fetchone() is None:
            raise HTTPException(status_code=404, detail="Incident not found.")
        follow_up_id = uuid4().hex
        cur.execute(
            "INSERT INTO phlo.incident_follow_up(follow_up_id,env,incident_id,description,due_at) VALUES (%s,%s,%s,%s,%s)",
            (follow_up_id, env, incident_id, body.description, body.due_at),
        )
        result = {
            "id": follow_up_id,
            "incident_id": incident_id,
            "description": body.description,
            "due_at": body.due_at,
            "completed_at": None,
        }
        _event(cur, incident_id, env, actor, "follow_up_created", result)
        _store_idempotent_result(cur, env, actor, action_target, idempotency_key, result)
        return result


@router.patch("/incidents/{incident_id}/follow-ups/{follow_up_id}")
def update_follow_up(
    request: Request,
    incident_id: str,
    follow_up_id: str,
    body: FollowUpUpdate,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=200)],
    env: Environment = Query(),
) -> dict[str, Any]:
    actor = _actor(request)
    action_target = f"incident:{incident_id}:follow-up:{follow_up_id}"
    payload = {"follow_up_id": follow_up_id, "completed": body.completed}
    with _transaction() as connection, connection.cursor() as cur:
        replay = _idempotent(cur, env, actor, action_target, idempotency_key, payload)
        if replay:
            return replay
        cur.execute(
            "UPDATE phlo.incident_follow_up SET completed_at=CASE WHEN %s THEN COALESCE(completed_at,now()) ELSE NULL END WHERE follow_up_id=%s AND incident_id=%s AND env=%s RETURNING follow_up_id,description,due_at,completed_at",
            (body.completed, follow_up_id, incident_id, env),
        )
        row = cur.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Follow-up not found.")
        result = {"id": row[0], "description": row[1], "due_at": row[2], "completed_at": row[3]}
        _event(cur, incident_id, env, actor, "follow_up_updated", result)
        _store_idempotent_result(cur, env, actor, action_target, idempotency_key, result)
        return result


@router.get("/assets/{asset_id:path}/incident-policy")
def get_asset_incident_policy(
    request: Request, asset_id: str, env: Environment = Query()
) -> dict[str, Any]:
    _actor(request)
    with _transaction() as connection, connection.cursor() as cur:
        cur.execute(
            "SELECT asset_id,owner,freshness_sla_seconds,version FROM phlo.asset_incident_policy WHERE env=%s AND asset_id=%s",
            (env, asset_id),
        )
        row = cur.fetchone()
    if row is None:
        return {
            "env": env,
            "asset_id": asset_id,
            "owner": None,
            "freshness_sla_seconds": None,
            "version": 0,
        }
    return {
        "env": env,
        "asset_id": row[0],
        "owner": row[1],
        "freshness_sla_seconds": row[2],
        "version": row[3],
    }


@router.get("/incident-policies", response_model=AssetIncidentPolicyPage)
def list_asset_incident_policies(
    request: Request,
    env: Environment = Query(),
    limit: PageLimit = 100,
    cursor: str | None = None,
) -> AssetIncidentPolicyPage:
    _actor(request)
    after_asset_id = _decode_policy_cursor(cursor, env)
    with _transaction() as connection, connection.cursor() as cur:
        if after_asset_id is None:
            cur.execute(
                "SELECT asset_id,owner,freshness_sla_seconds,version FROM phlo.asset_incident_policy WHERE env=%s ORDER BY asset_id LIMIT %s",
                (env, limit + 1),
            )
        else:
            cur.execute(
                "SELECT asset_id,owner,freshness_sla_seconds,version FROM phlo.asset_incident_policy WHERE env=%s AND asset_id>%s ORDER BY asset_id LIMIT %s",
                (env, after_asset_id, limit + 1),
            )
        rows = cur.fetchall()
    has_more = len(rows) > limit
    page = rows[:limit]
    return AssetIncidentPolicyPage(
        env=env,
        items=[
            {
                "asset_id": row[0],
                "owner": row[1],
                "freshness_sla_seconds": row[2],
                "version": row[3],
            }
            for row in page
        ],
        next_cursor=_encode_policy_cursor(env, page[-1][0]) if has_more else None,
    )


@router.put("/assets/{asset_id:path}/incident-policy")
def put_asset_incident_policy(
    request: Request,
    asset_id: str,
    body: AssetIncidentPolicyInput,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=200)],
    env: Environment = Query(),
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> dict[str, Any]:
    actor = _actor(request)
    if if_match is None or not if_match.isdecimal():
        raise HTTPException(status_code=428, detail="A numeric If-Match version is required.")
    expected_version = int(if_match)
    action_target = f"asset:{asset_id}:incident-policy"
    with _transaction() as connection, connection.cursor() as cur:
        replay = _idempotent(
            cur,
            env,
            actor,
            action_target,
            idempotency_key,
            {"version": expected_version, **body.model_dump(mode="json")},
        )
        if replay:
            return replay
        cur.execute(
            """INSERT INTO phlo.asset_incident_policy(env,asset_id,owner,freshness_sla_seconds,version)
               VALUES (%s,%s,%s,%s,1) ON CONFLICT(env,asset_id) DO UPDATE
               SET owner=EXCLUDED.owner,freshness_sla_seconds=EXCLUDED.freshness_sla_seconds,
                   version=phlo.asset_incident_policy.version+1
               WHERE phlo.asset_incident_policy.version=%s RETURNING version""",
            (env, asset_id, body.owner, body.freshness_sla_seconds, expected_version),
        )
        row = cur.fetchone()
        if row is None:
            raise HTTPException(status_code=409, detail="Asset policy version is stale.")
        result = {
            "env": env,
            "asset_id": asset_id,
            **body.model_dump(mode="json"),
            "version": row[0],
        }
        _store_idempotent_result(cur, env, actor, action_target, idempotency_key, result)
    return result


@router.get("/activity", response_model=ActivityPage)
def activity(
    request: Request,
    env: Environment = Query(),
    limit: PageLimit = 100,
    cursor: str | None = None,
) -> ActivityPage:
    _actor(request)
    decoded = _decode_cursor(cursor, env, "activity")
    with _transaction() as connection, connection.cursor() as cur:
        if decoded:
            cur.execute(
                """SELECT event_id,incident_id,actor,kind,payload,occurred_at FROM phlo.incident_event
                WHERE env=%s AND (occurred_at,event_id)<(%s,%s) ORDER BY occurred_at DESC,event_id DESC LIMIT %s""",
                (env, *decoded, limit + 1),
            )
        else:
            cur.execute(
                "SELECT event_id,incident_id,actor,kind,payload,occurred_at FROM phlo.incident_event WHERE env=%s ORDER BY occurred_at DESC,event_id DESC LIMIT %s",
                (env, limit + 1),
            )
        rows = cur.fetchall()
    has_more = len(rows) > limit
    page = rows[:limit]
    next_cursor = _encode_cursor(env, "activity", page[-1][-1], page[-1][0]) if has_more else None
    return ActivityPage(
        env=env,
        items=[
            {
                "id": r[0],
                "incident_id": r[1],
                "actor": r[2],
                "kind": r[3],
                "payload": r[4],
                "occurred_at": r[5],
            }
            for r in page
        ],
        next_cursor=next_cursor,
    )
