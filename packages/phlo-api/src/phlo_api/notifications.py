"""Actor-owned read receipts over bounded, authorized incident activity."""

from __future__ import annotations

import asyncio
from datetime import datetime
from hashlib import sha256
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import Field

from phlo_api import incidents
from phlo_api.security_manifest import OperationSpec, enforce_http_operation
from phlo_api.v1_contract import Environment, WireModel

router = APIRouter(tags=["v1 notifications"])
NotificationView = Literal["for_you", "watching", "all"]


class NotificationItem(WireModel):
    id: str
    incident_id: str
    title: str
    asset_id: str
    actor: str
    kind: str
    occurred_at: datetime
    read: bool


class NotificationPage(WireModel):
    env: Environment
    view: NotificationView
    items: list[NotificationItem]
    unread: int
    truncated: bool
    next_cursor: str | None


class NotificationReadInput(WireModel):
    event_ids: list[str] = Field(min_length=1, max_length=100)


class NotificationReadResult(WireModel):
    event_ids: list[str]


async def _authorize_incident(request: Request, env: Environment, incident_id: str) -> None:
    spec = OperationSpec(
        operation_name="incident_detail",
        surface="http",
        action="run.read",
        resource_type="run",
        resource_keys=("env", "incident_id"),
        resource_sources=(("env", "query"), ("incident_id", "path")),
    )
    await enforce_http_operation(request, spec, {"incident_id": incident_id})


def _visible(request: Request, env: Environment, incident_id: str) -> bool:
    try:
        asyncio.run(_authorize_incident(request, env, incident_id))
    except HTTPException as exc:
        if exc.status_code in {403, 404}:
            return False
        raise
    return True


def _notification_candidates(
    env: Environment,
    subject: str,
    view: NotificationView,
    limit: int,
    after: tuple[datetime, str] | None = None,
    unread_only: bool = False,
):
    filters = {"for_you": "AND i.owner=%s", "watching": "AND s.subject IS NOT NULL", "all": ""}
    params: tuple = (subject, subject, env)
    if view == "for_you":
        params += (subject,)
    continuation = ""
    if after is not None:
        continuation = "AND (e.occurred_at,e.event_id) < (%s,%s)"
        params += after
    params += (limit + 1,)
    with incidents._transaction() as connection, connection.cursor() as cur:
        cur.execute(
            f"""SELECT e.event_id,e.incident_id,i.title,i.asset_id,e.actor,e.kind,e.occurred_at,
                       (r.event_id IS NOT NULL)
                FROM phlo.incident_event e
                JOIN phlo.incident i ON i.incident_id=e.incident_id AND i.env=e.env
                LEFT JOIN phlo.incident_notification_read r ON r.env=e.env
                    AND r.event_id=e.event_id AND r.subject=%s
                LEFT JOIN phlo.incident_subscription s ON s.env=e.env
                    AND s.incident_id=e.incident_id AND s.subject=%s
                WHERE e.env=%s AND e.occurred_at >= now()-interval '30 days'
                    {filters[view]}
                    {"AND r.event_id IS NULL" if unread_only else ""}
                    {continuation}
                ORDER BY e.occurred_at DESC,e.event_id DESC LIMIT %s""",  # noqa: S608 # Reason: Static view fragments only; identity, scope and limit remain bound.
            params,
        )
        return cur.fetchall()


@router.get("/notifications", response_model=NotificationPage)
def v1_notifications(
    request: Request,
    env: Environment = Query(),
    view: NotificationView = "for_you",
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
    after: str | None = None,
    unread_only: bool = False,
) -> NotificationPage:
    """Return page-scoped unread counts and a continuation even across denied events."""
    subject = incidents._actor(request)
    scope = f"notifications:{sha256(subject.encode()).hexdigest()}:{view}:{unread_only}"
    position = incidents._decode_cursor(after, env, scope)
    rows = _notification_candidates(env, subject, view, 200, position, unread_only)
    allowed: dict[str, bool] = {}
    items = []
    scanned = 0
    for row in rows[:200]:
        scanned += 1
        incident_id = row[1]
        if incident_id not in allowed:
            allowed[incident_id] = _visible(request, env, incident_id)
        if allowed[incident_id]:
            items.append(
                NotificationItem(
                    id=row[0],
                    incident_id=incident_id,
                    title=row[2],
                    asset_id=row[3],
                    actor=row[4],
                    kind=row[5],
                    occurred_at=row[6],
                    read=row[7],
                )
            )
        if len(items) == limit:
            break
    more = len(rows) > scanned
    return NotificationPage(
        env=env,
        view=view,
        items=items,
        unread=sum(not item.read for item in items),
        truncated=more,
        next_cursor=(
            incidents._encode_cursor(env, scope, rows[scanned - 1][6], rows[scanned - 1][0])
            if more
            else None
        ),
    )


def _require_read_intent(request: Request) -> None:
    """Reject browser form submissions and cross-site personal-state writes."""
    if request.headers.get("x-phlo-intent") != "notifications.mark-read":
        raise HTTPException(status_code=403, detail="Explicit notification read intent required.")
    if (
        request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        != "application/json"
    ):
        raise HTTPException(status_code=415, detail="Notification read state requires JSON.")
    origin = request.headers.get("origin")
    if request.headers.get("sec-fetch-site") == "cross-site" or (
        origin is not None and origin.rstrip("/") != str(request.base_url).rstrip("/")
    ):
        raise HTTPException(
            status_code=403, detail="Cross-origin read state updates are forbidden."
        )


@router.put("/notifications/read-state", response_model=NotificationReadResult)
def v1_notifications_read(
    request: Request, body: NotificationReadInput, env: Environment = Query()
) -> NotificationReadResult:
    _require_read_intent(request)
    subject = incidents._actor(request)
    ids = list(dict.fromkeys(body.event_ids))
    if any(not value.strip() or len(value) > 128 for value in ids):
        raise HTTPException(
            status_code=422, detail="Event identities must contain 1 to 128 characters."
        )
    with incidents._transaction() as connection, connection.cursor() as cur:
        cur.execute(
            "SELECT e.event_id,e.incident_id FROM phlo.incident_event e JOIN phlo.incident i ON i.incident_id=e.incident_id AND i.env=e.env WHERE e.env=%s AND e.event_id=ANY(%s)",
            (env, ids),
        )
        rows = cur.fetchall()
        if len(rows) != len(ids) or any(not _visible(request, env, row[1]) for row in rows):
            raise HTTPException(status_code=404, detail="Notification not found.")
        cur.executemany(
            "INSERT INTO phlo.incident_notification_read(env,subject,event_id) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
            [(env, subject, identity) for identity in ids],
        )
    return NotificationReadResult(event_ids=ids)


@router.get(
    "/incidents/{incident_id}/subscriptions", response_model=incidents.IncidentSubscriptionView
)
def v1_incident_subscription(
    request: Request, incident_id: str, env: Environment = Query()
) -> incidents.IncidentSubscriptionView:
    subject = incidents._actor(request)
    with incidents._transaction() as connection, connection.cursor() as cur:
        cur.execute(
            "SELECT EXISTS(SELECT 1 FROM phlo.incident_subscription WHERE env=%s AND incident_id=%s AND subject=%s) FROM phlo.incident WHERE env=%s AND incident_id=%s",
            (env, incident_id, subject, env, incident_id),
        )
        row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Incident not found.")
    return incidents.IncidentSubscriptionView(incident_id=incident_id, subscribed=row[0])
