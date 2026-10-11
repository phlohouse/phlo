"""Durable, environment-scoped incident and activity API."""

from __future__ import annotations

import base64
import asyncio
import hashlib
import json
from contextlib import asynccontextmanager, contextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Iterator, Literal
from uuid import uuid4

import psycopg2
from fastapi import APIRouter, Header, HTTPException, Query, Request
from pydantic import Field, field_validator

from phlo.audit.events import AuditEventType, CanonicalAuditEvent
from phlo.compliance.signatures.types import SignatureMeaning, SignatureRequest
from phlo.config.process import get_process_settings
from phlo.logging import get_logger
from phlo_api.api.authentication import get_request_principal
from phlo_api.errors import BackendUnavailableError
from phlo_api.v1_contract import Environment, WireModel
from phlo.run_evidence.redaction import redact_payload

logger = get_logger(__name__)


@asynccontextmanager
async def _effect_lifespan(_application: Any):
    """Resume authorized notification deliveries after restart, without a user session."""

    async def recover():
        while True:
            try:
                await asyncio.to_thread(recover_incident_notifications)
            except Exception:
                logger.warning("incident_notification_recovery_unavailable")
            await asyncio.sleep(60)

    task = (
        asyncio.create_task(recover()) if get_process_settings().phlo_run_evidence_db_url else None
    )
    try:
        yield
    finally:
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task


router = APIRouter(tags=["v1 incidents"], lifespan=_effect_lifespan)
IncidentStatus = Literal["open", "acknowledged", "resolved"]
SchemaDecisionSide = Literal["source", "target"]
PageLimit = Annotated[int, Query(ge=1, le=500)]
Severity = Literal["low", "medium", "high"]
_INCIDENT_COLUMNS = """incident_id,asset_id,kind,title,status,owner,version,created_at,updated_at,
severity,asset_ids,description,notify_qa,pause_downstream"""


class IncidentInput(WireModel):
    asset_id: str = Field(min_length=1, max_length=512)
    kind: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=500)
    evidence: dict[str, Any]
    evidence_id: str = Field(min_length=1, max_length=512)
    severity: Severity = "medium"
    owner: str | None = Field(default=None, max_length=512)
    asset_ids: list[str] = Field(default_factory=list, max_length=100)
    description: str = Field(default="", max_length=10000)
    notify_qa: bool = False
    pause_downstream: bool = False

    @field_validator("asset_ids")
    @classmethod
    def validate_assets(cls, value: list[str]) -> list[str]:
        if any(not asset.strip() or len(asset) > 512 for asset in value):
            raise ValueError("asset IDs must contain 1 to 512 non-blank characters")
        return list(dict.fromkeys(value))


class IncidentUpdate(WireModel):
    status: IncidentStatus | None = None
    owner: str | None = Field(default=None, max_length=512)
    comment: str | None = Field(default=None, max_length=10000)
    signature_id: str | None = Field(default=None, min_length=1, max_length=100)
    severity: Severity | None = None
    asset_ids: list[str] | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=10000)
    notify_qa: bool | None = None
    pause_downstream: bool | None = None
    query_id: str | None = Field(default=None, min_length=1, max_length=100)
    retry_effects: bool = False

    @field_validator("asset_ids")
    @classmethod
    def validate_assets(cls, value: list[str] | None) -> list[str] | None:
        return IncidentInput.validate_assets(value) if value is not None else None


class FollowUpInput(WireModel):
    description: str = Field(min_length=1, max_length=4000)
    due_at: datetime | None = None


class FollowUpUpdate(WireModel):
    completed: bool


class AssetIncidentPolicyInput(WireModel):
    owner: str | None = Field(max_length=512)
    freshness_sla_seconds: int | None = Field(default=None, gt=0)


