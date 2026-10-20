"""``phlo transform`` CLI commands.

The group is a thin, testable wrapper around the engine's own commands: it
resolves the workspace and binary, forwards selection and adapter flags, and
prints the engine's report. Semantic answers always come from the binary, so
the CLI cannot drift from the engine's compiler or planner.
"""

from __future__ import annotations

import json
from pathlib import Path

import click

from phlo.cli.authorization_wrappers import enforce_surface_mutation_authorization
from phlo.cli.output import user_error
from phlo.config.env import resolve_project_root
from phlo.config.transformation import (
    KNOWN_PROVIDERS,
    active_transformation_provider,
    normalise_provider,
    set_active_transformation_provider,
)
from phlo.plugins.base import cli_command_plugin_class
from phlo_transform.assets import adapter_args
from phlo_transform.authorization import get_transform_adapter
from phlo_transform.binary import (
    binary_version,
    find_binary,
    install_binary,
    run_cli,
)
from phlo_transform.settings import TransformSettings, get_settings
from phlo_transform.translate import translate_dbt_project
from phlo_transform.workspace import TransformWorkspace, load_workspace


def _project_root() -> Path:
    return resolve_project_root()


def _settings(root: Path | None = None) -> TransformSettings:
    return get_settings(root or _project_root())


def _require_workspace(settings: TransformSettings) -> TransformWorkspace:
    project_root = _project_root()
    workspace = load_workspace(project_root, settings)
    if workspace is None:
        raise user_error(
            "no phlo-transform workspace found",
            missing=project_root / "workflows" / "transforms",
            details=[
                "Create a workspace with: phlo-transform init",
                "Or set PHLO_TRANSFORM_ROOT to an existing workspace",
            ],
            run="phlo-transform init",
        )
    return workspace


def _forward(
    workspace: TransformWorkspace,
    command: str,
    args: list[str],
    *,
    settings: TransformSettings,
    as_json: bool,
    include_adapter: bool = False,
    adapter_override: str | None = None,
) -> None:
    if include_adapter:
        effective = (
            settings.model_copy(update={"adapter": adapter_override})
            if adapter_override
            else settings
        )
        args = [*args, *adapter_args(None, effective, infer=False)]
    invocation = run_cli(
        workspace.binary,
        [command, *args],
        settings=settings,
        root=workspace.root,
        extra_args=["--json"] if as_json else None,
    )
    if invocation.stdout:
        click.echo(invocation.stdout.rstrip())
    if invocation.stderr:
        click.echo(invocation.stderr.rstrip(), err=True)
    if not invocation.ok:
        raise SystemExit(invocation.returncode)


@click.group("transform")
def transform_group() -> None:
    """Phlo Transform workspace commands (check, plan, run, test, lineage)."""


@transform_group.command("install")
@click.option("--version", default=None, help="Release version to download (default: settings).")
@click.option("--force", is_flag=True, help="Replace an already installed binary.")
def install_cmd(version: str | None, force: bool) -> None:
    """Download the pinned portable engine binary into .phlo/bin."""
    enforce_surface_mutation_authorization("transform.install", get_transform_adapter)
    settings = _settings()
    binary = install_binary(_project_root(), settings, version=version, force=force)
    reported = binary_version(binary, settings)
    click.echo(f"Installed {binary}")
    if reported:
        click.echo(reported)


@transform_group.command("check")
@click.option("--json", "as_json", is_flag=True, help="Emit the engine's JSON report.")
def check_cmd(as_json: bool) -> None:
    """Compile the workspace and print diagnostics."""
    settings = _settings()
    _forward(_require_workspace(settings), "check", [], settings=settings, as_json=as_json)


@transform_group.command("plan")
@click.option("--select", "select", multiple=True, help="Model selector (repeatable).")
@click.option("--exclude", "exclude", multiple=True, help="Exclude selector (repeatable).")
@click.option("--upstream", is_flag=True, help="Include transitive dependencies.")
@click.option("--downstream", is_flag=True, help="Include transitive dependents.")
@click.option("--force", is_flag=True, help="Plan a rebuild regardless of state.")
@click.option("--ref", default=None, help="Logical environment / Nessie reference.")
@click.option("--adapter", default=None, help="Execution adapter (trino or duckdb).")
@click.option("--json", "as_json", is_flag=True, help="Emit the engine's JSON report.")
def plan_cmd(
    select: tuple[str, ...],
    exclude: tuple[str, ...],
    upstream: bool,
    downstream: bool,
    force: bool,
    ref: str | None,
    adapter: str | None,
    as_json: bool,
) -> None:
    """Explain what would change and why."""
    settings = _settings()
    args = _selection_args(select, exclude, upstream, downstream, force, ref)
    _forward(
        _require_workspace(settings),
        "plan",
        args,
        settings=settings,
        as_json=as_json,
        include_adapter=True,
        adapter_override=adapter,
    )


