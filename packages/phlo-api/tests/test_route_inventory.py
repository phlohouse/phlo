"""Non-GET route inventory for phlo-api.

Every mounted non-GET route must be explicitly classified: either it carries
a scope/authorization guard in its dispatch path, or it is allow-listed as
public. Adding a mutation route without a guard fails the inventory so the
classification stays a conscious, reviewer-visible decision (issue #981).
"""

from __future__ import annotations

from fastapi import APIRouter, FastAPI

from phlo_api.main import app

_READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

# (method, path) -> required scope (or guard) enforced in the handler's
# dispatch path. ``require_scope`` and the ``check_*`` helpers in
# ``phlo_api.api.authorization`` both count as explicit guards.
_GUARDED_NON_GET_ROUTES: dict[tuple[str, str], str] = {
    ("POST", "/api/authoring/workflows"): "project:write",
    ("POST", "/api/authoring/workflows/validate"): "project:write",
    ("POST", "/api/authoring/schemas/validate"): "project:write",
    ("POST", "/api/authoring/project/lint"): "project:write",
    ("POST", "/api/continuity/plan"): "lakehouse:read",
    ("POST", "/api/continuity/apply"): "lakehouse:operate",
    ("POST", "/api/observatory/packages/install"): "admin",
    ("POST", "/api/observatory/runs/{run_id:path}/retry"): "lakehouse:operate",
    ("POST", "/api/observatory/runs/{run_id:path}/cancel"): "lakehouse:operate",
    ("PUT", "/api/observatory/dataset-workflow/config"): "project:write",
    ("POST", "/api/observatory/assets/{asset_id:path}/materialize"): "lakehouse:operate",
    ("POST", "/api/observatory/assets/{asset_id:path}/backfill"): "lakehouse:operate",
    ("POST", "/api/observatory/branches/actions"): "lakehouse:operate",
    ("POST", "/api/observatory/actions"): "lakehouse:operate",
    ("PUT", "/api/observatory/extensions/{name}/settings"): "admin",
    ("PUT", "/api/observatory/preferences"): "check_admin_manage",
    ("POST", "/api/observatory/workflow-wizard/proposals"): "project:write",
    ("POST", "/api/observatory/workflow-wizard/actions"): "project:write",
}

# Non-GET routes intentionally reachable without a scope guard: read-only
# queries expressed as POST so the request can carry a body, plus local
# per-user convenience state that is not a lakehouse or project mutation.
_PUBLIC_NON_GET_ROUTES: frozenset[tuple[str, str]] = frozenset(
    {
        ("POST", "/api/observatory/query"),
        ("POST", "/api/observatory/schemas/diff"),
        ("POST", "/api/observatory/contributing-rows/query"),
        ("POST", "/api/observatory/contributing-rows/page"),
        ("POST", "/api/observatory/saved-queries"),
    }
)


def _mutation_routes(routes: list) -> set[tuple[str, str]]:
    inventory: set[tuple[str, str]] = set()
    for route in routes:
        methods = set(getattr(route, "methods", None) or ())
        for method in methods - _READ_METHODS:
            inventory.add((method, route.path))
    return inventory


def _unclassified_mutation_routes(routes: list) -> list[tuple[str, str]]:
    declared = set(_GUARDED_NON_GET_ROUTES) | set(_PUBLIC_NON_GET_ROUTES)
    return sorted(_mutation_routes(routes) - declared)


def test_route_inventory_every_mutation_route_is_classified() -> None:
    """Every mounted non-GET route is guarded or explicitly public."""
    unclassified = _unclassified_mutation_routes(list(app.routes))
    assert not unclassified, (
        "non-GET routes without a scope guard or public allowance: "
        f"{unclassified!r}. Guard them with require_scope()/check_* or add a "
        "deliberate entry to _PUBLIC_NON_GET_ROUTES."
    )


def test_route_inventory_declarations_match_mounted_routes() -> None:
    """The guarded and public lists stay in sync with the real route table."""
    actual = _mutation_routes(list(app.routes))
    declared = set(_GUARDED_NON_GET_ROUTES) | set(_PUBLIC_NON_GET_ROUTES)
    assert declared == actual, (
        f"route inventory drift: stale={sorted(declared - actual)!r} "
        f"new={sorted(actual - declared)!r}"
    )
    overlap = set(_GUARDED_NON_GET_ROUTES) & set(_PUBLIC_NON_GET_ROUTES)
    assert not overlap, f"routes classified as both guarded and public: {overlap!r}"


def test_route_inventory_flags_new_unguarded_mutation_route() -> None:
    """A mounted mutation route absent from both lists is reported."""
    probe = FastAPI()
    probe_router = APIRouter()

    @probe_router.post("/api/probe/mutate")
    def _probe_mutation() -> dict[str, bool]:
        return {"ok": True}

    probe.include_router(probe_router)

    assert _unclassified_mutation_routes(list(probe.routes)) == [("POST", "/api/probe/mutate")]
