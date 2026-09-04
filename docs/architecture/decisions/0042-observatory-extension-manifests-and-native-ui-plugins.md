# ADR 0042: Observatory Extension Manifests and Native UI Plugins

## Status

**Accepted**

## Context

- Observatory now ships in core, but UI assumes services/packages installed.
- Need packages to:
  - expose settings surfaced in Observatory
  - add pages and UI customization
- Current settings: localStorage + env defaults.
- Requirement: server-wide settings, not per-user.
- UI stack: TanStack Start + Vite; routes are static at build time.
- No iframe-based integrations.

## Decision

### 1. New plugin type: Observatory Extension Plugin

Add `ObservatoryExtensionPlugin` in `src/phlo/plugins/base.py`.

- Entry point group: `phlo.plugins.observatory`
- Plugin exposes manifest + asset root for UI bundles.

Manifest (versioned contract):

```yaml
name: string
version: string
compat:
  observatory_min: string
settings:
  settings_schema: JSONSchema
  defaults: object
  scope: global | extension
ui:
  routes:
    - path: /extensions/<name>/...
      module: <asset_url>
      export: registerRoutes
  nav:
    - title: string
      to: /extensions/<name>/...
  slots:
    - slot_id: string
      module: <asset_url>
      export: registerSlot
  settings:
    - module: <asset_url>
      export: registerSettings
```

### 2. Discovery + API surface

- Discovery wired into `src/phlo/discovery/plugins.py`.
- `phlo-api` exposes:
  - `GET /api/observatory/extensions`
  - `GET /api/observatory/extensions/{name}`
  - `GET /api/observatory/extensions/{name}/assets/*`
  - `GET/PUT /api/observatory/settings`

### 3. Native TanStack integration (no iframes)

UI loads extension ES modules at runtime:

- Dynamic import from `phlo-api` asset endpoint.
- Each module exports registration functions:
  - `registerRoutes(router)` returns TanStack routes
  - `registerSlot(registry)` registers slot components
  - optional `registerSettings(formRegistry)`
- Observatory hosts a slot registry to render extension panels in known locations.

### 4. Server-wide settings

- Settings stored server-side via `phlo-api`.
- UI reads/writes via API.
- LocalStorage only for short-lived cache (optional).

## Rationale

- Entry points match existing plugin architecture.
- Manifests decouple UI from installed service assumptions.
- Native modules enable tight UX without bundling extensions into core build.
- Server-wide settings satisfy shared instance model.

## Consequences

### Positive

- Extensions can add pages and UI panels safely.
- Settings managed centrally, versioned, and auditable.
- Clear contract for plugin authors.

### Negative

- Runtime module loading adds CSP/security requirements.
- Needs asset-serving and version compatibility checks.

## Implementation Outline

1. Add `ObservatoryExtensionPlugin` base + entry point group.
2. Update discovery + registry to list observatory extensions.
3. Extend phlo-api with extension + settings endpoints.
4. Add slot registry + dynamic route loading in Observatory.
5. Add docs + example extension package.

## Alternatives Considered

- Build-time bundling of extension routes only.
  - Rejected: forces rebuild to add/remove extensions.
- iframe embedding.
  - Rejected: disallowed; weak integration and UX.

## Verification

```shell
phlo plugin list --type observatory
curl http://localhost:4000/api/observatory/extensions
curl http://localhost:3001/extensions/<name>/...
```
