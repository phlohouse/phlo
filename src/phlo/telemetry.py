"""Optional bridge to the phlo-observe SDK (``observe-core`` + ``phlo-observe``).

All phlo-internal emission of canonical observability events routes through
this module so the SDK remains an optional dependency: when it is not
installed (or observability is disabled) every helper degrades to a no-op and
pipeline execution is unaffected.

Configuration is environment-driven via ``configure_phlo``:

- ``OBSERVE_HTTP_ENDPOINT`` enables emission and appends an HTTP drain
  (``OBSERVE_HTTP_TOKEN`` / ``OBSERVE_HTTP_API_KEY`` supply credentials).
- ``OBSERVE_DRAINS`` selects drains explicitly (``console,http,...``).
- ``PHLO_OBSERVE_ENABLED=false`` disables emission outright; ``true`` enables
  the SDK defaults even without an endpoint.
- With neither endpoint nor drains configured the runtime stays disabled so
  plain installs emit nothing.

Optional dependencies are resolved through ``importlib`` so static analysis
never sees the imports: on Python versions the SDK does not support, or in
environments where it is simply not installed, this module still loads.
"""

from __future__ import annotations

import contextlib
import importlib
import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from phlo.logging import get_logger

logger = get_logger(__name__)

_sdk: Any | None = None
_sdk_checked = False
_configured = False
_configure_failed = False


def _import_optional(module_name: str, attr: str | None = None) -> Any | None:
    """Import an optional module/attribute; None when unavailable."""
    try:
        module = importlib.import_module(module_name)
        return getattr(module, attr) if attr else module
    except Exception:  # noqa: BLE001 - any failure means "optional dep absent"
        return None


def _sdk_module() -> Any | None:
    """Return the imported ``phlo_observe`` module, or None when unavailable."""
    global _sdk, _sdk_checked
    if _sdk_checked:
        return _sdk
    _sdk_checked = True
    _sdk = _import_optional("phlo_observe")
    if _sdk is None:
        logger.debug("phlo_observe_unavailable")
    return _sdk


def _observe_core() -> Any | None:
    """Return the ``observe_core`` module when the SDK is importable."""
    if _sdk_module() is None:
        return None
    return _import_optional("observe_core")


def available() -> bool:
    """Return whether the phlo-observe SDK is importable in this environment."""
    return _sdk_module() is not None


def enabled() -> bool:
    """Return whether emission is live: SDK present, configured, and enabled.

    Callers translating upstream events (hook plugins, adapters) should gate
    on this so a disabled or absent SDK costs one cheap check rather than a
    full translation whose emit() drops at the runtime anyway.
    """
    if _observe_core() is None or not configure():
        return False
    try:
        runtime_mod = _import_optional("observe_core.runtime")
        if runtime_mod is None:
            return False
        return bool(runtime_mod.get_runtime().settings.enabled)
    except Exception:  # noqa: BLE001 - a broken check must read as disabled
        return False


def _env_flag(name: str) -> bool | None:
    """Parse a boolean-ish environment variable; None when unset."""
    raw = os.environ.get(name)
    if raw is None:
        return None
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def configure(**overrides: Any) -> bool:
    """Configure the observe runtime once per process. Returns success.

    Idempotent: subsequent calls are no-ops. A configuration failure is
    logged and contained so emission sites never need error handling.
    """
    global _configured, _configure_failed
    if _configured:
        return not _configure_failed
    _configured = True
    sdk = _sdk_module()
    if sdk is None:
        _configure_failed = True
        return False
    try:
        enabled_override = _env_flag("PHLO_OBSERVE_ENABLED")
        if enabled_override is False:
            overrides.setdefault("enabled", False)
        elif enabled_override is not True and not (
            os.environ.get("OBSERVE_HTTP_ENDPOINT") or os.environ.get("OBSERVE_DRAINS")
        ):
            # No endpoint and no explicit drains: stay silent rather than
            # defaulting to console output in every Phlo process.
            overrides.setdefault("enabled", False)
        if os.environ.get("OBSERVE_HTTP_ENDPOINT") and not os.environ.get("OBSERVE_DRAINS"):
            # Replace the SDK's default console drain with the HTTP drain that
            # configure_phlo appends for the endpoint; duplicate stdout noise is
            # never the right default inside a production process.
            overrides.setdefault("drains", [])
        overrides.setdefault("service_name", "phlo")
        sdk.configure_phlo(**overrides)
    except Exception as exc:  # noqa: BLE001 - telemetry config must never abort startup
        logger.warning("phlo_observe_configure_failed", error=str(exc))
        _configure_failed = True
        return False
    return True


