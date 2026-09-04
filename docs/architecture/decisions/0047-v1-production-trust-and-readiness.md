# ADR 0047: V1 Production Trust and Readiness Contract

## Status

**Accepted**

- Date: 2026-08-31
- Decision owner: Phlo project maintainers
- Scope: the blessed v1 single-project, single-tenant production stack
- Supersedes: production guidance that permits unsigned proxy identity or shared workload credentials

## Context

Phlo's v1 production promise is an operator outcome, not a collection of configuration switches.

An operator must be able to install one exact release set, deploy the blessed stack securely, run and inspect a complete pipeline, recover it, and upgrade it through supported workflows.

That promise requires a closed trust topology and evidence contract. Every caller, receiver, identity, authorization authority, audit source, and network boundary must have one explicit owner.

The current code contains useful primitives, but it does not yet make that promise true:

- `phlo-api` classifies only `/health` as public, while general RBAC enforcement is still coupled to regulated mode.
- Regulated validation records some policy drift as a warning and does not define production deployment readiness.
- Current proxy guidance permits unsigned identity headers and documents a direct-port authentication bypass.
- Scoped HMAC service-token primitives exist, but their current envelope does not define key rotation.
- The support manifest requires TLS, OIDC/JWT, isolated workloads, backend-native policy and audit, secure secret files, and durable mutation evidence.

This ADR freezes the target contract. It changes no runtime behavior, supplies no credentials, and promotes no support gate.

## Decision

### 1. Scope, terminology, and invariants

The v1 production deployment is one Phlo project and one tenant. Multi-tenancy, Kubernetes, high availability, multi-region operation, and RPO/RTO guarantees are outside this decision.

The blessed stack contains Traefik, oauth2-proxy, Observatory, `phlo-api`, Dagster webserver and daemon, Trino, Nessie, MinIO, and PostgreSQL. MCP and the CLI are operator clients of `phlo-api`.

The following terms are normative:

- **Public edge**: the TLS listener reachable by an operator's browser or remote client.
- **Public API path**: an API path that accepts an anonymous request. Only `phlo-api` `/health` qualifies.
- **Private service path**: a path reachable only on loopback or a private deployment network.
- **Principal**: a cryptographically verified human or workload identity.
- **Scope**: a token-level upper bound. Scope never grants an action that RBAC denies.
- **Production-ready deployment**: one release/configuration digest whose static preflight and runtime attestation both pass.

Five invariants apply throughout the stack:

1. Authentication establishes a principal; authorization decides an action on a resource.
2. A missing or unavailable required control never becomes an implicit allow.
3. Browser convenience, network location, and forwarded headers are not identities.
4. A privileged mutation without durable audit persistence does not execute.
5. Static configuration evidence and observed runtime evidence are never conflated.

### 2. Exact trust topology

#### 2.1 Human and operator entry paths

| Boundary | Caller | Receiver | Authentication | Authorization | Reachability and audit |
| --- | --- | --- | --- | --- | --- |
| Browser → edge | human | Traefik | TLS, then oauth2-proxy OIDC session | edge route policy | public `:443`; edge access log |
| Edge → oauth2-proxy | Traefik | oauth2-proxy | private-network service path | OAuth flow endpoints only | private; proxy auth log |
| Edge → Observatory | authenticated browser | Observatory | edge-validated OIDC session | edge route policy | private upstream; edge access log |
| Observatory → API | human through Observatory | `phlo-api` | the human bearer JWT is forwarded unchanged | API scope ceiling plus API RBAC | private or edge API route; API audit |
| Browser/remote client → API | human | `phlo-api` | bearer JWT verified by the API | API scope ceiling plus API RBAC | edge only; API audit |
| CLI → API | operator | `phlo-api` | verified human JWT | API scope ceiling plus API RBAC | edge or authenticated private port; API audit |
| MCP → API | `phlo-mcp` workload | `phlo-api` | Phlo workload token; `aud=phlo-api`; declared MCP scope | MCP scope ceiling plus API RBAC | edge or authenticated private port; API audit |