class SchemaDecisionInput(WireModel):
    source_ref: str = Field(min_length=1, max_length=256)
    target_ref: str = Field(min_length=1, max_length=256)
    source_hash: str = Field(min_length=1, max_length=256)
    target_hash: str = Field(min_length=1, max_length=256)
    table_key: str = Field(min_length=1, max_length=512)
    columns: dict[str, SchemaDecisionSide] = Field(min_length=1, max_length=500)
    justification: str = Field(min_length=1, max_length=4000)

    @field_validator(
        "source_ref", "target_ref", "source_hash", "target_hash", "table_key", "justification"
    )
    @classmethod
    def reject_blank_values(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("value cannot be blank")
        return value

    @field_validator("columns")
    @classmethod
    def validate_column_names(
        cls, value: dict[str, SchemaDecisionSide]
    ) -> dict[str, SchemaDecisionSide]:
        if any(not name.strip() or len(name) > 512 for name in value):
            raise ValueError("column names must contain 1 to 512 non-blank characters")
        return value


class SchemaDecision(SchemaDecisionInput):
    id: str
    incident_id: str
    env: Environment
    actor: str
    created_at: datetime


class SchemaDecisionPage(WireModel):
    env: Environment
    incident_id: str
    items: list[SchemaDecision]


class IncidentEffect(WireModel):
    id: str
    kind: Literal["notification", "pause"]
    status: Literal["pending", "delivering", "delivered", "failed"]
    attempts: int
    error: str | None


class FollowUpView(WireModel):
    id: str
    description: str
    due_at: datetime | None
    completed_at: datetime | None


class FollowUpPage(WireModel):
    items: list[FollowUpView]


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
    severity: Severity = "medium"
    asset_ids: list[str] = Field(default_factory=list)
    description: str = ""
    notify_qa: bool = False
    pause_downstream: bool = False
    effects: list[IncidentEffect] = Field(default_factory=list)
    layers: list[str] = Field(default_factory=list)


class IncidentPage(WireModel):
    env: Environment
    items: list[IncidentView]
    next_cursor: str | None


class IncidentStatsResponse(WireModel):
    env: Environment
    counts: dict[str, int]


class IncidentTimelineEvent(WireModel):
    id: str
    actor: str
    kind: str
    payload: dict[str, Any]
    occurred_at: datetime


class IncidentTimelineResponse(WireModel):
    items: list[IncidentTimelineEvent]


class ActivityPage(WireModel):
    env: Environment
    items: list[dict[str, Any]]
    next_cursor: str | None


class AssetIncidentPolicyPage(WireModel):
    env: Environment
    items: list[dict[str, Any]]
    next_cursor: str | None


def _connection():
    dsn = get_process_settings().phlo_run_evidence_db_url
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
        cursor.execute(
            schema.with_name("003_incident_query_parity.sql").read_text(encoding="utf-8")
        )


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
    view = IncidentView.model_validate(
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
                    "severity",
                    "asset_ids",
                    "description",
                    "notify_qa",
                    "pause_downstream",
                ),
                row,
                strict=True,
            )
        )
    )
    view.asset_ids = view.asset_ids or [view.asset_id]
    return view


def _view_from_json(value: dict[str, Any]) -> IncidentView:
    return IncidentView.model_validate_json(json.dumps(value, default=str))


def _schema_decision_from_json(value: dict[str, Any]) -> SchemaDecision:
    return SchemaDecision.model_validate_json(json.dumps(value, default=str))


def _schema_decision_row(row: tuple[Any, ...]) -> SchemaDecision:
    return SchemaDecision.model_validate(
        dict(
            zip(
                (
                    "id",
                    "incident_id",
                    "env",
                    "source_ref",
                    "target_ref",
                    "source_hash",
                    "target_hash",
                    "table_key",
                    "columns",
                    "actor",
                    "justification",
                    "created_at",
                ),
                row,
                strict=True,
            )
        )
    )


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


def persist_query_execution(
    *,
    query_id: str,
    env: Environment,
    actor: str,
    nessie_ref: str,
    statement: str,
    executed_statement: str,
    result: dict[str, Any],
    provider_query_id: str | None,
) -> None:
    """Record server-produced execution identity, never a browser result snapshot."""
    with _transaction() as connection, connection.cursor() as cur:
        cur.execute(
            """INSERT INTO phlo.query_execution(query_id,env,actor,nessie_ref,engine,statement,
               statement_sha256,executed_statement,result,result_sha256,provider_query_id,completed_at)
               VALUES (%s,%s,%s,%s,'trino',%s,%s,%s,%s,%s,%s,now())""",
            (
                query_id,
                env,
                actor,
                nessie_ref,
                statement,
                hashlib.sha256(statement.encode()).hexdigest(),
                executed_statement,
                json.dumps(result, default=str),
                _digest(result),
                provider_query_id,
            ),
        )


