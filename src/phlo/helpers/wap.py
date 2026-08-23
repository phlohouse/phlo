"""Write-audit-publish helper primitives.

Derives deterministic per-run staging branch names and drives a versioned
catalog capability through the WAP cycle: ensure a branch off the target
ref, then publish (merge) only when all checks pass. Catalog providers
lacking branch or merge support raise PhloConfigError.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from phlo.capabilities import resolve_capability
from phlo.exceptions import PhloConfigError


@dataclass(frozen=True, slots=True)
class StageDiff:
    """Compact branch/table stage diff summary."""

    source_ref: str
    target_ref: str
    tables_changed: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


def branch_for_run(asset_key: str, run_id: str | None, *, prefix: str = "phlo") -> str:
    """Return a deterministic branch name for a workflow run."""
    safe_asset = asset_key.replace("/", "_").replace(".", "_")
    safe_run = (run_id or "manual").replace("/", "_")
    return f"{prefix}/{safe_asset}/{safe_run}"


def resolve_versioned_catalog(name: str | None = None) -> Any:
    """Resolve a versioned catalog provider."""
    resolution = resolve_capability("catalog", name)
    if resolution is None:
        raise PhloConfigError(
            message="No catalog capability could be resolved",
            suggestions=["Install/configure a versioned catalog such as phlo-nessie."],
        )
    return resolution.provider


def ensure_branch(branch: str, *, from_ref: str = "main", catalog: Any = None) -> str | None:
    """Ensure a versioned catalog branch exists."""
    provider = catalog or resolve_versioned_catalog()
    if hasattr(provider, "get_branch_hash") and provider.get_branch_hash(branch):
        return provider.get_branch_hash(branch)
    if not hasattr(provider, "create_branch"):
        raise PhloConfigError(message="Catalog provider does not support branch creation")
    return provider.create_branch(branch, from_ref=from_ref)


def publish_branch(branch: str, *, target_ref: str = "main", catalog: Any = None) -> bool:
    """Merge a staged branch into the target ref."""
    provider = catalog or resolve_versioned_catalog()
    if not hasattr(provider, "merge_branch"):
        raise PhloConfigError(message="Catalog provider does not support branch merges")
    return bool(provider.merge_branch(branch, target=target_ref))


def publish_if_checks_pass(
    branch: str,
    checks: list[bool],
    *,
    target_ref: str = "main",
    catalog: Any = None,
) -> bool:
    """Publish a branch only when all checks pass."""
    if not all(checks):
        return False
    return publish_branch(branch, target_ref=target_ref, catalog=catalog)


@contextmanager
def write_audit_publish(
    *,
    asset_key: str,
    run_id: str | None = None,
    target_ref: str = "main",
    catalog: Any = None,
):
    """Context manager that creates a staging branch and publishes on success."""
    branch = branch_for_run(asset_key, run_id)
    ensure_branch(branch, from_ref=target_ref, catalog=catalog)
    yield branch
    publish_branch(branch, target_ref=target_ref, catalog=catalog)
