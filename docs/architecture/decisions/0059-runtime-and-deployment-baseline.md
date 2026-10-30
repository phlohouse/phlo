# ADR 0059: Python 3.12 and generated Docker Compose as the runtime baseline

## Status

**Accepted (back-filled 2026-10-30)**

- Original decision: December 2025 (generated Compose), July 2026 (support
  claims), September 2026 (Python 3.12 minimum)
- Evidence: [#52](https://github.com/phlohouse/phlo/pull/52) (2025-12-13,
  root `docker-compose.yml` removed); [#579](https://github.com/phlohouse/phlo/pull/579)
  (2026-07-13, runtime and exclusion claims); [#962](https://github.com/phlohouse/phlo/pull/962)
  (2026-09-22, minimum raised from Python 3.11 to 3.12);
  [#1104](https://github.com/phlohouse/phlo/pull/1104) (2026-10-14, typed service topology)
- Decision owner: Phlo project maintainers

## Context

The first commit of the repository (`f47be587`, 2025-10-07) contained an
authored root `docker-compose.yml`. The root file was deleted in #52 when
service definitions moved into packages, and
[ADR 0030](0030-unified-plugin-system-with-registry.md) then made each provider
package own its `service.yaml`. No issue or document records why Docker Compose
was chosen over another deployment substrate; that rationale is not recoverable.

The project declared `requires-python = ">=3.11"` from October 2025 until
#962 raised the minimum to 3.12 across packages, examples, CI, release tooling,
scaffolds, support metadata and images. #962 does not state a reason for the
change. The same pull request made the published `phlo-observe` SDK the
canonical telemetry layer, and `phlo-observe`, `phlo-observe-core` and
`phlo-observe-query` all declare `Requires-Python >=3.12`. Review of #962 also
notes PEP 695 generic rewrites; the code base now uses PEP 695 classes and
`type` aliases in 11 places, including `phlo.run_evidence` and `phlo.migrations`.

## Decision

1. **Python 3.12 is the only supported interpreter.** `requires-python = ">=3.12"`,
   Ruff `target-version = "py312"`, ty `python-version = "3.12"`, and the CI
   test matrix runs 3.12 only. The support manifest lists 3.13 and later as
   unverified.
2. **Linux with Docker Compose v2 is the supported container path.** The support
   manifest's `runtime` section supports Linux, tested on `ubuntu-latest`, and
   "Docker Compose v2 on the CI-tested Linux path". Native macOS and Windows,
   Podman, other Linux distributions and non-x86 architectures are listed as
   unverified. A Podman backend exists in `phlo.infrastructure.container_backend`
   but has no CI lane.
3. **Compose is generated, not authored.** `phlo services init` generates
   `.phlo/docker-compose.yml` and its environment files from the selected
   packages' service manifests. `ServiceDefinition` is the single typed
   authority for service identity, dependencies and ports ([#1104](https://github.com/phlohouse/phlo/pull/1104)).
   Personal changes go in the project's `compose.yaml` and override files, not in
   the generated output. The repository root has no Compose file; project
   lifecycle belongs to the `phlo` CLI.
4. **Native processes are a development option.** Services whose manifest declares
   `dev.command` (currently `phlo-api` and Observatory) can run as host processes
   with `phlo services start --native`, and `phlo dev` runs Dagster on the host.
   Storage, catalogue and query services still run in containers.
5. **One project, one tenant.** The support manifest excludes multi-tenancy,
   Kubernetes-native operation, high availability and multi-region deployment.

## Consequences

- Supporting Python 3.13 requires adding it to the CI matrix and the support
  manifest. Returning to 3.11 would require removing PEP 695 syntax and the
  `phlo-observe` dependency.
- Service topology changes are made in the owning package's manifest and checked
  by `make docs-reference-check`; the generated reference is never edited by hand.
- The Windows Compose portability workflow checks that generated configuration
  and launchers are portable. It does not make native Windows a supported runtime.
- Kubernetes users need their own translation of the generated Compose
  configuration; Phlo makes no support claim for it.

## Alternatives Considered

- **An authored root Compose file:** the original layout, removed in #52 when
  service definitions moved into provider packages.
- **Kubernetes or Helm as a supported substrate:** excluded from the v1 support
  boundary. No issue records a design for it.
- **Keeping Python 3.11:** superseded by #962. Keeping it would have prevented
  adopting the published observer SDK.

## Related

- [ADR 0007](0007-cli-services-architecture.md) and [ADR 0029](0029-cli-services-enhancements.md): the `phlo services` CLI.
- [ADR 0030](0030-unified-plugin-system-with-registry.md): package-owned service manifests.
- [ADR 0047](0047-v1-production-trust-and-readiness.md): the production deployment scope.
- [ADR 0058](0058-support-manifest-as-support-authority.md): where runtime claims are recorded.
- [Service topology](../service-topology.md) and [Install Phlo](../../getting-started/install.md).
