"""Provider-neutral transformation authoring decorators.

The sql() decorator registers a transform asset at import time: SQL is
captured eagerly by calling the function with no arguments, and
functions with required parameters yield no static SQL rather than
deferring evaluation. Assets accumulate in a module-level list owned by
the core provider; clear_transform_assets() exists for test isolation.

Deprecated: no provider bridges transform specs to the orchestrator, so the
registered asset is unreachable at runtime. sql() emits a DeprecationWarning
at decoration time and will be removed in an upcoming release; define
transformations in dbt or through explicit asset-provider plugins instead.
"""

from __future__ import annotations

import inspect
import warnings
from collections.abc import Callable

from phlo._flow_authoring import (
    AssetDependency,
    append_asset,
    asset_key,
    build_run,
    contract_metadata,
    normalize_asset_deps,
)
from phlo.capabilities import AssetSpec
from phlo.contracts import SLA, Consumer

_TRANSFORM_ASSETS: list[AssetSpec] = []


def sql(
    *,
    table: str,
    depends_on: list[AssetDependency] | None = None,
    materialized: str = "table",
    group: str = "transform",
    owner: str | None = None,
    consumers: list[Consumer | str] | None = None,
    sla: SLA | None = None,
    description: str | None = None,
) -> Callable[[Callable[..., str]], Callable[..., str]]:
    """Register a SQL transform asset.

    Deprecated: the registered asset never reaches the pipeline. The decorator
    will be removed in an upcoming release; define transformations in dbt or
    through explicit asset-provider plugins instead.
    """

    def _decorator(fn: Callable[..., str]) -> Callable[..., str]:
        warnings.warn(
            "phlo.transform.sql is deprecated and will be removed in an "
            "upcoming release: the registered asset never reaches the "
            "pipeline. Define transformations in dbt or through "
            "explicit asset-provider plugins instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        sql_text = _static_sql_text(fn)
        append_asset(
            _TRANSFORM_ASSETS,
            AssetSpec(
                key=asset_key("transform", table),
                group=group,
                description=description or fn.__doc__,
                kinds={"sql", "transform"},
                tags={
                    "provider": "core",
                    "asset_type": "transform",
                    "transform_type": "sql",
                    "materialized": materialized,
                },
                metadata={
                    "table": table,
                    "sql": sql_text,
                    "materialized": materialized,
                    **contract_metadata(owner=owner, consumers=consumers, sla=sla),
                },
                deps=normalize_asset_deps(depends_on),
                run=build_run(fn),
            ),
        )
        return fn

    return _decorator


# SQL is captured eagerly at decoration time by calling fn() with no arguments.
# A function with required parameters cannot be called here, so its SQL is
# treated as unavailable (None) rather than deferring evaluation to run time.
def _static_sql_text(fn: Callable[..., str]) -> str | None:
    signature = inspect.signature(fn)
    required_parameters = [
        parameter
        for parameter in signature.parameters.values()
        if parameter.default is inspect.Parameter.empty
        and parameter.kind
        in {
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        }
    ]
    if required_parameters:
        return None
    return fn()


def get_transform_assets() -> list[AssetSpec]:
    """Return registered transform asset specs."""
    return list(_TRANSFORM_ASSETS)


def clear_transform_assets() -> None:
    """Clear registered transform asset specs."""
    _TRANSFORM_ASSETS.clear()


__all__ = ["clear_transform_assets", "get_transform_assets", "sql"]
