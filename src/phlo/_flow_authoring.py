"""Shared helpers for terse flow authoring decorators.

Private to the decorator layer: normalizes asset keys and dependency
references, builds owner/consumer/SLA contract metadata, and wraps user
functions into RunSpec/AssetSpec objects, injecting RuntimeContext only when
the authored signature asks for it.
"""

from __future__ import annotations

import inspect
import re
from collections.abc import Callable, Iterable
from typing import Any

from phlo.capabilities import AssetSpec, MaterializeResult, RunSpec
from phlo.capabilities.runtime import RuntimeContext
from phlo.contracts import SLA, Consumer, normalize_consumers, serialize_consumers, serialize_sla
from phlo.references import LogicalRelation

AssetDependency = str | LogicalRelation


def asset_key(prefix: str, name: str) -> str:
    """Build a stable asset key from a dotted table or target name."""
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_").lower()
    return f"{prefix}_{normalized}"


def contract_metadata(
    *,
    owner: str | None = None,
    consumers: list[Consumer | str] | None = None,
    sla: SLA | None = None,
) -> dict[str, Any]:
    """Build common owner, consumer, and SLA metadata."""
    return {
        "owner": owner,
        "consumers": serialize_consumers(normalize_consumers(consumers)),
        "sla": serialize_sla(sla),
    }


def build_run(fn: Callable[..., Any]) -> RunSpec:
    """Create a generic run spec around a user-authored function."""

    def _run(context: RuntimeContext) -> Iterable[MaterializeResult]:
        result = _call_with_optional_context(fn, context)
        yield MaterializeResult(metadata={"result": result}, status="success")

    return RunSpec(fn=_run)


# Accepted shapes: zero parameters, or exactly one positional context
# parameter. Keyword-only, *args, and **kwargs parameters are rejected because
# there is no meaningful value to supply for them at run time.
def _call_with_optional_context(fn: Callable[..., Any], context: RuntimeContext) -> Any:
    signature = inspect.signature(fn)
    unsupported_parameters = [
        parameter
        for parameter in signature.parameters.values()
        if parameter.kind
        in {
            inspect.Parameter.KEYWORD_ONLY,
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        }
    ]
    required_parameters = [
        parameter
        for parameter in signature.parameters.values()
        if parameter.default is inspect.Parameter.empty
        and parameter.kind
        in {inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD}
    ]
    if unsupported_parameters or len(required_parameters) > 1:
        function_name = getattr(fn, "__qualname__", None)
        if not isinstance(function_name, str):
            function_name = repr(fn)
        raise TypeError(
            f"Decorated function {function_name}{signature} must accept either "
            "no parameters or one context parameter."
        )
    if required_parameters:
        return fn(context)
    return fn()


def append_asset(registry: list[AssetSpec], asset: AssetSpec) -> None:
    """Register an asset in a module-local registry."""
    registry.append(asset)


def normalize_asset_deps(deps: Iterable[AssetDependency] | None) -> list[str]:
    """Normalize authored dependency references into stable asset keys."""
    if deps is None:
        return []
    return [
        dependency.asset_key if isinstance(dependency, LogicalRelation) else dependency
        for dependency in deps
    ]
