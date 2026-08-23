"""Workflow settings loaded from ``phlo.yaml`` and local environment overrides.

Per namespace, settings layer shared ``settings``, then ``settings.<namespace>``,
then ``workflows.<namespace>.settings`` with later layers winning; non-mapping
blocks raise WorkflowSettingsError.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, TypeVar, overload

from pydantic import BaseModel, ValidationError

from phlo.config.env import load_project_env

SchemaT = TypeVar("SchemaT", bound=BaseModel)


class WorkflowSettingsError(ValueError):
    """Raised when workflow settings cannot be validated."""


def _settings_defaults(project_config: dict[str, Any], namespace: str | None) -> dict[str, Any]:
    # Precedence within committed config, later layers winning: shared
    # ``settings``, then ``settings.<namespace>``, then
    # ``workflows.<namespace>.settings``. Non-mapping values in the shared
    # block are kept; namespace blocks replace them by key.
    settings_config = project_config.get("settings", {})
    if settings_config is None:
        settings_config = {}
    if not isinstance(settings_config, dict):
        raise WorkflowSettingsError("phlo.yaml settings must be a mapping")

    if namespace is None:
        return dict(settings_config)

    values = {key: value for key, value in settings_config.items() if not isinstance(value, dict)}

    namespace_defaults = settings_config.get(namespace, {})
    if namespace_defaults is None:
        namespace_defaults = {}
    if not isinstance(namespace_defaults, dict):
        raise WorkflowSettingsError(f"phlo.yaml settings.{namespace} must be a mapping")
    values.update(namespace_defaults)

    workflow_config = project_config.get("workflows", {})
    if workflow_config is None:
        workflow_config = {}
    if not isinstance(workflow_config, dict):
        raise WorkflowSettingsError("phlo.yaml workflows must be a mapping")

    workflow_entry = workflow_config.get(namespace, {})
    if workflow_entry is None:
        workflow_entry = {}
    if not isinstance(workflow_entry, dict):
        raise WorkflowSettingsError(f"phlo.yaml workflows.{namespace} must be a mapping")

    workflow_settings = workflow_entry.get("settings", {})
    if workflow_settings is None:
        workflow_settings = {}
    if not isinstance(workflow_settings, dict):
        raise WorkflowSettingsError(f"phlo.yaml workflows.{namespace}.settings must be a mapping")
    values.update(workflow_settings)
    return values


def _normalized_key(value: str) -> str:
    return value.lower().replace("-", "_")


def _field_names(schema: type[BaseModel] | None, values: dict[str, Any]) -> set[str]:
    if schema is not None:
        return set(schema.model_fields)
    return {_normalized_key(key) for key in values}


def _env_overrides(
    project_root: Path,
    namespace: str | None,
    field_names: set[str],
) -> dict[str, str]:
    env = load_project_env(project_root)
    normalized_env = {_normalized_key(key): value for key, value in env.items()}
    overrides: dict[str, str] = {}

    for field_name in field_names:
        candidates = [field_name, f"phlo_settings__{field_name}"]
        if namespace:
            normalized_namespace = _normalized_key(namespace)
            candidates.extend(
                [
                    f"{normalized_namespace}_{field_name}",
                    f"phlo_settings__{normalized_namespace}__{field_name}",
                ]
            )
        # Candidates are ordered least to most qualified, and later matches
        # overwrite earlier ones: the fully qualified
        # phlo_settings__<namespace>__<field> spelling wins when present.
        for candidate in candidates:
            if candidate in normalized_env:
                overrides[field_name] = normalized_env[candidate]

    return overrides


def _format_validation_error(namespace: str | None, exc: ValidationError) -> WorkflowSettingsError:
    missing = [
        ".".join(str(part) for part in error["loc"])
        for error in exc.errors()
        if error.get("type") == "missing"
    ]
    scope = f" for namespace '{namespace}'" if namespace else ""
    if missing:
        return WorkflowSettingsError(
            f"workflow settings missing required value{scope}: {', '.join(missing)}"
        )
    return WorkflowSettingsError(f"workflow settings invalid{scope}: {exc}")


@overload
def workflow_settings(
    namespace: str | None = None,
    *,
    schema: type[SchemaT],
    project_root: Path | None = None,
) -> SchemaT: ...


@overload
def workflow_settings(
    namespace: str | None = None,
    *,
    schema: None = None,
    project_root: Path | None = None,
) -> SimpleNamespace: ...


def workflow_settings(
    namespace: str | None = None,
    *,
    schema: type[SchemaT] | None = None,
    project_root: Path | None = None,
) -> SchemaT | SimpleNamespace:
    """Return workflow settings from committed defaults plus local overrides.

    Defaults are read from the root ``settings`` block in ``phlo.yaml``. When a
    namespace is supplied, ``settings.<namespace>`` and
    ``workflows.<namespace>.settings`` are overlaid on top of shared settings.
    Local values from ``.phlo/.env``, ``.phlo/.env.local``, and OS environment
    variables override committed defaults.
    """
    # Imported inside the function on purpose: phlo.infrastructure.config
    # imports phlo.config.cache, whose package __init__ imports this module,
    # so an eager import here is circular.
    from phlo.infrastructure.config import load_project_config

    root = project_root or Path.cwd()
    project_config = load_project_config(root)
    values = _settings_defaults(project_config, namespace)
    values.update(_env_overrides(root, namespace, _field_names(schema, values)))

    if schema is None:
        return SimpleNamespace(**values)

    try:
        return schema.model_validate(values)
    except ValidationError as exc:
        raise _format_validation_error(namespace, exc) from exc


settings = workflow_settings
