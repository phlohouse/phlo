"""dbt-to-native translation helpers.

The engine owns translation semantics; this module only locates the project's
dbt project and forwards the documented ``translate --from dbt`` flags. Both
the CLI and example maintenance scripts use the same helper so a regenerated
workspace is produced by exactly the command the docs describe.
"""

from __future__ import annotations

from pathlib import Path

from phlo.exceptions import PhloCapabilitySetupError
from phlo_transform.binary import Invocation, require_binary, run_cli
from phlo_transform.settings import TransformSettings


def find_dbt_project(project_root: Path) -> Path | None:
    """Return the shallowest dbt project below ``workflows/``, if any."""

    markers = [
        marker
        for marker in project_root.glob("workflows/**/dbt_project.yml")
        if "dbt_packages" not in marker.parts
    ]
    if not markers:
        return None
    return min(markers, key=lambda path: (len(path.parts), str(path))).parent


def translate_dbt_project(
    project_root: Path,
    settings: TransformSettings,
    *,
    dbt_project: Path | None = None,
    out: Path | None = None,
    check_only: bool = False,
    overwrite: bool = False,
    verify: bool = False,
) -> Invocation:
    """Run ``translate --from dbt`` against the project's dbt project."""

    source = dbt_project or find_dbt_project(project_root)
    if source is not None and not source.is_absolute():
        source = (project_root / source).resolve()
    if source is None:
        raise PhloCapabilitySetupError(
            capability="transform",
            required=True,
            message="no dbt project found to translate",
            suggestions=[
                "Create workflows/transforms/dbt/dbt_project.yml",
                "Pass --source-root explicitly",
            ],
        )
    if not check_only and out is None:
        raise ValueError("translate needs an output directory or check_only=True")
    binary = require_binary(project_root, settings)
    args = ["translate", "--from", "dbt"]
    if check_only:
        args.append("--check")
    if out is not None:
        destination = out if out.is_absolute() else project_root / out
        args.extend(["--out", str(destination)])
        if overwrite:
            args.append("--overwrite")
        if verify:
            args.append("--verify")
    return run_cli(binary, args, settings=settings, root=source)


__all__ = ["find_dbt_project", "translate_dbt_project"]