Observatory must not replace a human principal with a generic Observatory identity for governed actions. If it cannot forward the verified human token, the action is unavailable.

The edge may use cookies to maintain the browser session. For API requests, oauth2-proxy must pass the resulting bearer token; `phlo-api` independently verifies that token.

Unsigned `X-Forwarded-*` identity headers are rejected in production. They are neither a fallback identity nor additional authority, even when the request originates from Traefik.

OIDC login, callback, and logout endpoints are necessarily anonymous edge endpoints. They are not public `phlo-api` paths and cannot reach governed API handlers without a verified token.

#### 2.2 Control-plane and data-plane paths

| Boundary | Caller identity | Receiver | Credential and audience/role | Receiver authority | Reachability and audit |
| --- | --- | --- | --- | --- | --- |
| API → Dagster | `phlo-api` | Dagster webserver | Phlo workload token; `aud=phlo-dagster`; `scp=dagster:control` | Dagster caller-to-action policy | private; Dagster event log plus API correlation ID |
| Dagster → API | `phlo-orchestration` | `phlo-api` | Phlo workload token; `aud=phlo-api`; `scp=api:orchestrate` | API RBAC | private; API audit |
| API → PostgreSQL | `phlo-api` | PostgreSQL | API database credential and API role | PostgreSQL grants | private; PostgreSQL audit |
| API → Trino | `phlo-api` | Trino | API query credential and role | Trino access control | private; Trino event audit with human initiator correlation |
| API → Nessie | `phlo-api` | Nessie | API catalog credential and role | Nessie authorization | private; Nessie security/commit audit with human initiator correlation |
| Orchestration → Trino | `phlo-orchestration` | Trino | orchestration credential and query role | Trino access control | private; Trino event audit |
| Orchestration → Nessie | `phlo-orchestration` | Nessie | orchestration credential and catalog role | Nessie authorization | private; Nessie security/commit audit |
| Orchestration → MinIO | `phlo-orchestration` | MinIO | orchestration service account and bucket policy | MinIO policy | private; MinIO audit webhook |
| Orchestration → PostgreSQL | `phlo-orchestration` | PostgreSQL | orchestration database credential and role | PostgreSQL grants | private; PostgreSQL audit |
| Trino → Nessie | `phlo-query` | Nessie | query credential and catalog-read/write role | Nessie authorization | private; Nessie security/commit audit |
| Trino → MinIO | `phlo-query` | MinIO | query service account and object policy | MinIO policy | private; MinIO audit webhook |
| Nessie → PostgreSQL | `phlo-catalog` | PostgreSQL | catalog database credential and schema role | PostgreSQL grants | private; PostgreSQL audit |
| Nessie → MinIO | `phlo-catalog` | MinIO | catalog service account and metadata policy | MinIO policy | private; MinIO audit webhook |
| Maintenance → Trino/Nessie/MinIO | `phlo-maintenance` | provider receivers | maintenance-only credentials and roles | each receiver's native policy | private; provider audit plus operation journal |

The table defines identities and authority boundaries, not a requirement to use one credential technology across unrelated products.

Provider-native passwords, service accounts, OAuth clients, certificates, and roles remain owned by their provider packages. Phlo's HMAC envelope is only for Phlo-controlled HTTP receivers.

Dagster webserver, Trino, Nessie, MinIO, and PostgreSQL have no public host binding in production. A diagnostic binding must be loopback-only, authenticated, disabled by default, and visible in readiness evidence.

`phlo-api` may expose an authenticated private port for CLI, MCP, and service callers. Internet-facing API traffic enters through Traefik; a direct anonymous request never succeeds.

### 3. Human identity and API authorization

`phlo-api` is the final authority for API authentication. Successful edge authentication does not exempt the API from verifying the token.

The API accepts an asymmetric, issuer-signed JWT only when all of these conditions hold:

- the signature algorithm is on an explicit asymmetric allowlist;
- the signing key is selected by `kid` from the configured issuer's JWKS;
- `iss` exactly matches the configured issuer;
- `aud` contains the configured `phlo-api` audience;
- `sub` is present and non-empty;
- `exp`, `nbf`, and `iat` pass with a configured clock-skew ceiling;
- required scopes and mapped group claims have the expected type and bounded size.

The API never accepts `alg=none`, symmetric IdP JWT algorithms, an unconfigured issuer, a token-selected JWKS URL, or identity claims sourced only from forwarded headers.

JWKS retrieval uses HTTPS, a bounded response size, an algorithm/key-type allowlist, duplicate-`kid` rejection, and a finite cache lifetime.

A cached valid key may be used until its cache lifetime expires. Once required verification material is unavailable or stale, authentication returns `503`; it never degrades to unsigned identity.

Token scopes are an upper bound on RBAC. A valid token with insufficient scope returns `403`. A sufficient scope with an RBAC denial also returns `403`.

The minimum API scope vocabulary is closed for v1:

- `lakehouse:read`: read-only inspection;
- `lakehouse:operate`: ordinary governed operations;
- `lakehouse:admin`: privileged configuration, recovery, and plugin actions;
- `api:orchestrate`: Dagster-originated callbacks and run evidence;
- declared MCP scopes mapped to the same canonical API actions.

New scopes require an ADR amendment or a separately accepted security decision. Unknown scopes confer no authority.

### 4. Phlo workload-token contract

Phlo-controlled service calls use a versioned envelope:

```text
phlo1.<kid>.<base64url(canonical-json-claims)>.<base64url(hmac-sha256)>
```

The HMAC covers the ASCII prefix, `kid`, and exact encoded claims. The claims are:

```json
{
  "sub": "phlo-api",
  "aud": "phlo-dagster",
  "scp": ["dagster:control"],
  "iat": 1788170400,
  "exp": 1788170700,
  "jti": "128-bit-or-greater-random-value"
}
```

Claims use canonical JSON, UTF-8, and unpadded base64url. Unknown top-level claims are rejected in v1 so a sender cannot rely on authority a receiver ignores.

The receiver validates the version, `kid`, signature, exact audience, allowed caller, exact scope set, time window, and replay state before constructing a principal.

The default token lifetime is 300 seconds and the production ceiling is 300 seconds. Clock skew is configurable up to 30 seconds and does not extend the signer's requested lifetime.

The receiver rejects a token issued too far in the future, expired beyond allowed skew, longer-lived than the ceiling, signed by an inactive key, or containing an unknown caller/audience/scope tuple.

Each caller/audience pair has a distinct key ring. A key used for one pair cannot authenticate another pair, even if its bytes were accidentally duplicated; duplicate secret material is a production preflight failure.

#### 4.1 Rotation protocol

Each key has a non-secret unique `kid`, lifecycle state, and activation time. A signer uses exactly one active key. A verifier may accept the active key and explicitly retained retiring keys.

Rotation follows this order:

1. Generate a new key and unique `kid` outside logs and generated client data.
2. Distribute the new verification key reference to every receiver replica.
3. Confirm every receiver reports the new `kid` as accepted but not yet required.
4. Switch every signer replica to the new active `kid`.
5. Retain the old key for the token ceiling plus maximum clock skew.
6. Remove the old key and verify that it is rejected.

If receiver distribution cannot be confirmed, rotation stops before signer cutover. If signer cutover is partial, both keys remain accepted until all signers converge and the retirement interval elapses.

Key values live in mode-`0600` secret files or an equivalent mounted secret file. Configuration contains references, `kid` values, and metadata only.

Secret files are written atomically with restrictive permissions before they become visible. Secret values never appear in Compose output, process arguments, URLs, logs, errors, reports, or browser data.

#### 4.2 Replay protection

Replay state is keyed by `(audience, kid, jti)` and shared by every replica of that receiver audience. The store atomically records first acceptance until `exp` plus maximum clock skew.