@transform_group.command("run")
@click.option("--select", "select", multiple=True, help="Model selector (repeatable).")
@click.option("--exclude", "exclude", multiple=True, help="Exclude selector (repeatable).")
@click.option("--upstream", is_flag=True, help="Include transitive dependencies.")
@click.option("--downstream", is_flag=True, help="Include transitive dependents.")
@click.option("--force", is_flag=True, help="Rebuild selected models regardless of state.")
@click.option("--ref", default=None, help="Logical environment / Nessie reference.")
@click.option("--adapter", default=None, help="Execution adapter (trino or duckdb).")
@click.option("--json", "as_json", is_flag=True, help="Emit the engine's JSON report.")
def run_cmd(
    select: tuple[str, ...],
    exclude: tuple[str, ...],
    upstream: bool,
    downstream: bool,
    force: bool,
    ref: str | None,
    adapter: str | None,
    as_json: bool,
) -> None:
    """Plan, apply, and test the selected models."""
    enforce_surface_mutation_authorization("transform.run", get_transform_adapter)
    settings = _settings()
    args = _selection_args(select, exclude, upstream, downstream, force, ref)
    _forward(
        _require_workspace(settings),
        "run",
        args,
        settings=settings,
        as_json=as_json,
        include_adapter=True,
        adapter_override=adapter,
    )


@transform_group.command("test")
@click.option("--select", "select", multiple=True, help="Model selector (repeatable).")
@click.option("--ref", default=None, help="Logical environment / Nessie reference.")
@click.option("--adapter", default=None, help="Execution adapter (trino or duckdb).")
@click.option("--json", "as_json", is_flag=True, help="Emit the engine's JSON report.")
def test_cmd(select: tuple[str, ...], ref: str | None, adapter: str | None, as_json: bool) -> None:
    """Run workspace tests against the selected models."""
    settings = _settings()
    args = _selection_args(select, (), False, False, False, ref)
    _forward(
        _require_workspace(settings),
        "test",
        args,
        settings=settings,
        as_json=as_json,
        include_adapter=True,
        adapter_override=adapter,
    )


@transform_group.command("list")
@click.option("--json", "as_json", is_flag=True, help="Emit the engine's JSON report.")
def list_cmd(as_json: bool) -> None:
    """List compiled models, dependencies, and tests."""
    settings = _settings()
    _forward(_require_workspace(settings), "list", [], settings=settings, as_json=as_json)


@transform_group.command("explain")
@click.argument("model")
@click.option("--json", "as_json", is_flag=True, help="Emit the engine's JSON report.")
def explain_cmd(model: str, as_json: bool) -> None:
    """Explain one model: SQL, columns, lineage, and state."""
    settings = _settings()
    _forward(_require_workspace(settings), "explain", [model], settings=settings, as_json=as_json)


@transform_group.command("lineage")
@click.option(
    "--format",
    "lineage_format",
    type=click.Choice(["graph", "openlineage"]),
    default="graph",
    show_default=True,
    help="Export format.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit the engine's JSON report.")
def lineage_cmd(lineage_format: str, as_json: bool) -> None:
    """Export the workspace lineage graph."""
    settings = _settings()
    args = ["--format", lineage_format]
    _forward(_require_workspace(settings), "lineage", args, settings=settings, as_json=as_json)


@transform_group.command("doctor")
@click.option("--json", "as_json", is_flag=True, help="Emit the engine's JSON report.")
def doctor_cmd(as_json: bool) -> None:
    """Diagnose workspace, adapter, and state health."""
    settings = _settings()
    _forward(
        _require_workspace(settings),
        "doctor",
        [],
        settings=settings,
        as_json=as_json,
        include_adapter=True,
    )


