"""Discovery and typed parsing of Phlo Transform workspaces.

A workspace is a directory containing ``phlo.toml``; the CLI owns discovery,
SQL compilation, and dependency resolution. This module only locates the
workspace, runs ``list --json``, and exposes the report as typed records so
the asset provider never parses SQL itself.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from phlo.exceptions import PhloCapabilitySetupError
from phlo.logging import get_logger

from phlo_transform.binary import find_binary, require_binary, run_json
from phlo_transform.settings import TransformSettings

logger = get_logger(__name__)

WORKSPACE_MARKER = "phlo.toml"
_SKIPPED_PARTS = {".phlo", ".venv", "dbt_packages", "node_modules", "target"}


@dataclass(frozen=True, slots=True)
class WorkspaceModel:
    """One compiled model from ``phlo-transform list``."""

    id: str
    name: str
    namespace: str
    materialization: str
    target: str
    version: str
    path: str | None
    tags: tuple[str, ...]
    depends_on: tuple[str, ...]
    sources: tuple[str, ...]

    @property
    def leaf_name(self) -> str:
        """Return the final dotted name segment."""

        return self.name.rsplit(".", 1)[-1]

    @property
    def group(self) -> str:
        """Return the workflow or namespace that owns the model."""

        return self.namespace or "transform"


@dataclass(frozen=True, slots=True)
class WorkspaceTest:
    """One workspace test and the models it targets."""

    id: str
    name: str
    targets: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TransformWorkspace:
    """A discovered workspace and its compiled model graph."""

    root: Path
    binary: Path
    models: tuple[WorkspaceModel, ...]
    tests: tuple[WorkspaceTest, ...]
    sources: tuple[str, ...]
    source_assets: dict[str, str] = field(default_factory=dict)

    def model(self, name: str) -> WorkspaceModel | None:
        """Return a model by logical name."""

        return next((model for model in self.models if model.name == name), None)

    def tests_for(self, model_name: str) -> tuple[WorkspaceTest, ...]:
        """Return every test that targets a model."""

        return tuple(test for test in self.tests if model_name in test.targets)


def _is_candidate(path: Path) -> bool:
    return not _SKIPPED_PARTS.intersection(path.parts)


def discover_workspaces(project_root: Path) -> list[Path]:
    """Return candidate workspace roots in deterministic precedence order."""

    candidates: list[Path] = []
    for relative in (Path("transforms"), Path("workflows") / "transforms", Path(".")):
        marker = project_root / relative / WORKSPACE_MARKER
        if marker.is_file():
            candidates.append(marker.parent)
    for pattern in ("workflows/**/" + WORKSPACE_MARKER, "transforms/**/" + WORKSPACE_MARKER):
        for marker in sorted(
            project_root.glob(pattern),
            key=lambda path: (len(path.parts), str(path)),
        ):
            if not _is_candidate(marker):
                continue
            if any(
                marker.parent == candidate or marker.parent.is_relative_to(candidate)
                for candidate in candidates
            ):
                continue
            candidates.append(marker.parent)
    return candidates


def find_workspace(project_root: Path, settings: TransformSettings) -> Path | None:
    """Resolve the configured workspace, or None when the project has none.

    More than one workspace is an error rather than a silent pick: a project
    with two native workspaces needs ``PHLO_TRANSFORM_ROOT`` because model
    names and targets can collide across them.
    """

    if settings.root:
        configured = Path(settings.root).expanduser()
        root = configured if configured.is_absolute() else project_root / configured
        root = root.resolve()
        if not (root / WORKSPACE_MARKER).is_file():
            raise PhloCapabilitySetupError(
                capability="transform",
                required=True,
                message=f"PHLO_TRANSFORM_ROOT does not contain {WORKSPACE_MARKER}: {root}",
                suggestions=["Point PHLO_TRANSFORM_ROOT at a phlo-transform workspace"],
            )
        return root

    candidates = discover_workspaces(project_root)
    if not candidates:
        return None
    if len(candidates) > 1:
        listed = ", ".join(str(path.relative_to(project_root)) for path in candidates)
        raise PhloCapabilitySetupError(
            capability="transform",
            required=True,
            message=f"multiple phlo-transform workspaces found: {listed}",
            suggestions=[
                "Set PHLO_TRANSFORM_ROOT to the workspace that owns the transform graph",
            ],
        )
    return candidates[0]


def load_source_assets(root: Path) -> dict[str, str]:
    """Read the provider-owned source-to-ingestion-asset map from ``phlo.toml``.

    ``[phlo_transform.sources]`` maps a workspace source (``raw.c360_orders``)
    to the Phlo asset that produces it (``sling_c360_orders``). The engine
    ignores the table; it exists so orchestrators keep the ingestion ->
    transform dependency edges when a project switches providers.
    """

    path = root / WORKSPACE_MARKER
    try:
        payload = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        logger.warning("phlo_transform_workspace_config_unreadable", path=str(path))
        return {}
    section = payload.get("phlo_transform")
    if not isinstance(section, dict):
        return {}
    sources = section.get("sources")
    if not isinstance(sources, dict):
        return {}
    return {
        str(source): str(asset)
        for source, asset in sources.items()
        if isinstance(source, str) and isinstance(asset, str)
    }


def _parse_models(payload: dict) -> tuple[WorkspaceModel, ...]:
    models: list[WorkspaceModel] = []
    for raw in payload.get("models") or []:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "").strip()
        if not name:
            continue
        models.append(
            WorkspaceModel(
                id=str(raw.get("id") or f"model://{name}"),
                name=name,
                namespace=str(raw.get("namespace") or name.rsplit(".", 1)[0]),
                materialization=str(raw.get("materialization") or "table"),
                target=str(raw.get("target") or name),
                version=str(raw.get("version") or ""),
                path=str(raw["path"]) if raw.get("path") else None,
                tags=tuple(str(tag) for tag in raw.get("tags") or []),
                depends_on=tuple(str(dep) for dep in raw.get("depends_on") or []),
                sources=tuple(str(source) for source in raw.get("sources") or []),
            )
        )
    return tuple(models)


def _parse_tests(payload: dict) -> tuple[WorkspaceTest, ...]:
    tests: list[WorkspaceTest] = []
    for raw in payload.get("tests") or []:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "").strip()
        if not name:
            continue
        tests.append(
            WorkspaceTest(
                id=str(raw.get("id") or f"test://{name}"),
                name=name,
                targets=tuple(str(target) for target in raw.get("targets") or []),
            )
        )
    return tuple(tests)


def load_workspace(
    project_root: Path,
    settings: TransformSettings,
    *,
    require: bool = True,
) -> TransformWorkspace | None:
    """Return the compiled workspace, or None when discovery finds none.

    ``require`` distinguishes asset discovery (a missing workspace is a normal
    "provider not in use" state) from an explicit CLI command (which should
    explain how to create one).
    """

    root = find_workspace(project_root, settings)
    if root is None:
        return None
    binary = (
        require_binary(project_root, settings) if require else find_binary(project_root, settings)
    )
    if binary is None:
        return None
    payload, invocation = run_json(binary, ["list"], settings=settings, root=root)
    if payload is None or not invocation.ok:
        detail = (invocation.stderr or invocation.stdout).strip().splitlines()
        raise PhloCapabilitySetupError(
            capability="transform",
            required=True,
            message=(
                "phlo-transform could not list the workspace"
                + (f": {detail[-1]}" if detail else "")
            ),
            suggestions=[
                f"Run: phlo-transform --root {root} check",
                "Fix the reported diagnostics and retry",
            ],
        )
    sources = tuple(
        str(raw.get("name"))
        for raw in payload.get("sources") or []
        if isinstance(raw, dict) and raw.get("name")
    )
    workspace = TransformWorkspace(
        root=root,
        binary=binary,
        models=_parse_models(payload),
        tests=_parse_tests(payload),
        sources=sources,
        source_assets=load_source_assets(root),
    )
    logger.info(
        "phlo_transform_workspace_loaded",
        workspace_root=str(root),
        model_count=len(workspace.models),
        test_count=len(workspace.tests),
    )
    return workspace


__all__ = [
    "TransformWorkspace",
    "WORKSPACE_MARKER",
    "WorkspaceModel",
    "WorkspaceTest",
    "discover_workspaces",
    "find_workspace",
    "load_source_assets",
    "load_workspace",
]
