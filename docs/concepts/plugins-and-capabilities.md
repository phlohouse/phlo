# Plugins and capabilities

Phlo uses plugins and capabilities to keep the core asset model independent of specific infrastructure providers.

## Why capabilities are named

An asset needs functions such as ingestion, table storage, query execution, catalog access, or orchestration. Naming the function separately from its implementation lets a project select a provider at runtime.

For example, a table-store capability may resolve to Iceberg while another installed provider supplies a different implementation. An asset can request a specific provider with `capability_overrides`.

## How plugins become available

Python packages register entry points in Phlo plugin groups. Discovery scans installed distributions and loads valid registrations. A package can therefore add a service, asset provider, quality provider, CLI command, hook, or adapter without changing the core package.

Discovery errors are reported as structured Phlo errors. A failed optional plugin should not hide unrelated installed capabilities.

## How resolution works

Resolution considers the capability type, configured defaults, workflow tags, and asset overrides. An exact provider selection wins over a generic provider selection. If several providers are installed without a selection, the configuration must identify one.

The resolved provider receives the runtime context and specification. The provider owns its connection, execution, and error translation details.

## What a plugin should expose

A plugin declares metadata and implements the interface for its capability. Asset providers return `AssetSpec` values. Resource providers return resource specifications. Orchestrator adapters translate those specifications into runtime definitions.

Service plugins describe images, ports, health checks, and dependencies. Hook plugins register handlers for lifecycle and quality events. CLI plugins expose commands through the plugin registry.

## How to choose a plugin boundary

Use an asset provider when the integration creates data assets. Use a resource provider when it supplies a reusable runtime resource. Use a service plugin when the integration needs a managed container. Use a hook when the integration observes events without owning execution.

Keeping one responsibility per plugin makes capability selection predictable and keeps optional packages independent.

## How a capability reaches an asset

An asset declaration names the capability it needs rather than importing a concrete service implementation. During discovery, the registry collects installed providers and associates each provider with its supported capability types.

Resolution combines the declaration with project configuration and any per-asset override. The result is a provider instance or specification that the execution adapter can use.

The asset does not need to know whether a capability is implemented by a Python library, a service container, or a remote API.

## How plugin discovery is isolated

Plugin discovery loads entry points from installed distributions and validates their metadata before registration. A plugin that cannot be loaded can produce a discovery error without changing the declarations in another package.

The registry keeps provider identity separate from runtime state. This lets commands inspect available providers before a materialization starts and lets tests select a provider without changing workflow source.

Optional capabilities can remain absent. A project only fails when a declaration or command requires a capability that no configured provider can satisfy.

## How providers share specifications

Asset providers return asset and check specifications. Resource providers return service-facing resources such as catalogs, query engines, object stores, and maintenance executors.

Orchestrator adapters consume those specifications and build provider definitions. They do not need to know how an ingestion provider fetched source records or how a quality provider generated a check.

Shared specifications also give the CLI a common surface for validation, status reporting, and support diagnostics.

## How to choose a plugin boundary

A plugin boundary should follow the external responsibility that changes independently. A source connector should own source access. A service plugin should own service declaration. A governance plugin should own policy operations.

Combining unrelated responsibilities makes installation and failure behavior harder to reason about. It also forces projects to install capabilities they do not use.

When an integration only observes lifecycle events, a hook plugin is a better boundary than an asset provider. When it owns execution, an asset or resource provider is the more direct boundary.

## Why the boundary matters

Clear boundaries make upgrades local. A provider can change its service client while the capability specification and workflow declaration remain stable.

They also make support evidence clearer because an error can be attributed to discovery, resolution, provider execution, or orchestration.