@transform_group.command("translate")
@click.option("--from", "source", default="dbt", show_default=True, help="Source format.")
@click.option("--source-root", type=click.Path(file_okay=False, path_type=Path), default=None)
@click.option("--out", type=click.Path(file_okay=False, path_type=Path), default=None)
@click.option("--check", "check_only", is_flag=True, help="Analyse without writing.")
@click.option("--overwrite", is_flag=True, help="Overwrite existing files.")
@click.option("--verify", is_flag=True, help="Compile-check the generated workspace.")
@click.option("--json", "as_json", is_flag=True, help="Emit the engine's JSON report.")
def translate_cmd(
    source: str,
    source_root: Path | None,
    out: Path | None,
    check_only: bool,
    overwrite: bool,
    verify: bool,
    as_json: bool,
) -> None:
    """Translate a dbt project into a native transform workspace."""
    if source != "dbt":
        raise user_error(
            f"unsupported translation source '{source}'",
            details=["Only --from dbt is supported"],
        )
    if not check_only and out is None:
        raise user_error("translate needs --out or --check", run="phlo transform translate --help")
    if not check_only:
        enforce_surface_mutation_authorization("transform.translate", get_transform_adapter)
    settings = _settings()
    invocation = translate_dbt_project(
        _project_root(),
        settings,
        dbt_project=source_root,
        out=out,
        check_only=check_only,
        overwrite=overwrite,
        verify=verify,
    )
    if as_json:
        click.echo(invocation.stdout.rstrip())
        if invocation.stderr:
            click.echo(invocation.stderr.rstrip(), err=True)
        if not invocation.ok:
            raise SystemExit(invocation.returncode)
        return
    click.echo(invocation.stdout.rstrip())
    if invocation.stderr:
        click.echo(invocation.stderr.rstrip(), err=True)
    if not invocation.ok:
        raise SystemExit(invocation.returncode)


@transform_group.command("use")
@click.argument("provider", required=False)
@click.option("--unset", is_flag=True, help="Clear the project choice.")
def use_cmd(provider: str | None, unset: bool) -> None:
    """Select the transformation provider that owns the asset graph."""
    enforce_surface_mutation_authorization("transform.use", get_transform_adapter)
    if unset == bool(provider):
        raise user_error("choose one provider or pass --unset", run="phlo transform use dbt")
    if unset:
        set_active_transformation_provider(None)
        click.echo("Transformation provider follows install defaults.")
        return

    from phlo.plugins.discovery import discover_plugins, get_global_registry

    discover_plugins(plugin_type="transformation_provider", auto_register=True)
    installed = set(get_global_registry().list("transformation_provider"))
    canonical = normalise_provider(str(provider))
    if canonical not in installed:
        raise user_error(
            f"transformation provider '{provider}' is not installed",
            details=[
                f"Installed providers: {', '.join(sorted(installed)) or 'none'}",
                f"Known names: {', '.join(KNOWN_PROVIDERS)}",
            ],
        )
    path = set_active_transformation_provider(canonical, _project_root())
    click.echo(f"Active transformation provider: {canonical}")
    click.echo(f"Wrote {path}")
    click.echo(
        "Restart Dagster (phlo services restart dagster) for the orchestrated graph to reload."
    )


@transform_group.command("status")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable status.")
def status_cmd(as_json: bool) -> None:
    """Show the active provider, workspace, binary, and compiled counts."""
    from phlo_transform.assets import provider_is_active

    project_root = _project_root()
    settings = _settings(project_root)
    binary = find_binary(project_root, settings)
    workspace = load_workspace(project_root, settings, require=False)
    payload = {
        "active_provider": active_transformation_provider(project_root),
        "provider_active": bool(workspace) and provider_is_active(project_root),
        "project_root": str(project_root),
        "workspace": str(workspace.root) if workspace else None,
        "binary": str(binary) if binary else None,
        "binary_version": binary_version(binary, settings) if binary else None,
        "adapter": settings.adapter,
        "asset_key_mode": settings.asset_key_mode,
        "models": len(workspace.models) if workspace else 0,
        "tests": len(workspace.tests) if workspace else 0,
    }
    if as_json:
        click.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    for label, value in payload.items():
        click.echo(f"{label}: {value if value is not None else '-'}")


def _selection_args(
    select: tuple[str, ...],
    exclude: tuple[str, ...],
    upstream: bool,
    downstream: bool,
    force: bool,
    ref: str | None,
) -> list[str]:
    args: list[str] = []
    for term in select:
        args.extend(["--select", term])
    for term in exclude:
        args.extend(["--exclude", term])
    if upstream:
        args.append("--upstream")
    if downstream:
        args.append("--downstream")
    if force:
        args.append("--force")
    if ref:
        args.extend(["--ref", ref])
    return args


TransformCliPlugin = cli_command_plugin_class(
    "TransformCliPlugin",
    name="transform",
    version="0.1.0",
    description="Phlo Transform workspace CLI commands",
    commands=[transform_group],
)