def list_query_executions(env: Environment, nessie_ref: str, limit: int) -> list[dict[str, Any]]:
    """List shared execution identities without confidential statements or results."""
    with _transaction() as connection, connection.cursor() as cur:
        cur.execute(
            """SELECT query_id,env,nessie_ref,engine,statement_sha256,completed_at
               FROM phlo.query_execution WHERE env=%s AND nessie_ref=%s
               ORDER BY completed_at DESC,query_id DESC LIMIT %s""",
            (env, nessie_ref, limit),
        )
        return [
            dict(
                zip(
                    ("id", "env", "nessie_ref", "engine", "sql_hash", "completed_at"),
                    row,
                    strict=True,
                )
            )
            for row in cur.fetchall()
        ]


def load_query_execution(query_id: str, env: Environment, actor: str) -> dict[str, Any] | None:
    """Return confidential evidence only to its initiating actor in the same environment."""
    with _transaction() as connection, connection.cursor() as cur:
        cur.execute(
            """SELECT nessie_ref,statement,result,completed_at FROM phlo.query_execution
               WHERE query_id=%s AND env=%s AND actor=%s""",
            (query_id, env, actor),
        )
        row = cur.fetchone()
    return (
        dict(zip(("nessie_ref", "statement", "result", "completed_at"), row, strict=True))
        if row
        else None
    )


def _authorize(
    request: Request, action: str, resource_type: str, identity: str | None = None
) -> None:
    from phlo_api.security_manifest import OperationSpec, enforce_http_operation

    key = {"asset": "asset_id", "service": "service_id", "run": "schedule_id"}.get(resource_type)
    keys = ("env", key) if identity is not None and key else ("env",)
    sources = (
        (("env", "query"), (key, "path")) if identity is not None and key else (("env", "query"),)
    )
    asyncio.run(
        enforce_http_operation(
            request,
            OperationSpec(
                operation_name="update_incident",
                surface="http",
                action=action,
                resource_type=resource_type,
                resource_keys=keys,
                resource_sources=sources,
            ),
            {key: identity} if identity is not None and key else {},
        )
    )


def _pin_query(
    cur: Any, request: Request, incident_id: str, env: Environment, query_id: str
) -> bool:
    _authorize(request, "dataset.query", "project")
    cur.execute(
        """SELECT nessie_ref,engine,statement_sha256,result_sha256,provider_query_id
           FROM phlo.query_execution WHERE query_id=%s AND env=%s AND actor=%s""",
        (query_id, env, _actor(request)),
    )
    execution = cur.fetchone()
    if execution is None:
        raise HTTPException(status_code=404, detail="Completed query execution not found.")
    cur.execute(
        """INSERT INTO phlo.incident_query_evidence(env,incident_id,query_id,actor)
           VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING query_id""",
        (env, incident_id, query_id, _actor(request)),
    )
    if cur.fetchone() is None:
        return False
    _event(
        cur,
        incident_id,
        env,
        _actor(request),
        "query_evidence",
        {
            "query_id": query_id,
            "env": env,
            "nessie_ref": execution[0],
            "engine": execution[1],
            "statement_sha256": execution[2],
            "result_sha256": execution[3],
            "provider_query_id": execution[4],
        },
    )
    return True


def _preflight_effects(
    request: Request,
    env: Environment,
    *,
    asset_ids: list[str],
    notify_qa: bool,
    pause_downstream: bool,
) -> dict[str, dict[str, Any]]:
    effects: dict[str, dict[str, Any]] = {}
    if notify_qa:
        from phlo_api.observatory_api.settings import (
            NotificationUnavailableError,
            preflight_incident_notification,
        )

        _authorize(request, "service.manage", "service", "alerting")
        try:
            preflight_incident_notification(notify_qa=True)
        except NotificationUnavailableError as exc:
            raise HTTPException(
                status_code=503, detail="QA notification destination is unavailable."
            ) from exc
        effects["notification"] = {}
    if pause_downstream:
        from phlo_api.api.v1_jobs import preflight_incident_pause

        targets = asyncio.run(preflight_incident_pause(request, env=env, asset_ids=asset_ids))
        for target in targets:
            _authorize(request, "run.manage", "run", target["schedule_id"])
        effects["pause"] = {"targets": targets}
    return effects


