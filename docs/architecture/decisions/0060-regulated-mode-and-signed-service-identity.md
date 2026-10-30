# ADR 0060: Regulated mode and signed service-to-service identity

## Status

**Accepted (back-filled 2026-10-30)**

- Original decision: April–July 2026
- Evidence: [#466](https://github.com/phlohouse/phlo/pull/466) (merged 2026-04-22,
  regulated surface model); [#583](https://github.com/phlohouse/phlo/pull/583)
  (2026-07-14, authenticated API and Dagster boundaries);
  [#600](https://github.com/phlohouse/phlo/pull/600) (2026-07-18, scoped service
  token core); [#611](https://github.com/phlohouse/phlo/pull/611) (2026-07-18)
- Decision owner: Phlo project maintainers
- Later changes: [ADR 0047](0047-v1-production-trust-and-readiness.md)
  (2026-08-31) replaces the production trust topology and requires workload-key
  rotation, implemented by [#813](https://github.com/phlohouse/phlo/pull/813)
  (2026-09-03). This record keeps the earlier decisions that remain in the code.

## Context

The original regulated-mode work protected `phlo-api`, the CLI and Dagster. #466
stated that this "was not enough for the actual deployment model, because real
users and operators interact with more than those three entrypoints", and that
the aim was "not to pretend every surface has identical enforcement" but to make
the security model of each surface explicit and testable. Internal calls from
`phlo-api` to Dagster and Trino identified themselves with headers, which a
caller could spoof.

## Decision

1. **Regulated mode is an explicit deployment posture.** It is enabled with
   `PHLO_REGULATED` or `regulated: true` in `phlo.yaml`, in that order of
   precedence (`phlo.security.mode.is_regulated`). The earlier `PHLO_REGULATED_MODE`
   variable is a deprecated fallback with a 0.19.0 removal release
   ([ADR 0065](0065-deprecation-and-removal-schedule.md)).
2. **Every surface has a classification.** `phlo.security.gating` and the
   [regulated surface inventory](../regulated-surface-inventory.md) place each
   surface in one class: control-plane enforced (`phlo-api`, CLI, Dagster
   webserver), autonomous execution (Dagster daemon), data-plane governed through
   compiled backend RBAC (Trino, PostgreSQL, MinIO, Nessie), ingress-gated and
   write-restricted (Hasura, PostgREST, Superset), inherited protection, or blocked
   (pgweb, direct OpenMetadata). In regulated mode, blocked services cannot be
   selected and write-restricted services are read-only unless `phlo.yaml` opts in.
3. **Three principal types.** `user` (a human through IdP, proxy or JWT),
   `service` (`service:<name>`) and `platform` (for example
   `platform:dagster-daemon`, for scheduled and sensor runs with no human request).
4. **Signed, scoped service tokens instead of identity headers.** Service calls
   carry an HMAC token bound to a named caller and audience, signed with the
   credential assigned to that caller and audience pair, with a bounded lifetime.
   The nonce is consumed atomically through an injected durable store, PostgreSQL
   in production, so two receivers cannot both accept one token (#600). The
   originating user travels in `X-Phlo-Initiator` and the audit correlation in
   `X-Phlo-Correlation-Id`.
5. **The shared-secret token is development-only.** Tokens derived from
   `PHLO_SERVICE_SECRET` cannot be minted or accepted in production or regulated
   environments.
6. **Fail closed.** Regulated JWT validation requires constrained algorithms and
   configured issuer, audience and subject claims. Missing services, surfaces,
   identity claims or authorisation capabilities deny the request (#583).

## Consequences

- Operators know which surfaces Phlo enforces directly and which remain their
  responsibility through ingress and backend configuration.
- ADR 0047 decided that production HTTP authorisation must not depend on regulated
  mode. `requires_http_authorization()` is now true for `prod`, `production`,
  `staging` and `regulated` environments as well as in regulated mode, and is
  separate from `is_regulated()`.
- The original token envelope had no key identifier. ADR 0047 required rotation,
  and #813 replaced the envelope with versioned `phlo1.<kid>` tokens, per-caller
  key rings and replay keyed by audience, key and token ID. The single-use nonce,
  caller and audience binding and durable replay store from #600 remain.
- Compliance operations such as evidence export, sealed audit chains and
  recertification were explicitly out of scope for #466.

## Alternatives Considered

- **Protecting only `phlo-api`, the CLI and Dagster:** the original scope;
  rejected in #466 because operators reach other surfaces.
- **Trusting forwarded identity headers between services:** rejected because
  they can be spoofed. ADR 0047 later rejects unsigned forwarded identity at the
  edge as well.
- **One shared HMAC secret for all services:** kept only for development; it
  cannot distinguish callers or audiences.

## Related

- [ADR 0026](0026-observatory-auth-and-realtime.md): earlier Observatory authentication.
- [ADR 0047](0047-v1-production-trust-and-readiness.md): the v1 production trust contract that supersedes the production parts of this record.
- [Authentication and access reference](../../reference/auth-and-access.md).
- `src/phlo/security/mode.py`, `src/phlo/security/gating.py`, `src/phlo/security/service_identity.py`.
