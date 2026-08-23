"""Generic artifact manifest helpers.

ArtifactManifest collects ArtifactEntry records with optional checksums,
sizes, and metadata. Helpers build manifests from local paths, verify the
checksums that are present (entries without one are reported, not failed),
and render entries as lakehouse-ready table rows.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class ArtifactEntry:
    """Manifest entry for a file or object-store artifact."""

    uri: str
    checksum: str | None = None
    size_bytes: int | None = None
    media_type: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ArtifactManifest:
    """Collection of artifact entries for a workflow output or source extract."""

    name: str
    artifacts: list[ArtifactEntry]
    metadata: dict[str, Any] = field(default_factory=dict)


def file_checksum(path: str | Path, *, algorithm: str = "sha256") -> str:
    """Return a checksum for a local file."""
    digest = hashlib.new(algorithm)
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def manifest_from_paths(
    name: str,
    paths: Iterable[str | Path],
    *,
    checksum: bool = True,
) -> ArtifactManifest:
    """Build an artifact manifest from local paths."""
    entries: list[ArtifactEntry] = []
    for raw_path in paths:
        path = Path(raw_path)
        entries.append(
            ArtifactEntry(
                uri=str(path),
                checksum=file_checksum(path) if checksum and path.is_file() else None,
                size_bytes=path.stat().st_size if path.exists() and path.is_file() else None,
            )
        )
    return ArtifactManifest(name=name, artifacts=entries)


def verify_manifest_checksums(manifest: ArtifactManifest) -> dict[str, bool]:
    """Verify local artifact checksums when checksums are present."""
    results: dict[str, bool] = {}
    for artifact in manifest.artifacts:
        if artifact.checksum is None:
            continue
        path = Path(artifact.uri)
        results[artifact.uri] = path.exists() and file_checksum(path) == artifact.checksum
    return results


def manifest_summary(manifest: ArtifactManifest) -> dict[str, Any]:
    """Return a compact serializable manifest summary."""
    return {
        "name": manifest.name,
        "artifact_count": len(manifest.artifacts),
        "total_size_bytes": sum(artifact.size_bytes or 0 for artifact in manifest.artifacts),
        "checksummed_count": sum(1 for artifact in manifest.artifacts if artifact.checksum),
    }


def artifact_manifest_to_table_rows(
    manifest: ArtifactManifest,
    *,
    include_manifest_metadata: bool = True,
) -> list[dict[str, Any]]:
    """Render manifest entries as lakehouse-ready row dictionaries."""
    rows: list[dict[str, Any]] = []
    for index, artifact in enumerate(manifest.artifacts):
        row: dict[str, Any] = {
            "manifest_name": manifest.name,
            "artifact_index": index,
            "uri": artifact.uri,
            "checksum": artifact.checksum,
            "size_bytes": artifact.size_bytes,
            "media_type": artifact.media_type,
        }
        row.update({f"artifact_{key}": value for key, value in artifact.metadata.items()})
        if include_manifest_metadata:
            row.update({f"manifest_{key}": value for key, value in manifest.metadata.items()})
        rows.append(row)
    return rows