def reset_for_tests() -> None:
    """Reset cached SDK/config state. Intended for tests only."""
    global _configured, _configure_failed, _sdk_checked
    _configured = False
    _configure_failed = False
    _sdk_checked = False
    _sdk = None
    _unsupported_surface_warned.clear()


class _NullEvent:
    """No-op stand-in for an observe-core event builder."""

    correlation: dict[str, Any]
    attributes: dict[str, Any]
    outcome: Any = None
    severity: Any = None
    error: Any = None
    duration_ms: float | None = None

    def __init__(self) -> None:
        self.correlation = {}
        self.attributes = {}

    def set(self, **attributes: Any) -> None:
        self.attributes.update(attributes)

    def set_correlation(self, **values: Any) -> None:
        self.correlation.update(values)

    def set_entity(self, role: str, identifier: Any) -> None:
        pass

    def set_tag(self, key: str, value: Any) -> None:
        pass


@contextmanager
def _null_scope() -> Iterator[_NullEvent]:
    yield _NullEvent()


def observe(name: str, **kwargs: Any) -> Any:
    """Return an ``observe()`` operation scope, or a no-op scope when disabled."""
    core = _observe_core()
    if core is None or not configure():
        return _null_scope()
    try:
        return core.observe(name, **kwargs)
    except Exception:  # noqa: BLE001 - degrade to no-op rather than break callers
        return _null_scope()


def bind_context(**values: Any) -> Any:
    """Bind ambient correlation values, or a no-op context manager."""
    core = _observe_core()
    if core is None or not configure():
        return contextlib.nullcontext()
    try:
        return core.bind_context(**values)
    except Exception:  # noqa: BLE001
        return contextlib.nullcontext()


def _ambient_value(key: str) -> str | None:
    """Read one ambient correlation value; None when absent or SDK missing."""
    if _observe_core() is None:
        return None
    ambient_correlation = _import_optional("observe_core.context", "ambient_correlation")
    if ambient_correlation is None:
        return None
    try:
        value = ambient_correlation().get(key)
        return str(value) if value else None
    except Exception:  # noqa: BLE001
        return None


def ambient_run_id() -> str | None:
    """Return the currently bound ambient ``run_id`` (physical execution id)."""
    return _ambient_value("run_id")


def ambient_producer() -> str | None:
    """Return the ambient source producer bound by ``bind_context``."""
    if _observe_core() is None:
        return None
    read = _import_optional("observe_core.context", "ambient_producer")
    if read is None:
        return None
    try:
        value = read()
        return str(value) if value else None
    except Exception:  # noqa: BLE001
        return None


def run_entity_id() -> str | None:
    """Return ``run://<ambient producer>/<ambient run_id>`` for the bound scope.

    The observer derives run entities as ``run://<producer>/<run_id>``. SDK
    helpers pin their own producer (``iceberg``, ``trino``), so an ambient
    Dagster ``run_id`` would otherwise derive a phantom ``run://iceberg/<id>``
    entity next to the real ``run://dagster/<id>``. Use this to set the
    ``run`` entity explicitly and keep the entity graph consistent.
    """
    run_id = _ambient_value("run_id")
    if not run_id:
        return None
    run_id_for = _import_optional("observe_core.identifiers", "run_id_for")
    if run_id_for is None:
        return None
    try:
        return str(run_id_for(ambient_producer() or "phlo", run_id))
    except Exception:  # noqa: BLE001
        return None