The receiver consumes replay state only after all cryptographic and claim checks pass, and before the protected handler runs. A second acceptance fails even when it reaches another receiver replica.

A replay-store timeout, write failure, or unavailable store returns `503` and does not invoke the handler. Cleanup of expired replay rows is receiver-owned maintenance and cannot delete unexpired rows.

The legacy `caller:audience:timestamp:nonce:hmac` envelope and `PHLO_SERVICE_SECRET` helpers are development-only. Production and regulated receivers reject them without authentication fallback.

### 5. Provider identity, authorization, and audit ownership

Core defines neutral capability and evidence contracts. Each provider package configures or observes its own receiver and translates native state into the neutral result.

Core never imports a provider package. A provider never imports another provider. Cross-provider orchestration resolves neutral capabilities registered by the selected providers.

Three ownership modes are allowed:

- **Configured by Phlo**: an explicit, authorized command applies a provider-native plan.
- **Observed by Phlo**: a read-only adapter queries authoritative provider state.
- **Operator-supplied evidence**: Phlo verifies a structured, attributable, freshness-bounded artifact from an external authority.

Readiness never changes configuration, grants, credentials, governed data, or policy. Runtime attestation may emit a clearly marked audit canary whose only effect is an audit record.

Operator-supplied evidence is not a checkbox or prose assertion. It must identify the subject, issuer, observation time, configuration/release digest, evidence type, and integrity digest or signature.

| Backend | Required policy evidence | Required audit evidence | Production failure examples |
| --- | --- | --- | --- |
| PostgreSQL | exact service roles, schema/table grants, ownership, and absence of runtime superuser use | statement/security audit configured and durably exported with principal, action, object, result, and correlation where supplied | missing/excess grant, shared role, superuser runtime, audit disabled, export unavailable |
| Trino | authentication enabled; non-allow-all access control; exact identities, roles, catalogs, and grants | event-listener or equivalent durable query/security audit with principal, query/action, result, and correlation | allow-all, anonymous query, drift, listener disabled, sink unavailable |
| MinIO | distinct service accounts and exact bucket/prefix policies; no runtime root credentials | audit webhook or equivalent durable sink, enabled and attributable per service account | root/shared key, excess bucket access, webhook disabled, sink unavailable |
| Nessie | authentication and authorization enabled; exact catalog/branch/content rules | durable security access events plus attributable commit history | anonymous access, authz disabled, excess rule, unaudited mutation, sink unavailable |

A native operational log counts as security audit evidence only if it records the verified principal, action, protected resource, decision or outcome, timestamp, and correlation identifier where supplied.

Commit history alone does not prove denied access, authentication failures, or administrative changes. Trino's in-memory query history alone does not prove durable audit retention.

If a pinned backend version cannot expose a required fact or durable audit path, its result is `unavailable`. The production contract remains blocked until the backend or adapter can supply the evidence.

The backend state vocabulary is fixed:

- `passed`: authoritative evidence matches the required state;
- `failed`: authoritative evidence proves unsafe state or drift;
- `unavailable`: required evidence cannot be obtained or verified;
- `not_applicable`: the selected supported topology genuinely omits an optional component.

PostgreSQL, Trino, MinIO, and Nessie are required in the blessed stack, so their production checks cannot use `not_applicable`.

Both `failed` and `unavailable` block production readiness. Excess privilege is drift, not a harmless difference.

### 6. Durable privileged-mutation audit

Privileged mutations include schema/contract generation, governance changes, Observatory publish/retire, maintenance, restore, plugin installation, and any CLI or API write that changes governed state.

Before such a mutation runs, Phlo must have:

- a verified principal;
- an explicit authorization allow decision;
- a durable audit sink ready to accept the attempt;
- an operation and correlation identifier.

The audit record contains principal, credential class, canonical action, canonical resource, decision, timestamp, correlation ID, outcome, and sanitized before/after digests when state changes.

