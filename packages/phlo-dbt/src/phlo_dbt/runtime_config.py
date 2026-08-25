"""Canonical dbt runtime configuration derived from Phlo settings.

This module manages dbt runtime configuration, profile generation, and target
resolution. It bridges Phlo's settings system with dbt's profile format,
enabling seamless integration between the two platforms.

Example:
    >>> from phlo_dbt.runtime_config import DbtRuntimeConfig, write_dbt_profile
    >>> config = DbtRuntimeConfig(
    ...     target_name="prod",
    ...     catalog="analytics",
    ...     schema="marts"
    ... )
    >>> profile_path = write_dbt_profile(config, Path("/app/profiles"))
    >>> print(f"Profile written to: {profile_path}")

"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from phlo.capabilities import (
    CapabilitySupport,
    RefQueryCatalogManager,
    RuntimeContext,
    resolve_capability,
    resolve_runtime_ref,
    routing_from_context,
)
from phlo.logging import get_logger
from phlo_dbt.settings import get_settings as get_dbt_settings

logger = get_logger(__name__)

DEFAULT_DBT_TARGET = "dev"
DBT_QUERY_ENGINE_SUPPORT = CapabilitySupport(supports_refs=True)
DEFAULT_DBT_PROFILE_NAME = "phlo"
OWNED_WAP_REF_PREFIX = "pipeline-run-"


@dataclass(frozen=True, slots=True)
class DbtRuntimeConfig:
    """Canonical dbt runtime configuration for the active execution target.

    Holds everything needed to generate a dbt profile: connection details for
    the query engine (typically Trino), authentication settings, and execution
    parameters. Field defaults encode the bundled stack layout.

    Example:
        >>> config = DbtRuntimeConfig(
        ...     target_name="prod",
        ...     catalog="analytics",
        ...     schema="marts",
        ...     threads=4
        ... )
        >>> payload = config.to_profile_payload()
        >>> print(yaml.dump(payload))
    """

    profile_name: str = DEFAULT_DBT_PROFILE_NAME
    target_name: str = DEFAULT_DBT_TARGET
    engine_type: str = "trino"
    user: str = "dagster"
    host: str = "trino"
    port: int = 8080
    catalog: str = "iceberg"
    schema: str = "raw"
    threads: int = 2
    http_scheme: str = "http"
    method: str = "none"
    password: str = ""

    def to_profile_payload(self) -> dict[str, Any]:
        """Return the config in dbt `profiles.yml` shape.

        Example:
            >>> config = DbtRuntimeConfig(target_name="prod")
            >>> payload = config.to_profile_payload()
            >>> "phlo" in payload
            True
        """
        output = (
            self._clickhouse_output() if self.engine_type == "clickhouse" else self._trino_output()
        )
        return {
            self.profile_name: {
                "target": self.target_name,
                "outputs": {self.target_name: output},
            }
        }

    def _trino_output(self) -> dict[str, Any]:
        """Trino connection block (default engine)."""
        return {
            "type": self.engine_type,
            "method": self.method,
            "user": self.user,
            "host": self.host,
            "port": self.port,
            "catalog": self.catalog,
            "schema": self.schema,
            "http_scheme": self.http_scheme,
            "threads": self.threads,
        }

    def _clickhouse_output(self) -> dict[str, Any]:
        """ClickHouse connection block (dbt-clickhouse credentials shape)."""
        return {
            "type": self.engine_type,
            "user": self.user,
            "password": self.password,
            "host": self.host,
            "port": self.port,
            "schema": self.schema,
            "threads": self.threads,
        }


def resolve_dbt_target_name(
    runtime: RuntimeContext | None = None, *, target: str | None = None
) -> str:
    """Resolve the effective dbt target name from canonical routing.

    Resolution order:
    1. Explicit target argument
    2. Canonical routing environment
    3. Legacy `dbt_target` tag
    4. Default `DEFAULT_DBT_TARGET`

    Example:
        >>> target = resolve_dbt_target_name(target="prod")
        >>> print(target)
        prod
        >>>
        >>> # With runtime context containing environment
        >>> target = resolve_dbt_target_name(runtime=ctx)
        >>> # Returns environment name from routing context
    """
    if target:
        return target
    if runtime is not None:
        routing = routing_from_context(runtime)
        if routing.environment:
            return routing.environment
        runtime_tags = getattr(runtime, "tags", {}) or {}
        legacy_target = runtime_tags.get("dbt_target") if isinstance(runtime_tags, dict) else None
        if isinstance(legacy_target, str) and legacy_target:
            return legacy_target
    return DEFAULT_DBT_TARGET


def resolve_dbt_runtime_config(
    runtime: RuntimeContext | None = None,
    *,
    target: str | None = None,
) -> DbtRuntimeConfig:
    """Resolve canonical dbt runtime config from query-engine settings and routing.

    Combines Phlo settings with runtime context to produce a complete dbt
    runtime configuration. Handles catalog name resolution based on runtime
    references.

    Example:
        >>> config = resolve_dbt_runtime_config(target="prod")
        >>> print(f"Catalog: {config.catalog}, Target: {config.target_name}")
        >>> # With runtime that has a branch reference
        >>> config = resolve_dbt_runtime_config(runtime=ctx)
        >>> # Catalog will include branch suffix if not "main"
    """
    settings = get_dbt_settings()
    target_name = resolve_dbt_target_name(runtime, target=target)
    catalog = settings.dbt_query_catalog
    ref = resolve_runtime_ref(runtime, support=DBT_QUERY_ENGINE_SUPPORT, default_ref="main")
    # Non-main refs run against an isolated catalog. Refs owned by a
    # pipeline run (OWNED_WAP_REF_PREFIX) get their catalog provisioned by
    # the query engine when it implements RefQueryCatalogManager; any other
    # ref falls back to the plain "<catalog>_<ref>" name.
    if ref and ref != "main":
        catalog = f"{catalog}_{ref}"
        if ref.startswith(OWNED_WAP_REF_PREFIX):
            query_engine = resolve_capability("query_engine", runtime=runtime)
            if query_engine is not None and isinstance(
                query_engine.provider, RefQueryCatalogManager
            ):
                catalog = query_engine.provider.provision_ref_query_catalog(ref)

    return DbtRuntimeConfig(
        profile_name=resolve_dbt_profile_name(settings.dbt_project_path),
        target_name=target_name,
        engine_type=settings.dbt_query_engine_type,
        password=settings.dbt_query_password,
        user=settings.dbt_query_user,
        host=settings.dbt_query_host,
        port=settings.dbt_query_port,
        catalog=catalog,
        schema=settings.dbt_query_schema,
        threads=settings.dbt_query_threads,
        http_scheme=settings.dbt_query_http_scheme,
        method=settings.dbt_query_auth_method,
    )


def resolve_dbt_profile_name(project_dir: Path) -> str:
    """Resolve the dbt profile name declared by the project, if any.

    Reads the profile name out of dbt_project.yml, falling back to the default
    profile name when the file is missing or declares none.

    Example:
        >>> profile = resolve_dbt_profile_name(Path("/app/workflows/transforms/dbt"))
        >>> print(profile)
        phlo  # or the profile name from dbt_project.yml
    """
    project_file = project_dir / "dbt_project.yml"
    if not project_file.exists():
        return DEFAULT_DBT_PROFILE_NAME
    try:
        payload = yaml.safe_load(project_file.read_text(encoding="utf-8")) or {}
    except Exception:
        return DEFAULT_DBT_PROFILE_NAME
    profile_name = payload.get("profile")
    if isinstance(profile_name, str) and profile_name:
        return profile_name
    return DEFAULT_DBT_PROFILE_NAME


def render_dbt_profile_yaml(config: DbtRuntimeConfig) -> str:
    """Render canonical dbt runtime config as `profiles.yml` text.

    Example:
        >>> config = DbtRuntimeConfig(target_name="prod")
        >>> yaml_text = render_dbt_profile_yaml(config)
        >>> print(yaml_text)
        phlo:
          target: prod
          outputs:
            prod:
              type: trino
              ...
    """
    return yaml.safe_dump(config.to_profile_payload(), sort_keys=False)


def write_dbt_profile(
    config: DbtRuntimeConfig,
    profiles_dir: Path,
    *,
    filename: str = "profiles.yml",
    force: bool = False,
) -> Path:
    """Write canonical `profiles.yml` to disk and return its path, creating the
    profiles directory if it does not exist.

    When the target file already exists and declares a **different engine**
    than the config would write, it is preserved untouched and the path is
    returned unchanged - capability discovery must never clobber a
    hand-tuned non-default profile (e.g. dbt-clickhouse). Callers that own
    the file can pass ``force=True`` to overwrite regardless.

    Raises: OSError when directory creation or the file write fails.

    Example:
        >>> config = DbtRuntimeConfig(target_name="prod")
        >>> path = write_dbt_profile(config, Path("/app/profiles"))
        >>> print(f"Profile written to: {path}")
    """
    profiles_dir.mkdir(parents=True, exist_ok=True)
    profile_path = profiles_dir / filename

    if not force and profile_path.exists():
        try:
            existing = yaml.safe_load(profile_path.read_text(encoding="utf-8")) or {}
            engines: set[str] = set()
            if isinstance(existing, dict):
                for profile in existing.values():
                    outputs = profile.get("outputs", {}) if isinstance(profile, dict) else {}
                    if not isinstance(outputs, dict):
                        continue
                    for output in outputs.values():
                        if isinstance(output, dict) and output.get("type"):
                            engines.add(str(output["type"]).lower())
            requested = config.engine_type.lower()
            if engines and requested not in engines:
                logger.warning(
                    "dbt_profile_engine_mismatch_preserved",
                    path=str(profile_path),
                    existing_engines=sorted(engines),
                    requested_engine=requested,
                )
                return profile_path
        except Exception:  # noqa: BLE001 - preservation is best-effort only
            pass

    profile_path.write_text(render_dbt_profile_yaml(config), encoding="utf-8")
    return profile_path


def ensure_dbt_profile(
    profiles_dir: Path,
    *,
    runtime: RuntimeContext | None = None,
    target: str | None = None,
) -> Path:
    """Resolve and write canonical `profiles.yml` for the active dbt target.

    Combines configuration resolution and profile writing into a single
    convenience function so a valid profiles.yml always exists for the given
    runtime context and target.

    Example:
        >>> from phlo_dbt.runtime_config import ensure_dbt_profile
        >>> path = ensure_dbt_profile(
        ...     Path("/app/profiles"),
        ...     target="prod"
        ... )
        >>> print(f"Profile ready at: {path}")
    """
    return write_dbt_profile(
        resolve_dbt_runtime_config(runtime, target=target),
        profiles_dir,
    )
