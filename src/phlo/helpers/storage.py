"""Object-store path and layout helpers.

Defines the canonical lakehouse layout (warehouse/stage/tmp/checkpoints prefixes
on one bucket) and deterministic per-run stage paths. ensure_bucket_layout is
best-effort: it reports False rather than raising when no object-store
capability or setup method exists.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from phlo.capabilities import resolve_capability


@dataclass(frozen=True, slots=True)
class ObjectStoreLayout:
    """Canonical lakehouse object-store layout."""

    bucket: str = "lake"
    warehouse_prefix: str = "warehouse"
    stage_prefix: str = "stage"
    tmp_prefix: str = "tmp"
    checkpoints_prefix: str = "checkpoints"
    metadata: dict[str, Any] = field(default_factory=dict)


def object_store_url(bucket: str, *parts: str, scheme: str = "s3") -> str:
    """Build an object-store URL."""
    path = "/".join(part.strip("/") for part in parts if part)
    return f"{scheme}://{bucket}/{path}" if path else f"{scheme}://{bucket}"


def default_object_store_layout(*, bucket: str = "lake") -> ObjectStoreLayout:
    """Return Phlo's default lakehouse object-store layout."""
    return ObjectStoreLayout(bucket=bucket)


def stage_path_for_run(
    asset_key: str,
    *,
    run_id: str | None = None,
    partition_key: str | None = None,
    layout: ObjectStoreLayout | None = None,
) -> str:
    """Return a deterministic stage path for a workflow run."""
    layout = layout or default_object_store_layout()
    safe_asset = asset_key.replace("/", "_").replace(".", "_")
    parts = [layout.stage_prefix, safe_asset]
    if partition_key:
        parts.append(f"partition={partition_key}")
    if run_id:
        parts.append(f"run={run_id.replace('/', '_')}")
    return object_store_url(layout.bucket, *parts)


def ensure_bucket_layout(layout: ObjectStoreLayout | None = None, *, provider: Any = None) -> bool:
    """Ensure the default bucket/prefix layout when a provider exposes setup methods."""
    layout = layout or default_object_store_layout()
    if provider is None:
        resolution = resolve_capability("object_store")
        provider = resolution.provider if resolution else None
    if provider is None:
        return False
    if hasattr(provider, "ensure_layout"):
        provider.ensure_layout(layout)
        return True
    if hasattr(provider, "ensure_bucket"):
        provider.ensure_bucket(layout.bucket)
        return True
    return False


@contextmanager
def temporary_staging_path(
    asset_key: str,
    *,
    run_id: str | None = None,
    root: str | Path = ".phlo/tmp",
):
    """Create a local temporary staging directory for a workflow block."""
    safe_asset = asset_key.replace("/", "_").replace(".", "_")
    path = Path(root) / safe_asset / (run_id or "manual")
    path.mkdir(parents=True, exist_ok=True)
    yield path


def list_partition_files(root: str | Path, *, partition_key: str) -> list[Path]:
    """List local files under a Hive-style partition directory."""
    partition_dir = Path(root) / f"partition={partition_key}"
    if not partition_dir.exists():
        return []
    return sorted(path for path in partition_dir.rglob("*") if path.is_file())
