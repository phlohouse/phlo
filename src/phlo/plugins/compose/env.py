"""Render `.env` and `.env.local` content from discovered service definitions.

Non-secret defaults go to `.env`; secrets and local overrides go to
`.env.local` with freshly generated secret material for new variables.
Values are normalized to strings (None becomes empty, bools become
true/false) before rendering.
"""

import secrets
from importlib.metadata import PackageNotFoundError, version
from typing import Any

from phlo.plugins.discovery import ServiceDefinition


def _default_package_version(package: str) -> str:
    """Return the installed package version for repeatable service image builds."""
    try:
        return version(package)
    except PackageNotFoundError:
        return ""


def normalize_env_value(value: Any) -> str:
    """Serialize a value for `.env` output; None becomes empty, bools lowercase true/false."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def normalize_env_overrides(env_overrides: dict[str, Any]) -> dict[str, str]:
    """Normalize project overrides into a new string-keyed, string-valued mapping."""
    normalized: dict[str, str] = {}
    for key, value in env_overrides.items():
        if not isinstance(key, str):
            continue
        normalized[key] = normalize_env_value(value)
    return normalized


def generate_local_secret(var_name: str | None = None) -> str:
    """Generate local secret material for newly rendered `.env.local` files."""
    if var_name and var_name.upper() in {
        "MINIO_ROOT_PASSWORD",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SECRET_KEY",
        "S3_SECRET_KEY",
        "S3_SECRET_ACCESS_KEY",
        "ICEBERG_S3_SECRET_KEY",
        "DAGSTER_MINIO_SECRET_KEY",
    }:
        return secrets.token_hex(20)
    return f"phlo_{secrets.token_urlsafe(32)}"


def render_env(
    services: list[ServiceDefinition],
    *,
    env_overrides: dict[str, Any] | None,
    include_secrets: bool,
    include_non_secrets: bool,
    existing_values: dict[str, str] | None,
    header_lines: list[str],
) -> str:
    """Render environment file content for selected service variables."""
    lines = list(header_lines)

    overrides = normalize_env_overrides(env_overrides or {})
    existing_values = existing_values or {}

    # Group env vars by category
    categories: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    seen_vars: set[str] = set()

    for service in services:
        category = service.category
        if category not in categories:
            categories[category] = []

        for var_name, var_config in service.env_vars.items():
            if var_name in seen_vars:
                continue
            seen_vars.add(var_name)
            categories[category].append((var_name, var_config))

    # Write grouped env vars
    category_titles = {
        "core": "Core Infrastructure",
        "orchestration": "Orchestration",
        "bi": "Business Intelligence",
        "admin": "Admin Tools",
        "api": "API Layer",
        "observability": "Observability",
    }

    for category, vars_list in categories.items():
        if not vars_list:
            continue

        section_lines: list[str] = []
        title = category_titles.get(category, category.title())
        section_lines.append(f"# {title}")

        for var_name, var_config in vars_list:
            is_secret = bool(var_config.get("secret", False))
            if is_secret and not include_secrets:
                continue
            if not is_secret and not include_non_secrets:
                continue

            default = var_config.get("default", "")
            package_name = var_config.get("package")
            if package_name and not default:
                default = _default_package_version(str(package_name))
            elif var_name == "PHLO_VERSION" and not default:
                default = _default_package_version("phlo")
            description = var_config.get("description", "")
            value = overrides.get(var_name, normalize_env_value(default))

            if is_secret and var_name in existing_values:
                value = existing_values[var_name]
            elif is_secret and include_secrets and var_name not in overrides:
                value = generate_local_secret(var_name)

            if description:
                section_lines.append(f"# {description}")
            section_lines.append(f"{var_name}={value}")

        if len(section_lines) > 1:
            lines.extend(section_lines)
            lines.append("")

    if include_non_secrets:
        extra_overrides = {k: v for k, v in overrides.items() if k not in seen_vars}
        if extra_overrides:
            lines.append("# Project Overrides")
            for key in sorted(extra_overrides):
                lines.append(f"{key}={extra_overrides[key]}")
            lines.append("")

    if include_secrets:
        secret_vars = {
            var_name
            for service in services
            for var_name, var_config in service.env_vars.items()
            if bool(var_config.get("secret", False))
        }
        extra_existing = {
            k: v for k, v in existing_values.items() if k not in seen_vars and k not in secret_vars
        }
        if extra_existing:
            lines.append("# Local Overrides")
            for key in sorted(extra_existing):
                lines.append(f"{key}={extra_existing[key]}")
            lines.append("")

    return "\n".join(lines)


def generate_env(
    services: list[ServiceDefinition],
    env_overrides: dict[str, Any] | None = None,
) -> str:
    """Render non-secret defaults into `.env` content."""
    return render_env(
        services,
        env_overrides=env_overrides,
        include_secrets=False,
        include_non_secrets=True,
        existing_values=None,
        header_lines=[
            "# Phlo Infrastructure Configuration",
            "# Generated by: phlo services init",
            "# Non-secret defaults; override in phlo.yaml (env:) or .phlo/.env.local",
            "",
        ],
    )


def generate_env_local(
    services: list[ServiceDefinition],
    env_overrides: dict[str, Any] | None = None,
    existing_values: dict[str, str] | None = None,
) -> str:
    """Render secrets and local overrides into `.env.local` content, preserving existing values."""
    return render_env(
        services,
        env_overrides=env_overrides,
        include_secrets=True,
        include_non_secrets=False,
        existing_values=existing_values,
        header_lines=[
            "# Phlo Local Secrets",
            "# Generated by: phlo services init",
            "# Safe to edit; values are preserved on regeneration",
            "",
        ],
    )
