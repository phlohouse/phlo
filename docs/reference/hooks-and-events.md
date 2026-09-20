# Hooks and events

Phlo hooks provide an extension boundary for telemetry, alerts, lineage, metadata catalogues, and run evidence. The event definitions are in `src/phlo/hooks/events.py`, and registration types are in `src/phlo/plugins/hooks.py`.

## Event types

The immutable event dataclasses are `ServiceLifecycleEvent`, `IngestionEvent`, `TransformEvent`, `PublishEvent`, `QualityResultEvent`, `LineageEvent`, `TelemetryEvent`, `SchemaMigrationEvent`, `DataMigrationEvent`, `RunEvidenceObservationEvent`, and `LogEvent`.

Every event inherits `HookEvent`, which carries `event_type`, `version`, `event_id`, `producer`, `timestamp`, `tags`, and `correlation`. `HookCorrelation` can carry request, trace, span, project, run, asset, job, partition, and check identifiers.

## Register a hook

Create a `HookRegistration` with a `hook_name`, handler, optional priority, optional `HookFilter`, and a `FailurePolicy`.

```python
from phlo.plugins.hooks import FailurePolicy, HookFilter, HookRegistration

registration = HookRegistration(
    hook_name="record-publish",
    handler=handle_publish,
    priority=100,
    filters=HookFilter(event_types={"publish.end"}),
    failure_policy=FailurePolicy.LOG,
)
```

`HookFilter` can match event types, asset keys, and tags. A plugin exposes registrations through the `HookProvider.get_hooks` protocol or by subclassing `HookPlugin`.

## Dispatch order

`HookBus` sorts registrations by ascending priority. It breaks equal-priority ties by plugin name and hook name, which makes dispatch deterministic.

The default `LOG` policy records handler failures and continues with the remaining handlers. `IGNORE` also permits dispatch to continue. `RAISE` propagates the failure and stops dispatch.

## Package consumers

The alerting package consumes hook events through `AlertingHookPlugin`. The lineage package records lineage events. OpenMetadata translates metadata events, `phlo-otel` exports telemetry, and `phlo-observe-plugin` translates events for its Observatory service.

The bus discovers core telemetry and run-evidence providers lazily, then discovers installed plugin providers through the hook entry point group. Install the package that owns the integration before expecting its handler to be registered.

## Class reference

See [Plugin API](plugin-api.md) for the plugin entry points, service definitions, and extension interfaces.
