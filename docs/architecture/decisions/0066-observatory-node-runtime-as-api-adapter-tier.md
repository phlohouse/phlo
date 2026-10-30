# ADR 0066: Observatory keeps its Node runtime as an authenticated API-adapter tier

## Status

**Accepted (back-filled 2026-10-30)**

- Original decision: October 2026
- Evidence: [#1051](https://github.com/phlohouse/phlo/pull/1051) (merged 2026-10-04,
  replacement Observatory); issue [#1013](https://github.com/phlohouse/phlo/issues/1013)
  (closed 2026-10-10, scope transferred); issue [#998](https://github.com/phlohouse/phlo/issues/998)
  and [#1111](https://github.com/phlohouse/phlo/pull/1111) (merged 2026-10-11,
  adapter boundary and forwarding tests)
- Decision owner: Phlo project maintainers

## Context

[ADR 0032](0032-eliminate-docker-images-port-observatory-to-python.md) decided to
port Observatory's server functions to `phlo-api` and turn Observatory into a pure
client-side single-page application served by `phlo-api`. The backend logic did
move to `phlo-api`, but the shipped Observatory is not a static bundle.

The replacement UI that landed in #1051 is a TanStack Start application with a
Node server. `packages/phlo-observatory` builds it into its own container image
(`ghcr.io/phlohouse/phlo-observatory`, port 3000, `PHLO_API_URL` pointing at
`phlo-api`), and its native development command is `npm run dev`. The Python
package registers the service and its extension manifests; it does not serve the
frontend. The Node server forwards the user's bearer token to `phlo-api`, which
verifies it independently.

Issue #1013 asked for an explicit decision reconciling ADR 0032's pure-SPA target
with this runtime, an inventory of every server handler, and an executable guard.
The repository owner approved transferring that scope to #998, with the
instruction that the shipped authenticated Node runtime is retained and that a
zero-server-function or pure-SPA migration is no longer required.

## Decision

1. **Retain the Node runtime.** Observatory runs as its own service with a
   TanStack Start Node server. ADR 0032's pure-SPA target is not pursued merely to
   remove working identity forwarding.
2. **`phlo-api` owns backend semantics.** Authorisation, provider access, queries,
   governance and backend state belong to `phlo-api`.
3. **Node handlers are API adapters only.** A handler may validate input, forward
   the user's identity, call `phlo-api`, parse the response and assemble
   presentation data. It must not access providers or databases, hold shared
   backend credentials, issue SQL, or call non-canonical endpoints. All 66
   production handlers, in 11 adapter modules, are classified as authenticated
   API adapters; there are no UI-only or approved duplicate backend handlers
   ([test and contract gates](../../contributing/testing-gates.md#retained-node-runtime-and-handler-ownership)).
4. **The boundary is enforced.** `scripts/adapter-boundary.mjs` inspects
   production TypeScript with the TypeScript AST and rejects provider or database
   dependencies, I/O outside the authenticated transport, shared credentials, SQL
   in adapters, non-canonical endpoints and server functions outside the adapter
   directory. Negative tests insert prohibited calls.
5. **Forwarding and contracts are tested.** `forwarding.test.ts` uses a real HTTP
   listener to prove caller bearer precedence and the absence of a shared-token
   bypass, and to check that HTTP failures and environment isolation are preserved.
   `scripts/client-contracts.mjs` compares production API calls and their Zod
   parsers with OpenAPI generated from the installed FastAPI application.

## Consequences

- Observatory is a separate container and process from `phlo-api`, so it has its
  own image, port and health check (`/healthz`) in the blessed stack.
- Adding a feature that needs backend data means adding a `phlo-api` endpoint and
  a thin adapter, not logic in the Node server.
- The guard constrains code shape. It does not prove that a new algorithm in an
  adapter has no backend semantics; review still matters.
- Retirement of legacy API URLs and their consumers belongs to issue #1035, not
  to this decision.

## Alternatives Considered

- **Complete ADR 0032's pure SPA served by `phlo-api`:** not pursued; it would
  remove working authenticated forwarding without a reviewed replacement.
- **Allowing server handlers to call providers directly:** rejected; it would
  duplicate authorisation and source-of-truth logic outside `phlo-api`.

## Related

- [ADR 0026](0026-observatory-auth-and-realtime.md): earlier Observatory authentication.
- [ADR 0031](0031-observatory-as-core-and-dx-improvements.md) and [ADR 0032](0032-eliminate-docker-images-port-observatory-to-python.md): the earlier Observatory direction.
- [ADR 0042](0042-observatory-extension-manifests-and-native-ui-plugins.md): extension manifests registered by the Python package.
- [ADR 0047](0047-v1-production-trust-and-readiness.md): Observatory forwards the human token and never substitutes its own identity.