def bind_run_entity(scope: Any) -> None:
    """Set the ambient run entity on an observe scope's event builder.

    No-op when no run scope is bound or the scope is a null stand-in.
    """
    entity = run_entity_id()
    if entity is None:
        return
    with contextlib.suppress(Exception):
        scope.set_entity("run", entity)


def branch_entity_id(branch: str, *, system: str = "nessie") -> str | None:
    """Return the canonical ``branch://<system>/<branch>`` entity id.

    ``system`` identifies the catalog that owns the staging ref: ``nessie``
    for branch-strategy WAP, the snapshot-promotion catalog's provider name
    (e.g. ``polaris``) for snapshot-strategy candidate namespaces.
    """
    if not branch:
        return None
    branch_id = _import_optional("observe_core.identifiers", "branch_id")
    if branch_id is None:
        return None
    try:
        return str(branch_id(system, branch))
    except Exception:  # noqa: BLE001
        return None


def run_entity_for(producer: str, run_id: Any) -> str | None:
    """Return ``run://<producer>/<run_id>`` for an explicitly identified run.

    Used outside bound scopes (sensors, reconcilers) where the run producer is
    known but no ambient binding exists.
    """
    if not producer or not run_id:
        return None
    run_id_for = _import_optional("observe_core.identifiers", "run_id_for")
    if run_id_for is None:
        return None
    try:
        return str(run_id_for(producer, run_id))
    except Exception:  # noqa: BLE001
        return None


_unsupported_surface_warned: set[str] = set()


def _set_if_supported(
    builder: Any, method: str, items: dict[str, Any] | None, *, event_name: str
) -> None:
    """Call ``builder.<method>(key, value)`` for each non-None item.

    Pre-V2 observe-core builders lack ``set_entity``/``set_tag``; letting the
    AttributeError propagate to ``emit``'s handler would drop the whole event,
    so degrade to a once-per-process warning and skip the unsupported field.
    """
    if not items:
        return
    setter = getattr(builder, method, None)
    if setter is None:
        if method not in _unsupported_surface_warned:
            _unsupported_surface_warned.add(method)
            logger.warning(
                "phlo_observe_sdk_surface_unsupported",
                builder_method=method,
                event_name=event_name,
                hint="installed phlo-observe SDK predates the V2 entity/tag model; those fields are dropped. Install a V2-capable SDK (see PHLO_OBSERVE_SDK) to restore them.",
            )
        return
    for key, value in items.items():
        if value is not None:
            setter(key, value)


def emit(
    name: str,
    *,
    category: Any = None,
    delivery: Any = None,
    severity: Any = None,
    outcome: Any = None,
    attributes: dict[str, Any] | None = None,
    correlation: dict[str, Any] | None = None,
    entities: dict[str, Any] | None = None,
    tags: dict[str, Any] | None = None,
    error: Any = None,
    producer: str | None = None,
    started_at: Any = None,
    ended_at: Any = None,
    duration_ms: float | None = None,
) -> None:
    """Emit one event with full field control, including explicit timestamps.

    Unlike :func:`event`, this path supports ``started_at``/``ended_at`` so
    observers emitting after the fact (sensors, reconcilers) can record the
    real execution window rather than the emission instant. Ambient and
    operation context still merge underneath explicit ``correlation`` values.
    """
    if not enabled():
        return
    try:
        builder_mod = _import_optional("observe_core.builder")
        models_mod = _import_optional("observe_core.models")
        runtime_mod = _import_optional("observe_core.runtime")
        if builder_mod is None or models_mod is None or runtime_mod is None:
            return
        builder = builder_mod.EventBuilder(
            name,
            **{"category": category} if category is not None else {},
            **{"delivery": delivery} if delivery is not None else {},
            **{"severity": severity} if severity is not None else {},
            attributes=attributes,
        )
        if correlation:
            builder.set_correlation(**correlation)
        if outcome is not None:
            builder.set_outcome(outcome)
        if severity is not None:
            builder.set_severity(severity)
        if delivery is not None:
            builder.set_delivery(delivery)
        _set_if_supported(builder, "set_entity", entities, event_name=name)
        _set_if_supported(builder, "set_tag", tags, event_name=name)
        if producer is not None:
            builder.set_source(models_mod.SourceInfo(producer=producer))
        if error is not None:
            builder.set_error(
                error
                if isinstance(error, models_mod.ErrorInfo)
                else models_mod.ErrorInfo(exception_type=type(error).__name__, message=str(error))
            )
        builder.started_at = started_at
        builder.ended_at = ended_at
        builder.duration_ms = duration_ms
        runtime_mod.get_runtime().emit(builder, None)
    except Exception as exc:  # noqa: BLE001 - emission must never break the caller
        logger.debug("phlo_observe_emit_failed", event_name=name, error=str(exc))


