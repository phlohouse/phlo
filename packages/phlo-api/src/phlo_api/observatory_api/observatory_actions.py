"""Guarded Observatory action family dispatcher.

Resolves action ids against declared action families, each carrying the
capability its execution needs. Never raises: unknown ids return a failed
result and known families without an executable provider path return
skipped, so callers render outcomes from the result alone.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
from typing import Any, Literal

from phlo.capabilities.resolver import resolve_capability
from phlo.capabilities.runtime import RuntimeRouting

from phlo_api.observatory_api.observatory_metadata import safe_metadata
from phlo_api.observatory_api.observatory_models import (
    ObservatoryAction,
    ObservatoryActionRequest,
    ObservatoryActionResult,
    ObservatoryHealth,
    ObservatoryOperation,
    ObservatoryResourceRef,
)


@dataclass(frozen=True)
class _ActionFamily:
    prefix: str
    kind: str
    label: str
    reason: str
    risk_level: Literal["low", "medium", "high", "critical"] = "low"
    required_capability: str | None = None


@dataclass
class _ActionRuntimeContext:
    run_id: str | None = None
    partition_key: str | None = None
    tags: dict[str, str] = field(default_factory=dict)
    _resources: dict[str, Any] = field(default_factory=dict)

    @property
    def logger(self) -> Any:
        """Return the module logger for action handling."""
        return logging.getLogger("phlo.observatory.observatory.actions")

    @property
    def resources(self) -> dict[str, Any]:
        """Return the registered resources by name."""
        return self._resources

    @property
    def routing(self) -> RuntimeRouting:
        """Build runtime routing from the recorded run, partition, and resources."""
        return RuntimeRouting(
            partition_key=self.partition_key,
            run_id=self.run_id,
            resources=self._resources,
        )

    def get_resource(self, name: str) -> Any:
        """Return a named resource."""
        return self._resources[name]


_ACTION_FAMILIES = (
    _ActionFamily(
        prefix="quality:",
        kind="quality.rerun",
        label="Re-run quality check",
        reason="Quality re-runs need a provider-backed execution contract.",
        required_capability="quality_backend",
    ),
    _ActionFamily(
        prefix="branch:",
        kind="branch.workflow",
        label="Run branch workflow",
        reason="Branch workflows need a catalog provider write contract.",
        risk_level="medium",
        required_capability="catalog",
    ),
    _ActionFamily(
        prefix="storage:",
        kind="storage.maintenance",
        label="Run storage maintenance",
        reason="No executable table storage provider is registered for this maintenance action.",
        risk_level="medium",
        required_capability="table_store",
    ),
    _ActionFamily(
        prefix="metadata:",
        kind="metadata.sync",
        label="Sync metadata",
        reason="No executable metadata provider is registered for this sync action.",
        required_capability="metadata_catalog",
    ),
    _ActionFamily(
        prefix="alert:",
        kind="alert.workflow",
        label="Run alert workflow",
        reason="Alert workflows need an alerting provider write contract.",
        required_capability="alert_sink",
    ),
    _ActionFamily(
        prefix="api:",
        kind="api.publish",
        label="Publish API",
        reason="API publishing needs an API gateway provider write contract.",
        risk_level="medium",
        required_capability="api_backend",
    ),
    _ActionFamily(
        prefix="dataset:",
        kind="dataset.workflow",
        label="Run Dataset workflow",
        reason="Dataset workflow actions need a catalog write contract.",
        risk_level="medium",
        required_capability="metadata_catalog",
    ),
    _ActionFamily(
        prefix="candidate:",
        kind="dataset.candidate",
        label="Run candidate workflow",
        reason="Candidate promotion needs a catalog write contract.",
        risk_level="medium",
        required_capability="metadata_catalog",
    ),
)


def _known_family_for_action(action_id: str) -> _ActionFamily | None:
    for family in _ACTION_FAMILIES:
        if not action_id.startswith(family.prefix):
            continue
        if family.kind == "quality.rerun" and not action_id.endswith(":rerun"):
            continue
        return family
    return None


def _skipped_action_result(
    request: ObservatoryActionRequest, family: _ActionFamily
) -> ObservatoryActionResult:
    action = ObservatoryAction(
        id=request.action_id,
        label=family.label,
        kind=family.kind,
        enabled=False,
        requires_confirmation=True,
        reason=family.reason,
        risk_level=family.risk_level,
        required_capability=family.required_capability,
    )
    return ObservatoryActionResult(
        action=action,
        status="skipped",
        message=family.reason,
    )


def _action_for_family(
    request: ObservatoryActionRequest,
    family: _ActionFamily,
    *,
    enabled: bool,
    reason: str | None = None,
) -> ObservatoryAction:
    return ObservatoryAction(
        id=request.action_id,
        label=family.label,
        kind=family.kind,
        enabled=enabled,
        requires_confirmation=True,
        reason=reason,
        risk_level=family.risk_level,
        required_capability=family.required_capability,
    )


def _operation(
    request: ObservatoryActionRequest,
    action: ObservatoryAction,
    *,
    status: Literal["succeeded", "failed", "skipped"],
    message: str,
    target: ObservatoryResourceRef | None = None,
    metadata: dict[str, Any] | None = None,
) -> ObservatoryOperation:
    state: Literal["ok", "warning", "error"] = "ok"
    if status == "skipped":
        state = "warning"
    elif status == "failed":
        state = "error"
    return ObservatoryOperation(
        id=request.action_id,
        name=action.label,
        kind=action.kind,
        status=status,
        health=ObservatoryHealth(state=state, message=message),
        target=target,
        metadata=safe_metadata(metadata or {}),
    )


def _check_for_action(action_id: str, registry: Any) -> Any | None:
    check_action_id = action_id.removeprefix("quality:")
    try:
        checks = registry.list("check")
    except Exception:
        return None
    for check in checks:
        if f"{check.asset_key}:{check.name}:rerun" == check_action_id:
            return check
    return None


def _execute_quality_action(
    request: ObservatoryActionRequest,
    family: _ActionFamily,
    registry: Any | None,
) -> ObservatoryActionResult | None:
    if registry is None:
        return None

    check = _check_for_action(request.action_id, registry)
    if check is None:
        message = "Quality check is not registered in the capability registry."
        action = _action_for_family(request, family, enabled=False, reason=message)
        return ObservatoryActionResult(
            action=action,
            status="skipped",
            message=message,
            operation=_operation(request, action, status="skipped", message=message),
        )

    if not callable(getattr(check, "fn", None)):
        message = "Quality check has no executable function."
        action = _action_for_family(request, family, enabled=False, reason=message)
        return ObservatoryActionResult(
            action=action,
            status="skipped",
            message=message,
            operation=_operation(
                request,
                action,
                status="skipped",
                message=message,
                target=ObservatoryResourceRef(
                    kind="quality",
                    id=f"{check.asset_key}:{check.name}",
                    label=check.name,
                ),
            ),
        )

    action = _action_for_family(request, family, enabled=True)
    context = _ActionRuntimeContext(tags={"phlo/action_id": request.action_id})
    try:
        result = check.fn(context)
    except Exception as exc:
        message = f"Quality check execution failed: {exc}"
        return ObservatoryActionResult(
            action=action,
            status="failed",
            message=message,
            operation=_operation(
                request,
                action,
                status="failed",
                message=message,
                target=ObservatoryResourceRef(
                    kind="quality",
                    id=f"{check.asset_key}:{check.name}",
                    label=check.name,
                ),
            ),
        )

    passed = bool(getattr(result, "passed", False))
    status: Literal["succeeded", "failed"] = "succeeded" if passed else "failed"
    message = (
        f"Quality check {check.name} passed." if passed else f"Quality check {check.name} failed."
    )
    metadata = {
        "asset_key": getattr(result, "asset_key", check.asset_key),
        "check_name": getattr(result, "check_name", check.name),
        "severity": getattr(result, "severity", getattr(check, "severity", None)),
        "metadata": getattr(result, "metadata", {}),
    }
    return ObservatoryActionResult(
        action=action,
        status=status,
        message=message,
        operation=_operation(
            request,
            action,
            status=status,
            message=message,
            target=ObservatoryResourceRef(
                kind="quality",
                id=f"{check.asset_key}:{check.name}",
                label=check.name,
            ),
            metadata=metadata,
        ),
    )


def _execute_alert_action(
    request: ObservatoryActionRequest,
    family: _ActionFamily,
    registry: Any | None,
) -> ObservatoryActionResult | None:
    if registry is None:
        return None

    resolution = resolve_capability("alert_sink", registry=registry)
    sink = resolution.provider if resolution is not None else None
    send_alert = getattr(sink, "send_alert", None)
    if not callable(send_alert):
        return None

    action = _action_for_family(request, family, enabled=True)
    try:
        sent = bool(
            send_alert(
                title="Observatory action requested",
                message=f"Observatory executed action {request.action_id}.",
                severity="warning",
            )
        )
    except Exception as exc:
        message = f"Alert workflow failed: {exc}"
        return ObservatoryActionResult(
            action=action,
            status="failed",
            message=message,
            operation=_operation(request, action, status="failed", message=message),
        )

    status: Literal["succeeded", "failed"] = "succeeded" if sent else "failed"
    message = "Alert workflow sent." if sent else "Alert sink declined the alert."
    return ObservatoryActionResult(
        action=action,
        status=status,
        message=message,
        operation=_operation(
            request,
            action,
            status=status,
            message=message,
            metadata={"provider": resolution.name if resolution is not None else None},
        ),
    )


def _provider_action_result(
    request: ObservatoryActionRequest,
    family: _ActionFamily,
    registry: Any | None,
) -> ObservatoryActionResult | None:
    if family.kind == "quality.rerun":
        return _execute_quality_action(request, family, registry)
    if family.kind == "alert.workflow":
        return _execute_alert_action(request, family, registry)
    return None


def _failed_action_result(request: ObservatoryActionRequest) -> ObservatoryActionResult:
    message = f"Unsupported Observatory action: {request.action_id}"
    action = ObservatoryAction(
        id=request.action_id,
        label="Unsupported action",
        kind="unsupported",
        enabled=False,
        requires_confirmation=True,
        reason=message,
    )
    return ObservatoryActionResult(
        action=action,
        status="failed",
        message=message,
        operation=ObservatoryOperation(
            id=request.action_id,
            name=action.label,
            kind=action.kind,
            status="failed",
            health=ObservatoryHealth(state="error", message=message),
        ),
    )


def execute_observatory_action(
    request: ObservatoryActionRequest, *, registry: Any | None = None
) -> ObservatoryActionResult:
    """Execute or decline a guarded Observatory action."""
    # Never raises: unknown action ids return a failed result, known families
    # without an executable provider path return skipped, so callers can always
    # render the outcome from the returned result alone.
    family = _known_family_for_action(request.action_id)
    if family is not None:
        result = _provider_action_result(request, family, registry)
        if result is not None:
            return result
        return _skipped_action_result(request, family)
    return _failed_action_result(request)
