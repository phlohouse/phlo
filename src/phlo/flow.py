"""Terse flow authoring decorators for provider-neutral flow declarations.

publish(), observe(), backfill(), contract(), access(), and schedule() append
specs to module-level registries; get_* accessors drain them and clear_*
resets them. Declaration order at import time is the only ordering guarantee.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from phlo._flow_authoring import (
    AssetDependency,
    append_asset,
    asset_key,
    build_run,
    contract_metadata,
    normalize_asset_deps,
)
from phlo.capabilities import AssetCheckSpec, AssetSpec
from phlo.contracts import SLA, Consumer, normalize_consumers, serialize_consumers, serialize_sla


@dataclass(frozen=True, slots=True)
class ContractSpec:
    """Provider-neutral governance contract declaration for a table."""

    key: str
    table: str
    owner: str | None
    consumers: list[dict[str, Any]]
    sla: dict[str, Any] | None
    pii: bool
    lifecycle: str | None
    metadata: dict[str, Any]
    fn: Callable[..., Any]


@dataclass(frozen=True, slots=True)
class AccessPolicySpec:
    """Provider-neutral access policy declaration for a table."""

    key: str
    table: str
    roles: list[str]
    pii_columns: list[str]
    policy: str
    metadata: dict[str, Any]
    fn: Callable[..., Any]


@dataclass(frozen=True, slots=True)
class ScheduleSpec:
    """Provider-neutral schedule declaration for launching static targets."""

    key: str
    name: str
    cron: str
    targets: list[str]
    timezone: str
    metadata: dict[str, Any]
    fn: Callable[..., Any]


_PUBLISH_ASSETS: list[AssetSpec] = []
_OBSERVE_ASSETS: list[AssetSpec] = []
_BACKFILL_ASSETS: list[AssetSpec] = []
_CONTRACT_SPECS: list[ContractSpec] = []
_ACCESS_POLICIES: list[AccessPolicySpec] = []
_SCHEDULES: list[ScheduleSpec] = []


def publish(
    *,
    table: str,
    audience: list[str] | None = None,
    owner: str | None = None,
    freshness_hours: int | None = None,
    depends_on: list[AssetDependency] | None = None,
    group: str = "publish",
    consumers: list[Consumer | str] | None = None,
    sla: SLA | None = None,
    description: str | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Mark a curated table as a publishable Dataset surface."""

    def _decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        append_asset(
            _PUBLISH_ASSETS,
            AssetSpec(
                key=asset_key("publish", table),
                group=group,
                description=description or fn.__doc__,
                kinds={"publish", "dataset"},
                tags={"provider": "core", "asset_type": "publish"},
                metadata={
                    "table": table,
                    "audience": list(audience or []),
                    "freshness_hours": freshness_hours,
                    **contract_metadata(owner=owner, consumers=consumers, sla=sla),
                },
                deps=normalize_asset_deps(depends_on),
                run=build_run(fn),
            ),
        )
        return fn

    return _decorator