class _GuardedContext:
    """Read-proxy that turns raising attributes into ``None``.

    The SDK's Dagster integration duck-types via plain ``getattr`` — Dagster's
    ``partition_key`` (and other run-scoped properties) raise
    ``DagsterInvariantViolationError`` when the run is unpartitioned. Without
    this guard a single guarded property aborts scope creation and silently
    drops ambient correlation for the whole step.
    """

    __slots__ = ("_inner",)

    # Deprecated direct accessors the SDK's duck-typed reads still request.
    # Resolve them through the modern attribute path so the read neither
    # warns nor reaches Dagster's deprecated shim; contexts lacking the path
    # fall back to plain getattr (OpExecutionContext.op, unbound runs).
    _ATTR_TRANSLATIONS: dict[str, tuple[str, ...]] = {
        "run_id": ("run", "run_id"),
        "run_tags": ("run", "tags"),
        "op": ("op_execution_context", "op"),
    }

    def __init__(self, inner: Any) -> None:
        object.__setattr__(self, "_inner", inner)

    def __getattribute__(self, name: str) -> Any:
        # ``__getattribute__`` (not ``__getattr__``): the failing attributes
        # exist — they raise inside their getters, so a missing-attribute
        # fallback would never fire.
        if name == "_inner" or name.startswith("__"):
            return object.__getattribute__(self, name)
        inner = object.__getattribute__(self, "_inner")
        try:
            path = _GuardedContext._ATTR_TRANSLATIONS.get(name)
            if path is not None:
                try:
                    value = inner
                    for part in path:
                        value = getattr(value, part)
                except Exception:
                    value = getattr(inner, name)
                return value
            return getattr(inner, name)
        except Exception:  # noqa: BLE001 - guarded attributes read as missing
            return None


def _guarded(context: Any) -> Any:
    """Wrap a Dagster context in the exception-swallowing read proxy."""
    if context is None or isinstance(context, _GuardedContext):
        return context
    return _GuardedContext(context)


# -- Dagster integration ------------------------------------------------------


def dagster_run_scope(context: Any, *, asset_key: str | None = None) -> Any:
    """Bind Dagster run/job/partition correlation for the enclosed block."""
    if _sdk_module() is None or not configure():
        return contextlib.nullcontext()
    scope = _import_optional("phlo_observe.integrations.dagster", "dagster_run_scope")
    if scope is None:
        return contextlib.nullcontext()
    try:
        return scope(_guarded(context), asset_key=asset_key)
    except Exception:  # noqa: BLE001
        return contextlib.nullcontext()


def dagster_step(context: Any, **kwargs: Any) -> Any:
    """Wrap a Dagster step; emits ``pipeline.step`` on exit."""
    if _sdk_module() is None or not configure():
        return _null_scope()
    step = _import_optional("phlo_observe.integrations.dagster", "dagster_step")
    if step is None:
        return _null_scope()
    try:
        return step(_guarded(context), **kwargs)
    except Exception:  # noqa: BLE001
        return _null_scope()


def emit_materialization(context: Any, **kwargs: Any) -> Any:
    """Wrap an asset materialization; emits ``asset.materialize`` on exit."""
    if _sdk_module() is None or not configure():
        return _null_scope()
    materialize = _import_optional("phlo_observe.integrations.dagster", "emit_materialization")
    if materialize is None:
        return _null_scope()
    try:
        return materialize(_guarded(context), **kwargs)
    except Exception:  # noqa: BLE001
        return _null_scope()