The audit record never contains bearer tokens, secret values, private keys, full DSNs, or unrestricted request bodies.

If the allow-attempt record cannot be durably persisted, the mutation returns `503` and does not execute. Completion or failure is appended under the same operation identifier.

Authorization denials also produce durable audit events. If the sink is unavailable, the request remains denied and the service reports audit unavailability without converting the denial into an allow.

The known CLI-mutation and denial-audit gaps are implementation defects. They are not exceptions to this contract.

### 7. Two-stage production readiness

Production readiness has two stages because local configuration and live backend state are different kinds of evidence.

#### 7.1 Stage A: static preflight

Static preflight runs before container-backend contact or other startup mutation. It inspects only local files, selected services, generated plans, credential references, and declared external endpoints.

It covers at least:

- production environment and non-development Compose selection;
- required HTTP authorization mode and configured authentication provider;
- OIDC issuer, API audience, JWKS URL, and bounded verification settings;
- TLS endpoint declaration;
- distinct workload identities, credential references, key IDs, and roles;
- no bundled, empty, root, default, duplicated, or shared production credential;
- mode and ownership of every generated secret file;
- no public host binding for protected services;
- registration of every required backend readiness adapter;
- configured durable audit destinations;
- canonical policy compilation and desired-policy digest.

A failed static preflight stops before Docker or another container backend is contacted.

#### 7.2 Stage B: runtime attestation

Runtime attestation runs after the internal services have started and passed basic process health. It is non-remediating and runs before the deployment is reported ready.

It covers at least:

- an HTTPS probe of the configured external edge, including certificate and redirect policy;
- JWT verification and the anonymous/allowed/denied/unavailable API matrix;
- direct-port and protected-service reachability from the declared external vantage point;
- active workload caller/audience/scope pairs and replay-store health;
- observed provider identities, grants, policies, audit configuration, and drift;
- end-to-end delivery and retrieval of a uniquely identified, clearly marked audit canary;
- release, configuration, desired-policy, and selected-service digests.

Production services remain fail-closed while runtime attestation is incomplete. Edge routing to governed handlers is not declared ready until Stage B passes.

If Stage B fails, `services start --production` returns failure and names the blocking checks. It does not claim readiness or silently substitute Stage A evidence for a live observation.

Any automatic teardown policy is an operational decision outside this ADR. Failure must preserve enough sanitized evidence for diagnosis and must not expose an unauthenticated service.

#### 7.3 Report contract

Both stages emit the same versioned JSON document with `schema_version = "1"` and a required `stage` of `static_preflight` or `runtime_attestation`.

The report contains:

- report ID, stage, generation time, and evaluator version;
- release, configuration, desired-policy, and selected-service digests;
- environment and selected services;
- overall pass boolean;
- ordered checks using a closed check-ID vocabulary;
- sanitized evidence references;
- deployment-readiness composition when both stages are available.

Every check contains a stable `id`, state, closed `reason_code`, sanitized message, remediation, source, observation time, and non-secret details.

Check IDs name the invariant being tested. Reason codes name why that invariant did not pass. Neither is generated from provider error text.

Messages and details never contain a DSN, token, secret, private key, raw policy containing secrets, complete environment dump, or unrestricted backend response.

Stage B evidence is bound to the exact Stage A release/configuration/policy/service digests. A digest mismatch prevents composition and requires both stages to be evaluated again.

Reports are point-in-time operational evidence. They are not HA, RPO/RTO, compliance certification, release acceptance, or proof that a control will remain healthy.

No previous report is reused as a current pass by default. Repeated-artifact evidence and freshness policy belong to Plan 016 and later support-gate review.

### 8. Required acceptance demonstrations

The implementation is not accepted from schema tests alone. It must demonstrate the following against a production-shaped deployment.

#### 8.1 API and edge

