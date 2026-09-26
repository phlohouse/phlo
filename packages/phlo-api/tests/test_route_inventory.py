"""Non-GET route inventory for phlo-api.

Every mounted non-GET route must be explicitly classified: either it carries
a scope/authorization guard in its dispatch path, or it is allow-listed as
public. Adding a mutation route without a guard fails the inventory so the
classification stays a conscious, reviewer-visible decision (issue #981).
"""

from __future__ import annotations

import importlib
import inspect
import re

import pytest
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
    ("POST", "/api/v1/incidents"): "v1_manifest",
    ("PATCH", "/api/v1/incidents/{incident_id}"): "v1_manifest",
    ("PUT", "/api/v1/incidents/{incident_id}/subscriptions"): "v1_manifest",
    ("POST", "/api/v1/incidents/{incident_id}/follow-ups"): "v1_manifest",
    ("PATCH", "/api/v1/incidents/{incident_id}/follow-ups/{follow_up_id}"): "v1_manifest",
    ("PUT", "/api/v1/assets/{asset_id:path}/incident-policy"): "v1_manifest",
    ("POST", "/api/v1/assets/{asset_id:path}/materialize"): "v1_manifest",
    ("POST", "/api/v1/assets/{asset_id:path}/backfill"): "v1_manifest",
    ("POST", "/api/v1/assets/{asset_id:path}/audits"): "v1_manifest",
    (
        "POST",
        "/api/v1/assets/{asset_id:path}/audits/{proposal_id}/pull-request",
    ): "v1_manifest",
    ("POST", "/api/observatory/saved-queries"): "project:write",
    ("POST", "/api/observatory/workflow-wizard/proposals"): "project:write",
    ("POST", "/api/observatory/workflow-wizard/actions"): "project:write",
}

# Non-GET routes intentionally reachable without a scope guard: read-only
# queries expressed as POST so the request can carry a body.
_PUBLIC_NON_GET_ROUTES: frozenset[tuple[str, str]] = frozenset(
    {
        ("POST", "/api/observatory/query"),
        ("POST", "/api/observatory/schemas/diff"),
        ("POST", "/api/observatory/contributing-rows/query"),
        ("POST", "/api/observatory/contributing-rows/page"),
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


_GUARD_CALL = re.compile(r"\b(require_scope|check_[a-z_]+)\s*\(")


def _endpoint_dispatch_source(path: str, method: str) -> str:
    """Handler source plus one level of phlo_api callees (delegated guards)."""
    for route in app.routes:
        if getattr(route, "path", None) == path and method in set(route.methods or ()):
            endpoint = route.endpoint
            break
    else:
        raise AssertionError(f"{method} {path} is not mounted")

    source = inspect.getsource(endpoint)
    module_globals = getattr(endpoint, "__globals__", {})
    called = set(re.findall(r"\b([a-zA-Z_][a-zA-Z0-9_]*)\s*\(", source))
    candidates: list[object] = [
        module_globals.get(name) for name in called if name in module_globals
    ]
    # Handlers that defer their guard to a callee may import it inside the
    # function body rather than at module level.
    for module_name, names in re.findall(r"from\s+([\w.]+)\s+import\s+\(?\n?\s*([\w\s,]+)", source):
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            continue
        candidates.extend(
            getattr(module, name.strip(), None) for name in names.split(",") if name.strip()
        )
    parts = [source]
    for candidate in candidates:
        if inspect.isfunction(candidate):
            try:
                parts.append(inspect.getsource(candidate))
            except (OSError, TypeError):
                pass
    return "\n".join(parts)


@pytest.mark.parametrize(
    ("method", "path", "guard"),
    [(method, path, guard) for (method, path), guard in sorted(_GUARDED_NON_GET_ROUTES.items())],
)
def test_guarded_route_handlers_contain_a_guard(method: str, path: str, guard: str) -> None:
    """Every declared guarded route really calls a guard in its dispatch path."""
    if guard == "v1_manifest":
        from phlo_api.security_manifest import HTTP_ROUTE_KEY_MANIFEST

        assert (method, path) in HTTP_ROUTE_KEY_MANIFEST
        return
    source = _endpoint_dispatch_source(path, method)
    if guard.startswith("check_"):
        assert f"{guard}(" in source or f"{guard} (" in source, (
            f"{method} {path} is declared guarded by {guard} but the call is "
            "absent from the handler's dispatch path"
        )
    else:
        assert _GUARD_CALL.search(source), (
            f"{method} {path} is declared guarded but no require_scope()/check_* "
            "call exists in the handler's dispatch path"
        )