def observe(
    *,
    table: str,
    freshness_hours: int | None = None,
    row_count_change: dict[str, float] | None = None,
    depends_on: list[AssetDependency] | None = None,
    group: str = "observe",
    description: str | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Register operational health checks for a table or asset."""

    def _decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        key = asset_key("observe", table)
        checks: list[AssetCheckSpec] = []
        # These checks carry fn=None: they are declarative specs (thresholds in
        # metadata/description) that the runtime adapter evaluates, not callables
        # executed here. Both are advisory -- blocking=False, warning severity.
        if freshness_hours is not None:
            checks.append(
                AssetCheckSpec(
                    name="freshness_hours",
                    asset_key=key,
                    fn=None,
                    blocking=False,
                    description=f"Warn when {table} is older than {freshness_hours} hours.",
                    severity="warning",
                    tags={"check_type": "freshness"},
                )
            )
        if row_count_change is not None:
            checks.append(
                AssetCheckSpec(
                    name="row_count_change",
                    asset_key=key,
                    fn=None,
                    blocking=False,
                    description=f"Watch row-count movement for {table}.",
                    severity="warning",
                    tags={"check_type": "volume"},
                )
            )

        append_asset(
            _OBSERVE_ASSETS,
            AssetSpec(
                key=key,
                group=group,
                description=description or fn.__doc__,
                kinds={"observe", "operational_check"},
                tags={"provider": "core", "asset_type": "observe"},
                metadata={
                    "table": table,
                    "freshness_hours": freshness_hours,
                    "row_count_change": dict(row_count_change or {}),
                },
                deps=normalize_asset_deps(depends_on),
                run=build_run(fn),
                checks=checks,
            ),
        )
        return fn

    return _decorator


def backfill(
    *,
    target: str,
    partitions: dict[str, Any],
    mode: str = "replace-partitions",
    depends_on: list[AssetDependency] | None = None,
    group: str = "backfill",
    owner: str | None = None,
    description: str | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Register a repeatable backfill job."""

    def _decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        append_asset(
            _BACKFILL_ASSETS,
            AssetSpec(
                key=asset_key("backfill", target),
                group=group,
                description=description or fn.__doc__,
                kinds={"backfill"},
                tags={"provider": "core", "asset_type": "backfill", "mode": mode},
                metadata={
                    "target": target,
                    "partitions": dict(partitions),
                    "mode": mode,
                    "owner": owner,
                },
                deps=normalize_asset_deps(depends_on),
                run=build_run(fn),
            ),
        )
        return fn

    return _decorator


def contract(
    *,
    table: str,
    owner: str | None = None,
    consumers: list[Consumer | str] | None = None,
    pii: bool = False,
    freshness_hours: int | None = None,
    lifecycle: str | None = None,
    sla: SLA | None = None,
    metadata: dict[str, Any] | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Declare governance ownership, consumer, SLA, and lifecycle metadata."""

    def _decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        effective_sla = sla or (
            SLA(freshness_hours=freshness_hours) if freshness_hours is not None else None
        )
        _CONTRACT_SPECS.append(
            ContractSpec(
                key=asset_key("contract", table),
                table=table,
                owner=owner,
                consumers=serialize_consumers(normalize_consumers(consumers)),
                sla=serialize_sla(effective_sla),
                pii=pii,
                lifecycle=lifecycle,
                metadata=dict(metadata or {}),
                fn=fn,
            )
        )
        return fn

    return _decorator


def access(
    *,
    table: str,
    roles: list[str],
    pii_columns: list[str] | None = None,
    policy: str = "read",
    metadata: dict[str, Any] | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Declare intended access policy metadata for a table."""

    def _decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        _ACCESS_POLICIES.append(
            AccessPolicySpec(
                key=asset_key("access", table),
                table=table,
                roles=list(roles),
                pii_columns=list(pii_columns or []),
                policy=policy,
                metadata=dict(metadata or {}),
                fn=fn,
            )
        )
        return fn

    return _decorator


def schedule(
    *,
    name: str,
    cron: str,
    targets: list[str],
    timezone: str = "UTC",
    metadata: dict[str, Any] | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Declare when static targets should run.

    The decorated function is stored as a dynamic parameter hook. Adapters can
    call it at run time for partition values, config, or tags.
    """

    def _decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        _SCHEDULES.append(
            ScheduleSpec(
                key=asset_key("schedule", name),
                name=name,
                cron=cron,
                targets=list(targets),
                timezone=timezone,
                metadata=dict(metadata or {}),
                fn=fn,
            )
        )
        return fn

    return _decorator


def get_publish_assets() -> list[AssetSpec]:
    """Return registered publish asset specs."""
    return list(_PUBLISH_ASSETS)


def clear_publish_assets() -> None:
    """Clear registered publish asset specs."""
    _PUBLISH_ASSETS.clear()


def get_observe_assets() -> list[AssetSpec]:
    """Return registered observe asset specs."""
    return list(_OBSERVE_ASSETS)


def clear_observe_assets() -> None:
    """Clear registered observe asset specs."""
    _OBSERVE_ASSETS.clear()


def get_backfill_assets() -> list[AssetSpec]:
    """Return registered backfill asset specs."""
    return list(_BACKFILL_ASSETS)


def clear_backfill_assets() -> None:
    """Clear registered backfill asset specs."""
    _BACKFILL_ASSETS.clear()


def get_contract_specs() -> list[ContractSpec]:
    """Return registered governance contract specs."""
    return list(_CONTRACT_SPECS)


def clear_contract_specs() -> None:
    """Clear registered governance contract specs."""
    _CONTRACT_SPECS.clear()


def get_access_policies() -> list[AccessPolicySpec]:
    """Return registered access policy specs."""
    return list(_ACCESS_POLICIES)


def clear_access_policies() -> None:
    """Clear registered access policy specs."""
    _ACCESS_POLICIES.clear()


def get_schedules() -> list[ScheduleSpec]:
    """Return registered schedule specs."""
    return list(_SCHEDULES)


def clear_schedules() -> None:
    """Clear registered schedule specs."""
    _SCHEDULES.clear()


def clear_flow_declarations() -> None:
    """Clear all flow authoring declarations registered in this process."""
    clear_publish_assets()
    clear_observe_assets()
    clear_backfill_assets()
    clear_contract_specs()
    clear_access_policies()
    clear_schedules()


__all__ = [
    "AccessPolicySpec",
    "ContractSpec",
    "ScheduleSpec",
    "access",
    "backfill",
    "clear_access_policies",
    "clear_backfill_assets",
    "clear_contract_specs",
    "clear_flow_declarations",
    "clear_observe_assets",
    "clear_publish_assets",
    "clear_schedules",
    "contract",
    "get_access_policies",
    "get_backfill_assets",
    "get_contract_specs",
    "get_observe_assets",
    "get_publish_assets",
    "get_schedules",
    "observe",
    "publish",
    "schedule",
]
