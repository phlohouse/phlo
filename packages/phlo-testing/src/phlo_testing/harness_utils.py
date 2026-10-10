"""Utilities for Phlo test harnesses.

Re-exports the stdlib-only helpers from ``phlo_testing.harness_core`` (shared
with the repo's golden-path scripts) and adds the pieces that need the phlo
package itself. These helpers deliberately live inside the installed package
so ``phlo-testing`` never loads repo-only scripts such as
``scripts/run_golden_path.py`` (which only resolves inside a repository
checkout, not from a pip-installed package).
"""

from __future__ import annotations

from pathlib import Path

from phlo.config.layout import env_defaults_path, env_secrets_path

from phlo_testing.harness_core import (
    log,
    log_step,
    log_success,
    log_error,
    log_warning,
    log_info,
    force_remove_directory,
    check_port_in_use,
    find_available_port,
    resolve_port,
    run_command,
    run_phlo,
    setup_project_venv,
    wait_for_http,
    wait_for_tcp,
    http_get,
    http_get_basic,
    http_get_bearer,
    http_post,
    extract_openmetadata_token,
    openmetadata_login,
    openmetadata_get_with_fallback,
    read_env_file,
    upsert_env_file,
    write_file,
)

__all__ = [
    "apply_env_updates",
    "check_port_in_use",
    "extract_openmetadata_token",
    "find_available_port",
    "force_remove_directory",
    "http_get",
    "http_get_basic",
    "http_get_bearer",
    "http_post",
    "log",
    "log_error",
    "log_info",
    "log_step",
    "log_success",
    "log_warning",
    "openmetadata_get_with_fallback",
    "openmetadata_login",
    "read_env_file",
    "resolve_port",
    "run_command",
    "run_phlo",
    "setup_project_venv",
    "upsert_env_file",
    "wait_for_http",
    "wait_for_tcp",
    "write_file",
]


def apply_env_updates(phlo_dir: Path, updates: dict[str, str]) -> None:
    """Apply env updates to defaults and secrets in the project's active layout."""
    for env_path in (env_defaults_path(phlo_dir), env_secrets_path(phlo_dir)):
        env_path.parent.mkdir(parents=True, exist_ok=True)
        upsert_env_file(env_path, updates)