def emit_asset_check(context: Any, *, check_name: str, passed: bool, **kwargs: Any) -> None:
    """Emit a ``quality.check`` event for a Dagster asset check result."""
    if not enabled():
        return
    emit_check = _import_optional("phlo_observe.integrations.dagster", "emit_asset_check")
    if emit_check is None:
        return
    try:
        emit_check(_guarded(context), check_name=check_name, passed=passed, **kwargs)
    except Exception:  # noqa: BLE001
        return


# -- DLT integration ------------------------------------------------------------


def dlt_pipeline_scope(pipeline: Any) -> Any:
    """Bind a dlt pipeline identity for contained events."""
    if _sdk_module() is None or not configure():
        return contextlib.nullcontext()
    scope = _import_optional("phlo_observe.integrations.dlt", "dlt_pipeline_scope")
    if scope is None:
        return contextlib.nullcontext()
    try:
        return scope(pipeline)
    except Exception:  # noqa: BLE001
        return contextlib.nullcontext()


def dlt_pipeline_run(pipeline: Any, **kwargs: Any) -> Any:
    """Wrap ``pipeline.run(...)``; emits ``dlt.pipeline.run`` on exit."""
    if _sdk_module() is None or not configure():
        return _null_scope()
    run = _import_optional("phlo_observe.integrations.dlt", "dlt_pipeline_run")
    if run is None:
        return _null_scope()
    try:
        return run(pipeline, **kwargs)
    except Exception:  # noqa: BLE001
        return _null_scope()


def dlt_load_info_attributes(load_info: Any) -> dict[str, Any]:
    """Extract canonical attributes from a dlt ``LoadInfo``."""
    if _sdk_module() is None or not configure():
        return {}
    extract = _import_optional("phlo_observe.integrations.dlt", "load_info_attributes")
    if extract is None:
        return {}
    try:
        return extract(load_info)
    except Exception:  # noqa: BLE001
        return {}


# -- dbt integration ------------------------------------------------------------


def emit_dbt_run_results(path_or_dict: Any) -> int:
    """Emit ``dbt.invocation`` + per-node events from a dbt run_results doc."""
    if _sdk_module() is None or not configure():
        return 0
    emit_results = _import_optional("phlo_observe.integrations.dbt", "emit_run_results")
    if emit_results is None:
        return 0
    try:
        return emit_results(path_or_dict)
    except Exception as exc:  # noqa: BLE001
        logger.debug("phlo_observe_dbt_results_failed", error=str(exc))
        return 0


# -- WAP integration ------------------------------------------------------------


def wap_branch_create(*, branch: str, base_branch: str | None = None, **kwargs: Any) -> Any:
    """Emit ``wap.branch.create`` around branch creation."""
    if _sdk_module() is None or not configure():
        return _null_scope()
    helper = _import_optional("phlo_observe.integrations.wap", "wap_branch_create")
    if helper is None:
        return _null_scope()
    try:
        return helper(branch=branch, base_branch=base_branch, **kwargs)
    except Exception:  # noqa: BLE001
        return _null_scope()


# -- Iceberg/Nessie integration -------------------------------------------------


def iceberg_commit(*, table: str, **kwargs: Any) -> Any:
    """Emit ``iceberg.commit`` around a table operation."""
    if _sdk_module() is None or not configure():
        return _null_scope()
    helper = _import_optional("phlo_observe.integrations.iceberg", "iceberg_commit")
    if helper is None:
        return _null_scope()
    try:
        return helper(table=table, **kwargs)
    except Exception:  # noqa: BLE001
        return _null_scope()


# -- Trino integration ----------------------------------------------------------


def trino_query(**kwargs: Any) -> Any:
    """Wrap a Trino query; emits ``trino.query`` on exit."""
    if _sdk_module() is None or not configure():
        return _null_scope()
    helper = _import_optional("phlo_observe.integrations.trino", "trino_query")
    if helper is None:
        return _null_scope()
    try:
        return helper(**kwargs)
    except Exception:  # noqa: BLE001
        return _null_scope()
