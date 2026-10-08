"""Execute file export assets and publish complete sets on a local filesystem.

Each attempt stages independently. Complete run directories are committed
before atomic current-pointer replacement, and published runs are never
overwritten by Phlo. Consumers resolve one manifest for the whole set.
"""

from __future__ import annotations

import errno
import hashlib
import json
import shutil
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from tempfile import TemporaryDirectory

from phlo.capabilities.registry import register_capability
from phlo.capabilities.runtime import RuntimeContext, routing_from_context
from phlo.capabilities.specs import AssetSpec, MaterializeResult, RunSpec
from phlo.helpers.artifacts import (
    ArtifactEntry,
    ArtifactManifest,
    manifest_from_paths,
    verify_manifest_checksums,
)


@dataclass(frozen=True, slots=True)
class ExportContext:
    """One writer invocation and its selected upstream versions.

    Write every declared output beneath ``staging_dir`` and close files before
    returning. Record available version/snapshot references in
    ``upstream_versions`` when selecting the data, not by looking up a later
    latest version. ``runtime`` exposes resources, routing, and logging.
    """

    staging_dir: Path
    run_id: str
    runtime: RuntimeContext
    upstream_versions: dict[str, str] = field(default_factory=dict)


def _export_root(destination: str | Path, name: str) -> Path:
    if not name or name in {".", ".."} or "/" in name or "\\" in name:
        raise ValueError("Export name must be a non-empty single path component")
    if "://" in str(destination):
        raise ValueError("Exports support local filesystem destinations only")
    return Path(destination).resolve() / name


def _read_manifest(path: Path) -> ArtifactManifest:
    data = json.loads(path.read_text(encoding="utf-8"))
    return ArtifactManifest(
        name=data["name"],
        artifacts=[ArtifactEntry(**entry) for entry in data["artifacts"]],
        metadata=data["metadata"],
    )


def resolve_export_manifest(destination: str | Path, name: str) -> ArtifactManifest:
    """Resolve the current pointer once and read its immutable manifest.

    Reuse the returned manifest for every artifact read. Resolving separately
    per artifact could mix runs if a concurrent publication changes current.
    Raises FileNotFoundError when no complete set has been published.
    """
    root = _export_root(destination, name)
    pointer = json.loads((root / "current.json").read_text(encoding="utf-8"))
    return _read_manifest(root / pointer["manifest"])


def _publish(
    root: Path,
    context: ExportContext,
    outputs: Mapping[str, Path],
    candidate: Path,
) -> tuple[ArtifactManifest, Path]:
    """Copy declared files, commit an immutable run, then replace current."""
    candidate.mkdir()
    for relative_path in outputs.values():
        source = context.staging_dir / relative_path
        if not source.is_file():
            raise FileNotFoundError(f"Missing export output: {relative_path}")
        if any(part.is_symlink() for part in (source, *source.parents)):
            raise ValueError(f"Export output must not use symlinks: {relative_path}")
        target = candidate / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)

    # Hash the opaque identity rather than interpreting it as a filesystem path.
    run_dir = root / "runs" / hashlib.sha256(context.run_id.encode()).hexdigest()
    manifest_path = run_dir / "manifest.json"
    staging_manifest = manifest_from_paths(
        root.name, [candidate / path for path in outputs.values()]
    )
    routing = routing_from_context(context.runtime)
    manifest = ArtifactManifest(
        name=root.name,
        artifacts=[
            replace(entry, uri=str(run_dir / path), metadata={"name": name})
            for (name, path), entry in zip(outputs.items(), staging_manifest.artifacts, strict=True)
        ],
        metadata={
            "run_id": context.run_id,
            "partition_key": routing.partition_key,
            "ref": routing.ref,
            "upstream_versions": dict(context.upstream_versions),
        },
    )
    (candidate / "manifest.json").write_text(
        json.dumps(asdict(manifest), sort_keys=True), encoding="utf-8"
    )
    run_dir.parent.mkdir(parents=True, exist_ok=True)
    try:
        candidate.rename(run_dir)
    except OSError as exc:
        # Another attempt may have committed this identity concurrently. A
        # complete run directory is non-empty, so rename cannot replace it.
        if exc.errno not in {errno.EEXIST, errno.ENOTEMPTY}:
            raise
        existing = _read_manifest(manifest_path)
        if existing != manifest or not all(verify_manifest_checksums(existing).values()):
            raise ValueError(
                f"Export run {context.run_id!r} already has different content"
            ) from exc

    pointer = candidate.parent / "current.json"
    pointer.write_text(
        json.dumps({"manifest": str(manifest_path.relative_to(root))}), encoding="utf-8"
    )
    pointer.replace(root / "current.json")
    return manifest, manifest_path


def export(
    *,
    name: str,
    destination: str | Path,
    outputs: Mapping[str, str],
    depends_on: Sequence[str] = (),
    group: str = "exports",
    resources: Iterable[str] = (),
    max_retries: int = 0,
    retry_delay_seconds: int = 30,
) -> Callable[[Callable[[ExportContext], None]], Callable[[ExportContext], None]]:
    """Register one executable asset for a complete named file export set.

    ``destination/name`` owns immutable run directories and ``current.json``.
    Retries use canonical runtime run identity, fresh staging, and reject
    changes to an already published run. Concurrent publications of distinct
    runs are last-publication-wins. Old runs are never automatically deleted.
    """
    root = _export_root(destination, name)
    dependencies = tuple(depends_on)
    paths = {key: Path(value) for key, value in outputs.items()}
    if not paths or any(not key for key in paths):
        raise ValueError("Exports require at least one named output")
    for path in paths.values():
        if path.is_absolute() or ".." in path.parts or path == Path():
            raise ValueError(f"Export output must be a relative file path: {path}")
        if path.parts[0] == "manifest.json":
            raise ValueError("manifest.json is reserved for the export manifest")
    if len(set(paths.values())) != len(paths):
        raise ValueError("Export outputs must have distinct paths")

    def decorate(writer: Callable[[ExportContext], None]) -> Callable[[ExportContext], None]:
        def run(runtime: RuntimeContext) -> Iterable[MaterializeResult]:
            routing = routing_from_context(runtime)
            if not routing.run_id:
                raise ValueError("Exports require a stable runtime run_id")
            root.mkdir(parents=True, exist_ok=True)
            with TemporaryDirectory(prefix=".attempt-", dir=root) as attempt:
                staging = Path(attempt) / "staging"
                staging.mkdir()
                context = ExportContext(staging, routing.run_id, runtime)
                writer(context)
                unknown = context.upstream_versions.keys() - set(dependencies)
                if unknown:
                    raise ValueError(f"Undeclared export upstream versions: {sorted(unknown)}")
                manifest, manifest_path = _publish(root, context, paths, Path(attempt) / "set")
            yield MaterializeResult(
                metadata={
                    "phlo/export_manifest": asdict(manifest),
                    "phlo/export_manifest_path": str(manifest_path),
                    "phlo/export_current_path": str(root / "current.json"),
                    "phlo/export_run_id": routing.run_id,
                }
            )

        register_capability(
            "asset",
            AssetSpec(
                key=name,
                group=group,
                description=writer.__doc__,
                kinds={"file"},
                deps=list(dependencies),
                resources=set(resources),
                metadata={
                    "phlo/export_outputs": {key: str(path) for key, path in paths.items()},
                    "phlo/export_destination": str(root),
                },
                run=RunSpec(
                    fn=run, max_retries=max_retries, retry_delay_seconds=retry_delay_seconds
                ),
            ),
        )
        return writer

    return decorate
