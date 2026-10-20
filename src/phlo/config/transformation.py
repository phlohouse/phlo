"""Active transformation provider selection.

A project can carry more than one transformation representation at once: a
dbt project and a native Phlo Transform workspace, for example. Exactly one
of them may own the transform asset graph, so the active provider is a
project decision rather than an install-order accident.

Resolution order:

1. ``PHLO_TRANSFORMATION_PROVIDER`` from the process or project env layers.
2. ``.phlo/transformation-provider`` written by ``phlo transform use``.
3. Unset, which leaves the decision to the installed providers.

Aliases are normalised onto provider plugin names: ``phlo-transform`` and
``transform`` both select the ``transform`` provider, and ``dbt`` selects
``dbt``.
"""

from __future__ import annotations

from pathlib import Path

from phlo.config.env import load_project_env, resolve_project_root

PROVIDER_MARKER = Path(".phlo") / "transformation-provider"
PROVIDER_ALIASES = {
    "dbt": "dbt",
    "phlo-transform": "transform",
    "transform": "transform",
}
KNOWN_PROVIDERS = ("dbt", "transform")


def normalise_provider(name: str) -> str:
    """Return the canonical provider name for a user-facing alias.

    Unknown names are returned stripped rather than rejected: core does not
    know which providers are installed, and a plugin-aware caller such as the
    ``phlo transform`` CLI validates against the live registry.
    """

    return PROVIDER_ALIASES.get(name.strip().lower(), name.strip().lower())


def _marker_value(project_root: Path) -> str | None:
    path = project_root / PROVIDER_MARKER
    if not path.exists():
        return None
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            value = line.strip()
            if value and not value.startswith("#"):
                return value
    except OSError:
        return None
    return None


def active_transformation_provider(project_root: Path | str | None = None) -> str | None:
    """Return the configured transformation provider, or None when unset.

    The process environment wins over the project marker so a one-off
    ``PHLO_TRANSFORMATION_PROVIDER=dbt phlo ...`` overrides the checked-in
    project choice without editing files.
    """

    root = resolve_project_root(project_root)
    configured = load_project_env(root).get("PHLO_TRANSFORMATION_PROVIDER")
    if configured:
        return normalise_provider(configured)
    marker = _marker_value(root)
    return normalise_provider(marker) if marker else None


def set_active_transformation_provider(
    name: str | None, project_root: Path | str | None = None
) -> Path | None:
    """Persist the active provider for a project and return the marker path.

    Passing None removes the marker, restoring provider-default resolution.
    """

    root = resolve_project_root(project_root)
    path = root / PROVIDER_MARKER
    if name is None:
        path.unlink(missing_ok=True)
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{normalise_provider(name)}\n", encoding="utf-8")
    return path


__all__ = [
    "KNOWN_PROVIDERS",
    "PROVIDER_ALIASES",
    "PROVIDER_MARKER",
    "active_transformation_provider",
    "normalise_provider",
    "set_active_transformation_provider",
]