- `/health` succeeds anonymously and exposes no privileged state.
- An anonymous non-public API request returns `401`.
- A valid JWT with insufficient scope returns `403`.
- A valid JWT with no available authorization backend returns `503`.
- An RBAC denial returns `403`; an allow reaches the handler once.
- An unsigned forwarded identity is rejected.
- A direct anonymous API request is rejected.
- Protected backend ports are unreachable from the external vantage point.
- An absent, invalid, or wrongly terminated TLS path blocks runtime readiness.

#### 8.2 Workload identity

- Each allowed caller/audience/scope tuple succeeds once.
- Missing, blank, duplicated, wrong-caller, wrong-audience, and wrong-scope credentials fail closed.
- Expired, future, overlong, tampered, unknown-`kid`, and retired-key tokens fail closed.
- Replay fails across receiver replicas.
- Replay-store outage returns `503` and invokes no handler.
- Rotation completes without an authentication gap, and the retired key is rejected after the overlap interval.
- The legacy shared token is rejected in production.

#### 8.3 Providers and audit

- Every provider principal performs one permitted operation and is denied one forbidden operation.
- Removing a grant, adding excess privilege, disabling auth, or using a root/shared credential blocks readiness.
- Disabling each audit facility or its durable sink blocks readiness.
- A runtime audit canary is attributable, retrievable by its unique ID, and excluded from business-event metrics.
- A privileged mutation persists attempt and outcome records under one correlation ID.
- Audit-sink failure prevents the mutation.
- Making a required provider unavailable yields `unavailable`, never `passed` or `not_applicable`.

Each negative demonstration changes one prerequisite at a time and must identify the expected check ID and reason code.

## Consequences

- Production HTTP security is independent of regulated-compliance mode.
- Unsigned proxy identity and `PHLO_SERVICE_SECRET` remain development-only compatibility paths.
- Provider-native credentials remain provider-owned; Phlo's HMAC contract is limited to Phlo-controlled receivers.
- Startup requires a static preflight followed by runtime attestation. One cannot substitute for the other.
- Backend audit means durable, attributable security evidence, not merely an operational log.
- A backend that cannot expose required evidence cannot participate in the blessed production contract.
- New public routes, scopes, workloads, or backends require review against this ADR.
- Support gates remain blocked until separately authorized release evidence satisfies the support contract.

Downstream Plans 002–005 must be reconciled with this ADR before execution. In particular, Plan 002 owns Stage A, Plan 005 contributes to Stage B, and neither may collapse both stages into a pre-container check.

## Alternatives Considered

### Trusting proxy identity headers

Rejected for production. Network origin and unsigned headers do not provide cryptographic identity, and a direct API path becomes an authentication bypass.

### Proxy-only authentication with no API verification

Rejected. CLI, MCP, and service callers need authenticated API access, and a proxy-only contract makes API correctness depend on network topology.

### Making the API reachable only through the browser proxy

Rejected as the sole contract. It would force non-browser clients through a session-oriented component and would not remove the need for workload identity.

### One shared service secret

Rejected. It cannot distinguish callers, bound compromise, rotate one relationship independently, or support least privilege.

### Keeping the existing unversioned HMAC envelope

Rejected for production. It lacks an explicit expiry, key identifier, closed claims contract, and executable overlap protocol.

### One startup preflight for static and live facts

Rejected. A fresh deployment cannot inspect live backend policy before those backends start, while static configuration cannot prove observed runtime state.

### Treating drift or unavailable evidence as a warning

Rejected for production. A production claim made without required policy or audit evidence is an optimistic pass.

### Treating query, commit, or application logs as audit by default

Rejected. Logs qualify only when they are durable, attributable, complete for the required event class, and verifiably delivered.

## Related

- ADR 0041: Capability Primitives and Orchestrator Adapters
- `registry/support/v1.json` production requirements
- `docs/setup/security.md`, to be superseded where it conflicts with this ADR
- Plan 001: freeze the production trust and readiness contract
- Plans 002–005: implementation slices requiring reconciliation with this contract
- Plan 016: repeated artifact evidence and later promotion inputs
