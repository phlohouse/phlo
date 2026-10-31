"""Environment-scoped free-text search over assets and runs.

Search contract: ``q`` is matched as a case-insensitive substring. Assets match
on ID, description, group, owner, source name, and declared relation; runs
match on run ID, job ID, status, and the values of the tags that
``/api/v1/runs`` publishes. Other Dagster tags are not searchable, so search
cannot reveal values the runs resource withholds. Asset search covers the whole
inventory of the selected environment. Run search covers only the newest
``run_scan_limit`` runs of that environment, and ``run_scan_complete`` reports
whether older runs exist that were not searched.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Query, Request

from phlo_api.api import v1_assets as assets
from phlo_api.api import v1_jobs as jobs
from phlo_api.api.v1 import _target
from phlo_api.api.v1_pages import decode_offset, encode_cursor
from phlo_api.errors import BadInputError
from phlo_api.v1_contract import Environment, WireModel

router = APIRouter(tags=["v1 search"])

SearchKind = Literal["asset", "run"]
_RUN_SCAN_LIMIT = 100


class SearchResult(WireModel):
    kind: SearchKind
    id: str
    label: str
    summary: str | None


class SearchPage(WireModel):
    env: Environment
    q: str
    kinds: list[SearchKind]
    items: list[SearchResult]
    run_scan_limit: int
    run_scan_complete: bool | None
    next_cursor: str | None


def _matches(needle: str, *values: object) -> bool:
    return any(isinstance(value, str) and needle in value.lower() for value in values)


@router.get("/search", response_model=SearchPage)
async def v1_search(
    request: Request,
    q: Annotated[str, Query(min_length=1, max_length=200)],
    env: Environment = Query(),
    kind: Annotated[str | None, Query(max_length=32)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    cursor: Annotated[str | None, Query(max_length=4096)] = None,
) -> SearchPage:
    """Search the selected environment; assets precede runs, each in a stable order.

    ``kind`` is a comma-separated subset of ``asset`` and ``run`` (default both).
    The cursor is bound to ``env``, ``q``, and ``kind``.
    """
    allowed = frozenset({"env", "q", "kind", "limit", "cursor"})
    target = _target(request, env, allowed_query=allowed)
    requested = {value.strip() for value in (kind or "asset,run").split(",") if value.strip()}
    if not requested or not requested <= {"asset", "run"}:
        raise BadInputError("kind must be a comma-separated subset of asset and run.")
    kinds: list[SearchKind] = [value for value in ("asset", "run") if value in requested]
    scope = ["search", env, target.dagster_location, target.nessie_ref, q, kinds]
    offset = decode_offset(cursor, scope)
    needle = q.lower()

    results: list[SearchResult] = []
    if "asset" in kinds:
        results.extend(
            SearchResult(kind="asset", id=item.id, label=item.id, summary=item.description)
            for item in await assets._assets(request, env, allowed_query=allowed)
            if _matches(
                needle,
                item.id,
                item.description,
                item.group_name,
                item.owner,
                item.source_name,
                item.relation,
            )
        )
    run_scan_complete: bool | None = None
    if "run" in kinds:
        runs, following = await jobs._run_page(
            target.dagster_location, target.nessie_ref, _RUN_SCAN_LIMIT
        )
        run_scan_complete = following is None
        results.extend(
            SearchResult(
                kind="run", id=run.run_id, label=run.job_id, summary=f"{run.status} {run.job_id}"
            )
            for run in runs
            if _matches(needle, run.run_id, run.job_id, run.status, *run.tags.values())
        )
    end = offset + limit
    return SearchPage(
        env=env,
        q=q,
        kinds=kinds,
        items=results[offset:end],
        run_scan_limit=_RUN_SCAN_LIMIT,
        run_scan_complete=run_scan_complete,
        next_cursor=encode_cursor(scope, end) if end < len(results) else None,
    )