def _enqueue_effects(
    cur: Any,
    env: Environment,
    actor: str,
    incident: IncidentView,
    effects: dict[str, dict[str, Any]],
) -> None:
    for kind, details in effects.items():
        payload = {
            "severity": incident.severity,
            "owner": incident.owner,
            "asset_ids": incident.asset_ids,
            "notify_qa": incident.notify_qa,
            **details,
        }
        cur.execute(
            """INSERT INTO phlo.incident_effect(effect_id,env,incident_id,incident_version,actor,kind,payload)
               VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
            (uuid4().hex, env, incident.id, incident.version, actor, kind, json.dumps(payload)),
        )


def recover_incident_notifications() -> None:
    """Retry bounded due notifications only; a stored preference cannot authorize pipeline control."""
    with _transaction() as connection, connection.cursor() as cur:
        cur.execute(
            """SELECT DISTINCT env,incident_id FROM phlo.incident_effect
               WHERE kind='notification' AND
                 (status='pending' OR (status='failed' AND updated_at < now()-interval '1 minute')
                  OR (status='delivering' AND updated_at < now()-interval '5 minutes')) LIMIT 100"""
        )
        due = cur.fetchall()
    for env, incident_id in due:
        dispatch_incident_effects(None, env=env, incident_id=incident_id)


def dispatch_incident_effects(
    request: Request | None, *, env: Environment, incident_id: str
) -> None:
    """Claim durable attempts, then deliver outside the transaction. Retries are at-least-once.

    User retries are restricted to the initiating actor. A trusted notification worker may
    pass no request to deliver already authorized notification effects, never pipeline controls.
    A stale delivering attempt may have reached the sink;
    its retry retains effect identity but cannot promise exactly-once external delivery.
    """
    actor = _actor(request) if request is not None else None
    with _transaction() as connection, connection.cursor() as cur:
        cur.execute(
            """UPDATE phlo.incident_effect SET status='delivering',attempts=attempts+1,updated_at=now()
               WHERE env=%s AND incident_id=%s AND (actor=%s OR (%s IS NULL AND kind='notification'))
                 AND (status IN ('pending','failed') OR
                      (status='delivering' AND updated_at < now()-interval '5 minutes'))
               RETURNING effect_id,kind,payload,actor,attempts""",
            (env, incident_id, actor, actor),
        )
        attempts = cur.fetchall()
    for effect_id, kind, payload, initiating_actor, attempt_number in attempts:
        status, error = "delivered", None
        try:
            if kind == "notification":
                from phlo_api.observatory_api.settings import send_incident_notification

                if request is not None:
                    _authorize(request, "service.manage", "service", "alerting")
                confirmed = send_incident_notification(
                    request,
                    env=env,
                    incident_id=incident_id,
                    effect_id=effect_id,
                    **payload,
                )
                if not confirmed:
                    raise RuntimeError("Notification delivery was not confirmed.")
            else:
                from phlo_api.api.v1_jobs import pause_incident_downstream

                assert request is not None  # No-request workers can only claim notifications.
                for target in payload["targets"]:
                    _authorize(request, "run.manage", "run", target["schedule_id"])
                results = asyncio.run(
                    pause_incident_downstream(
                        request,
                        env=env,
                        incident_id=incident_id,
                        effect_id=effect_id,
                        targets=payload["targets"],
                    )
                )
                if {result["schedule_id"] for result in results} != {
                    target["schedule_id"] for target in payload["targets"]
                } or any(
                    result["status"] not in {"paused", "already_paused"} for result in results
                ):
                    raise RuntimeError("Downstream pause was not confirmed for every target.")
        except Exception:
            status, error = (
                "failed",
                "Delivery failed or is unconfirmed. Retry may repeat external delivery.",
            )
        with _transaction() as connection, connection.cursor() as cur:
            cur.execute(
                """UPDATE phlo.incident_effect SET status=%s,error=%s,updated_at=now()
                   WHERE effect_id=%s AND attempts=%s AND status='delivering'""",
                (status, error, effect_id, attempt_number),
            )
            if cur.rowcount:
                _event(
                    cur,
                    incident_id,
                    env,
                    initiating_actor,
                    "effect_delivery",
                    {
                        "effect_id": effect_id,
                        "effect": kind,
                        "status": status,
                        "error": error,
                    },
                )


def _effect_status(cur: Any, incident_id: str, env: Environment) -> list[IncidentEffect]:
    cur.execute(
        """SELECT effect_id,kind,status,attempts,error FROM phlo.incident_effect
           WHERE incident_id=%s AND env=%s ORDER BY updated_at,effect_id""",
        (incident_id, env),
    )
    return [
        IncidentEffect.model_validate(
            dict(zip(("id", "kind", "status", "attempts", "error"), row, strict=True))
        )
        for row in cur.fetchall()
    ]


def _deliver_and_view(request: Request, env: Environment, view: IncidentView) -> IncidentView:
    if view.notify_qa or view.pause_downstream:
        dispatch_incident_effects(request, env=env, incident_id=view.id)
        with _transaction() as connection, connection.cursor() as cur:
            view.effects = _effect_status(cur, view.id, env)
    return view


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
                f"""SELECT {_INCIDENT_COLUMNS}
                   FROM phlo.incident WHERE env=%s AND (updated_at,incident_id)<(%s,%s)
                   ORDER BY updated_at DESC,incident_id DESC LIMIT %s""",
                (env, *decoded, limit + 1),
            )
        else:
            cur.execute(
                f"""SELECT {_INCIDENT_COLUMNS}
                   FROM phlo.incident WHERE env=%s ORDER BY updated_at DESC,incident_id DESC LIMIT %s""",
                (env, limit + 1),
            )
        rows = cur.fetchall()
    has_more = len(rows) > limit
    page = rows[:limit]
    next_cursor = _encode_cursor(env, "incidents", page[-1][8], page[-1][0]) if has_more else None
    return IncidentPage(env=env, items=[_row(row) for row in page], next_cursor=next_cursor)


@router.get("/incidents/stats", response_model=IncidentStatsResponse)
def incident_stats(request: Request, env: Environment = Query()) -> IncidentStatsResponse:
    _actor(request)
    with _transaction() as connection, connection.cursor() as cur:
        cur.execute(
            "SELECT status,count(*) FROM phlo.incident WHERE env=%s GROUP BY status", (env,)
        )
        return IncidentStatsResponse(env=env, counts=dict(cur.fetchall()))


@router.post("/incidents", status_code=201, response_model=IncidentView)
def create_incident(
    request: Request,
    body: IncidentInput,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=200)],
    env: Environment = Query(),
) -> IncidentView:
    actor = _actor(request)
    asset_ids = list(dict.fromkeys([body.asset_id, *body.asset_ids]))
    for asset in asset_ids:
        if asset != body.asset_id:
            _authorize(request, "asset.manage", "asset", asset)
    payload = body.model_dump(mode="json", exclude_defaults=True)
    with _transaction() as connection, connection.cursor() as cur:
        action_target = f"asset:{body.asset_id}:kind:{body.kind}"
        replay = _idempotent(cur, env, actor, action_target, idempotency_key, payload)
        if replay:
            return _deliver_and_view(request, env, _view_from_json(replay))
        cur.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (f"{env}:{body.evidence_id}",)
        )
        signal_digest = _digest(payload)
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
                f"SELECT {_INCIDENT_COLUMNS} FROM phlo.incident WHERE incident_id=%s",
                (prior_signal[0],),
            )
            result = _row(cur.fetchone()).model_dump(mode="json")
            _store_idempotent_result(cur, env, actor, action_target, idempotency_key, result)
            return _view_from_json(result)
        effects = _preflight_effects(
            request,
            env,
            asset_ids=asset_ids,
            notify_qa=body.notify_qa,
            pause_downstream=body.pause_downstream,
        )
        incident_id = uuid4().hex
        cur.execute(
            """INSERT INTO phlo.incident(incident_id,env,asset_id,kind,title,status,
                    severity,owner,asset_ids,description,notify_qa,pause_downstream,origin)
               VALUES (%s,%s,%s,%s,%s,'open',%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT(env,asset_id,kind) WHERE origin='signal'
               DO UPDATE SET updated_at=now() RETURNING incident_id""",
            (
                incident_id,
                env,
                body.asset_id,
                body.kind,
                body.title,
                body.severity,
                body.owner,
                json.dumps(list(dict.fromkeys([body.asset_id, *body.asset_ids]))),
                body.description,
                body.notify_qa,
                body.pause_downstream,
                "manual" if body.evidence_id.startswith("manual:") else "signal",
            ),
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
            f"SELECT {_INCIDENT_COLUMNS} FROM phlo.incident WHERE incident_id=%s",
            (incident_id,),
        )
        view = _row(cur.fetchone())
        _enqueue_effects(cur, env, actor, view, effects)
        result = view.model_dump(mode="json")
        _store_idempotent_result(cur, env, actor, action_target, idempotency_key, result)
    return _deliver_and_view(request, env, view)


@router.get("/incidents/{incident_id}", response_model=IncidentView)
def incident_detail(request: Request, incident_id: str, env: Environment = Query()) -> IncidentView:
    _actor(request)
    with _transaction() as connection, connection.cursor() as cur:
        cur.execute(
            f"SELECT {_INCIDENT_COLUMNS} FROM phlo.incident WHERE incident_id=%s AND env=%s",
            (incident_id, env),
        )
        row = cur.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Incident not found.")
        view = _row(row)
        view.effects = _effect_status(cur, incident_id, env)
    return view


@router.get("/incidents/{incident_id}/timeline", response_model=IncidentTimelineResponse)
def incident_timeline(
    request: Request, incident_id: str, env: Environment = Query()
) -> IncidentTimelineResponse:
    _actor(request)
    with _transaction() as connection, connection.cursor() as cur:
        cur.execute(
            "SELECT event_id,actor,kind,payload,occurred_at FROM phlo.incident_event WHERE incident_id=%s AND env=%s ORDER BY occurred_at,event_id",
            (incident_id, env),
        )
        return IncidentTimelineResponse(
            items=[
                IncidentTimelineEvent(
                    id=r[0], actor=r[1], kind=r[2], payload=r[3], occurred_at=r[4]
                )
                for r in cur.fetchall()
            ]
        )


_SCHEMA_DECISION_COLUMNS = """decision_id,incident_id,env,source_ref,target_ref,source_hash,
target_hash,table_key,columns,actor,justification,created_at"""


def load_schema_decisions(
    incident_id: str,
    env: Environment,
    source_ref: str,
    target_ref: str,
    source_hash: str,
    target_hash: str,
) -> list[SchemaDecision]:
    """Load decisions only when every incident and branch revision binding matches."""
    with _transaction() as connection, connection.cursor() as cur:
        cur.execute(
            f"""SELECT {_SCHEMA_DECISION_COLUMNS} FROM phlo.incident_schema_decision
                WHERE incident_id=%s AND env=%s AND source_ref=%s AND target_ref=%s
                  AND source_hash=%s AND target_hash=%s
                ORDER BY table_key,decision_id""",  # noqa: S608  # reason: Fixed column list; values are bound.
            (incident_id, env, source_ref, target_ref, source_hash, target_hash),
        )
        return [_schema_decision_row(row) for row in cur.fetchall()]


@router.get("/incidents/{incident_id}/schema-decisions", response_model=SchemaDecisionPage)
def list_schema_decisions(
    request: Request, incident_id: str, env: Environment = Query()
) -> SchemaDecisionPage:
    _actor(request)
    with _transaction() as connection, connection.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM phlo.incident WHERE incident_id=%s AND env=%s",
            (incident_id, env),
        )
        if cur.fetchone() is None:
            raise HTTPException(status_code=404, detail="Incident not found.")
        cur.execute(
            f"""SELECT {_SCHEMA_DECISION_COLUMNS} FROM phlo.incident_schema_decision
                WHERE incident_id=%s AND env=%s ORDER BY created_at,decision_id LIMIT 500""",  # noqa: S608  # reason: Fixed column list; values are bound.
            (incident_id, env),
        )
        items = [_schema_decision_row(row) for row in cur.fetchall()]
    return SchemaDecisionPage(env=env, incident_id=incident_id, items=items)


@router.post(
    "/incidents/{incident_id}/schema-decisions",
    status_code=201,
    response_model=SchemaDecision,
)
def create_schema_decision(
    request: Request,
    incident_id: str,
    body: SchemaDecisionInput,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=200)],
    env: Environment = Query(),
) -> SchemaDecision:
    actor = _actor(request)
    payload = body.model_dump(mode="json")
    action_target = f"incident:{incident_id}:schema-decision"
    with _transaction() as connection, connection.cursor() as cur:
        replay = _idempotent(cur, env, actor, action_target, idempotency_key, payload)
        if replay:
            return _schema_decision_from_json(replay)
        cur.execute(
            "SELECT 1 FROM phlo.incident WHERE incident_id=%s AND env=%s FOR KEY SHARE",
            (incident_id, env),
        )
        if cur.fetchone() is None:
            raise HTTPException(status_code=404, detail="Incident not found.")
        decision_id = uuid4().hex
        cur.execute(
            """INSERT INTO phlo.incident_schema_decision(
                   decision_id,incident_id,env,source_ref,target_ref,source_hash,target_hash,
                   table_key,columns,actor,justification)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (incident_id,env,source_ref,target_ref,source_hash,target_hash,table_key)
               DO NOTHING RETURNING created_at""",
            (
                decision_id,
                incident_id,
                env,
                body.source_ref,
                body.target_ref,
                body.source_hash,
                body.target_hash,
                body.table_key,
                json.dumps(body.columns),
                actor,
                body.justification,
            ),
        )
        inserted = cur.fetchone()
        if inserted is None:
            raise HTTPException(
                status_code=409,
                detail="An immutable decision already exists for this table and revision tuple.",
            )
        result = SchemaDecision(
            id=decision_id,
            incident_id=incident_id,
            env=env,
            actor=actor,
            created_at=inserted[0],
            **body.model_dump(),
        )
        result_json = result.model_dump(mode="json")
        _event(cur, incident_id, env, actor, "schema_decision", result_json)
        _store_idempotent_result(cur, env, actor, action_target, idempotency_key, result_json)
        return result


def _validate_incident_update(body: IncidentUpdate, if_match: str | None) -> int:
    """Validate update fields and return the required optimistic-lock version."""
    if not body.model_fields_set:
        raise HTTPException(status_code=422, detail="At least one incident field is required.")
    for field in (
        "status",
        "severity",
        "asset_ids",
        "description",
        "notify_qa",
        "pause_downstream",
        "query_id",
    ):
        if field in body.model_fields_set and getattr(body, field) is None:
            raise HTTPException(status_code=422, detail=f"Incident {field} cannot be null.")
    if body.signature_id is not None and body.status != "resolved":
        raise HTTPException(status_code=422, detail="Signatures are only accepted for resolution.")
    if body.status == "resolved" and (not body.signature_id or not body.comment):
        raise HTTPException(
            status_code=422,
            detail="Resolution requires a signature and a non-empty resolution comment.",
        )
    if if_match is None or not if_match.isdecimal():
        raise HTTPException(status_code=428, detail="A numeric If-Match version is required.")
    return int(if_match)


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
    expected_version = _validate_incident_update(body, if_match)
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
                **body.model_dump(
                    mode="json", include={"status", "owner", "comment", "signature_id"}
                ),
                **body.model_dump(mode="json", exclude_defaults=True),
            },
        )
        if replay:
            return _view_from_json(replay)
        cur.execute(
            """SELECT status,owner,version,severity,asset_ids,description,notify_qa,pause_downstream,asset_id
               FROM phlo.incident WHERE incident_id=%s AND env=%s FOR UPDATE""",
            (incident_id, env),
        )
        old = cur.fetchone()
        if old is None:
            raise HTTPException(status_code=404, detail="Incident not found.")
        if expected_version != old[2]:
            raise HTTPException(status_code=409, detail="Incident version is stale.")
        asset_ids = body.asset_ids if body.asset_ids is not None else (old[4] or [old[8]])
        if old[8] not in asset_ids:
            raise HTTPException(
                status_code=422, detail="Affected assets must include the primary asset."
            )
        if body.asset_ids is not None:
            for asset in asset_ids:
                _authorize(request, "asset.manage", "asset", asset)
        effects = _preflight_effects(
            request,
            env,
            asset_ids=asset_ids,
            notify_qa=body.notify_qa is True,
            pause_downstream=body.pause_downstream is True,
        )
        if body.query_id is not None:
            inserted = _pin_query(cur, request, incident_id, env, body.query_id)
            if not inserted and body.model_fields_set == {"query_id"}:
                cur.execute(
                    f"SELECT {_INCIDENT_COLUMNS} FROM phlo.incident WHERE incident_id=%s",
                    (incident_id,),
                )
                result = _row(cur.fetchone()).model_dump(mode="json")
                _store_idempotent_result(cur, env, actor, action_target, idempotency_key, result)
                return _view_from_json(result)
        if body.status == "resolved":
            signature_id = body.signature_id
            comment = body.comment
            if signature_id is None or comment is None or not comment.strip():
                raise HTTPException(
                    status_code=422,
                    detail="Resolution requires a signature and a non-empty resolution comment.",
                )
            if old[0] != "acknowledged":
                raise HTTPException(
                    status_code=409,
                    detail="Only acknowledged incidents can be resolved.",
                )
            from phlo.identity.authority import IdentityAuthority
            from phlo_api.api import v1_admin_identity

            signature_request = SignatureRequest(
                signer_subject=actor,
                meaning=SignatureMeaning.APPROVED,
                action="incident.resolve",
                record_type="incident",
                record_id=f"{env}:{incident_id}",
                record_version=str(expected_version),
                justification=comment,
            )
            v1_admin_identity._audit(
                CanonicalAuditEvent(
                    event_type=AuditEventType.MUTATION,
                    surface="phlo-api",
                    actor_subject=actor,
                    action="incident.resolve",
                    resource_type="incident",
                    resource_id=f"{env}:{incident_id}",
                    decision="skip",
                    reason_code="signed_resolution_attempt",
                    outcome="attempted",
                    attributes={
                        "signature_id": signature_id,
                        "expected_version": expected_version,
                        "env": env,
                    },
                )
            )
            if not v1_admin_identity._identity_call(
                lambda: IdentityAuthority().consume_signature(signature_id, signature_request)
            ):
                v1_admin_identity._audit(
                    CanonicalAuditEvent(
                        event_type=AuditEventType.AUTHORIZATION,
                        surface="phlo-api",
                        actor_subject=actor,
                        action="incident.resolve",
                        resource_type="incident",
                        resource_id=f"{env}:{incident_id}",
                        decision="deny",
                        reason_code="signature_missing_stale_reused_or_mismatched",
                        outcome="failure",
                        attributes={"signature_id": signature_id},
                    )
                )
                raise HTTPException(
                    status_code=409,
                    detail="Signature is missing, stale, reused, or mismatched.",
                )
        cur.execute(
            """UPDATE phlo.incident SET status=%s,owner=%s,severity=%s,asset_ids=%s,description=%s,
                   notify_qa=%s,pause_downstream=%s,version=version+1,updated_at=now()
               WHERE incident_id=%s AND env=%s""",
            (
                body.status if "status" in body.model_fields_set else old[0],
                body.owner if "owner" in body.model_fields_set else old[1],
                body.severity if body.severity is not None else old[3],
                json.dumps(asset_ids),
                body.description if body.description is not None else old[5],
                body.notify_qa if body.notify_qa is not None else old[6],
                body.pause_downstream if body.pause_downstream is not None else old[7],
                incident_id,
                env,
            ),
        )
        if body.comment is not None:
            _event(
                cur,
                incident_id,
                env,
                actor,
                "resolution_comment" if body.status == "resolved" else "comment",
                {"text": body.comment},
            )
        if body.model_fields_set - {"comment", "query_id", "retry_effects"}:
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
            f"SELECT {_INCIDENT_COLUMNS} FROM phlo.incident WHERE incident_id=%s",
            (incident_id,),
        )
        view = _row(cur.fetchone())
        _enqueue_effects(cur, env, actor, view, effects)
        result = view.model_dump(mode="json")
        _store_idempotent_result(cur, env, actor, action_target, idempotency_key, result)
    return _deliver_and_view(request, env, view)


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


@router.get("/incidents/{incident_id}/follow-ups", response_model=FollowUpPage)
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
