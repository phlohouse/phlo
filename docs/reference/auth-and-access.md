# Auth and access reference

Authentication and authorisation in Phlo are layered.

## Target regulated model

Phlo's target regulated posture is an end-to-end chain instead of a single `phlo-api` concern. The current alpha release remains subject to the blocked [v1 support boundary](../../registry/support/v1.json).

The model now connects:

- caller authentication at the edge
- canonical action enforcement at supported request surfaces
- service-to-service identity for internal hops
- platform identity for autonomous Dagster daemon work
- backend-native credentials and grants in the data plane

That means a regulated request can now stay attributable from ingress all the way to backend query and object-store activity, provided you configure the service credentials and ingress boundary described in the setup docs.

## Model

```mermaid
flowchart TD
    principal["User or service principal"] --> authn["Authentication provider"]
    authn --> session["Authenticated session"]
    session --> authz["Authorization policy backend"]
    authz --> surfaces["API and UI surfaces"]
    authz --> data["Data and governance backends"]
```

## Responsibilities

- authentication decides who the caller is
- authorisation decides what that caller may do
- serving layers like `phlo-api`, `Hasura`, and `PostgREST` enforce those decisions in different ways
- governance and backend systems may apply their own secondary controls

## Principal Types

| Type | Subject pattern | When used |
|------|-----------------|-----------|
| `user` | `alice@example.com` | Human via IdP, proxy headers, or JWT |
| `service` | `service:phlo-api` | Named service calling another service |
| `platform` | `platform:dagster-daemon` | Autonomous execution with no live human request |

Regulated mode accepts all three types. `platform` exists so scheduled and sensor-driven Dagster runs remain attributable even when no HTTP request is involved.

## Service Identity And Correlation

Internal service calls should use short-lived HMAC service tokens, not spoofable identity headers. Phlo uses:

- `Authorization: Bearer <service-token>` for the calling service identity
- `X-Phlo-Initiator` for the originating upstream principal when one exists
- `X-Phlo-Correlation-Id` for end-to-end audit reconstruction

`phlo-api` reuses the request ID as the default correlation ID. Dagster daemon flows use the Dagster `run_id` for the same purpose.

## End-to-end regulated path

For the target regulated deployment model, the path is:

1. A user authenticates at ingress or through a configured API auth provider.
2. A control-plane surface such as `phlo-api`, the CLI, or Dagster GraphQL maps the request to a canonical action and resource.
3. Phlo evaluates that action with `enforce()` and emits audit metadata.
4. Downstream service calls carry service identity and correlation headers.
5. Backends such as Trino, PostgreSQL, MinIO, and Nessie see the scoped service credential rather than one shared superuser identity.

If one of those layers is missing, the deployment is only partially regulated.

## phlo-api Route Guard Semantics

- `phlo-api` route guards only enforce authorisation when an authorisation backend is configured
- with the default `PHLO_AUTHORIZATION_MODE=optional`, guarded routes remain reachable when `PHLO_AUTHORIZATION_BACKEND` is unset
- set `PHLO_AUTHORIZATION_MODE=required` to fail closed with HTTP `503` on guarded routes when no authorisation backend is configured
- once a backend is configured, route guards evaluate the caller normally and still return `401` or `403` based on authentication and policy decisions
- regulated mode itself can be enabled with `PHLO_REGULATED=true` or `regulated: true` at the root of `phlo.yaml`

Example:

```yaml
regulated: true

authentication:
  provider: proxy

api:
  authorization:
    backend: opa
    mode: required
```

Built-in authentication provider names include `static`, `proxy`, and `service_token`. Their built-in config blocks live under the same root `authentication` section in `phlo.yaml`.

## Agent and MCP token scopes

Agent-facing `phlo-api` routes require explicit bearer-token scopes. Tokens may come from the configured authentication provider, or from the local `PHLO_API_TOKENS` JSON object for single-project development and MCP use:

```bash
export PHLO_API_TOKENS='{
  "agent-token": {
    "subject": "agent:amp",
    "scopes": ["lakehouse:read", "lakehouse:operate", "project:write"]
  }
}'
```

| Scope | Grants | Examples |
|---|---|---|
| `lakehouse:read` | Read-only inspection | status, logs, traces, assets, schemas |
| `lakehouse:operate` | Data-plane mutations | materialize, retry, cancel, backfill |
| `project:write` | Project filesystem authoring | create workflow, validate workflow/schema, lint project |
| `admin` | All scoped operations | operator break-glass and local automation |

Mutation routes also enforce per-subject rate limits, write JSONL audit records to `.phlo/audit/operations.jsonl`, and honour idempotency keys for repeat-safe operation replay.

You can declare these settings in `phlo.yaml` as either:

```yaml
api:
  authorization:
    backend: opa
    mode: required
```

or, for a service-scoped override:

```yaml
services:
  phlo-api:
    authorization:
      backend: opa
      mode: required
```

Precedence is `env vars` -> `services.phlo-api.authorization` -> `api.authorization`.

## OIDC browser authentication

For browser SSO, run the `api` and `proxy` profiles with Traefik and oauth2-proxy, backed by an OIDC provider such as Authentik. Phlo routes `/oauth2/*` to oauth2-proxy, redirects unauthenticated browser requests to its sign-in page, and protects the API route with forward-auth. oauth2-proxy owns the browser session; `phlo-api` validates the signed access token itself. Forwarded user, email, and group headers are not authentication credentials and are not trusted by the API. Phlo does not install or configure an Authentik server; operators provision the OIDC provider and client.

Configure the same issuer and audience at both layers. For Authentik, use the provider's exact issuer URL and configure the token audience accepted by `phlo-api` (commonly the OIDC client ID). Publish the groups claim in the access token if Phlo RBAC is mapped to identity-provider groups.

Set these values in the deployment environment (keep client and cookie secrets in the deployment's secret store):

```dotenv
PHLO_AUTHENTICATION_PROVIDER=jwt
PHLO_AUTH_JWT_ISSUER=https://auth.example.com/application/o/phlo/
PHLO_AUTH_JWT_AUDIENCE=phlo-api
PHLO_AUTH_JWT_JWKS_URL=https://auth.example.com/application/o/phlo/jwks/
PHLO_AUTH_JWT_GROUPS_CLAIM=groups
OAUTH2_PROXY_PROVIDER=oidc
OAUTH2_PROXY_OIDC_ISSUER_URL=https://auth.example.com/application/o/phlo/
OAUTH2_PROXY_CLIENT_ID=phlo-api
OAUTH2_PROXY_CLIENT_SECRET=<secret>
OAUTH2_PROXY_COOKIE_SECRET=<generated-secret>
OAUTH2_PROXY_COOKIE_SECURE=true
OAUTH2_PROXY_REDIRECT_URL=https://api.example.com/oauth2/callback
```

The issuer, JWKS endpoint, client ID, and audience above are examples: set them to the exact values configured in Authentik. Set `TRAEFIK_DOMAIN` to the host suffix used by the API router, and terminate HTTPS at the trusted edge before forwarding to Traefik. Do not expose the API directly to untrusted networks; direct bearer-token requests are signature-checked, but only the edge provides the browser login flow. Production startup requires issuer, audience, and JWKS configuration; it does not accept the legacy shared-secret JWT mode.

The request path is:

```mermaid
sequenceDiagram
    participant Browser
    participant Traefik
    participant oauth2-proxy
    participant phlo-api

    Browser->>Traefik: GET /api/datasets
    Traefik->>oauth2-proxy: forwardAuth /oauth2/auth
    oauth2-proxy->>Browser: 401 + redirect to IdP login
    Browser->>oauth2-proxy: IdP credentials
    oauth2-proxy->>Browser: Set session cookie
    Browser->>Traefik: GET /api/datasets (with cookie)
    Traefik->>oauth2-proxy: forwardAuth /oauth2/auth
    oauth2-proxy-->>Traefik: 202 + X-Auth-Request-Access-Token
    Traefik->>phlo-api: Proxy request + signed access token
    phlo-api->>phlo-api: Verify signature, issuer, audience, and time claims via JWKS
    phlo-api-->>Traefik: 200 OK
    Traefik-->>Browser: Response
```

The API and oauth2-proxy service definitions include the Traefik routes and forward-auth chain; the callback route is excluded from API authentication to avoid a redirect loop. Forward-auth passes only `X-Auth-Request-Access-Token`. The `jwt` provider verifies it against the configured issuer and JWKS; identity headers such as `X-Forwarded-User` and `X-Forwarded-Groups` cannot establish identity. If JWKS becomes unavailable after its bounded cache expires, authentication fails closed with HTTP 503 until verification material is available again.

## What Phlo enforces vs what operators still own

| Concern | Phlo-owned | Operator-owned |
|--------|------------|----------------|
| Canonical action mapping on `phlo-api`, CLI, Dagster | Yes | No |
| Dagster daemon platform identity | Yes | No |
| Service token format and validation | Yes | No |
| Ingress authentication for browser-only surfaces | No | Yes |
| Hasura/PostgREST/Superset internal permission models | No | Yes |
| Backend role creation, secret rotation, retention posture | Partial | Yes |

This split is deliberate. Phlo owns the control plane it can interpret directly. Operators still own the ingress boundary, optional surfaces without a Phlo adapter, and backend security operations.

## Canonical RBAC

Phlo's canonical RBAC control plane lives under `.phlo/authorization/` and provides a single model for roles, subject assignment, policy validation, backend planning, sync, and drift verification.

- source-of-truth files: `.phlo/authorization/roles.yaml` and `.phlo/authorization/policies.yaml`
- control commands: `phlo authz validate`, `phlo authz plan`, `phlo authz sync`, and `phlo authz verify`
- canonical RBAC currently supports `allow` policies only
- canonical `deny` rules are rejected by validation and backend compilation

The canonical RBAC files and commands are part of the authorisation control plane.

## Current configuration model

The core authorisation model is represented by `ApiAuthorizationConfig`. Its fields are `backend` and `mode`. `mode` accepts `optional` and `required`. An API route guard uses the configured backend when present. Required mode treats a missing backend as unavailable authorisation configuration.

Service overrides use the same authorization fields under `infrastructure.services.<name>`. The core configuration schema does not define an identity-provider user store, token issuer, tenant directory, or external ingress policy.

## Authorisation boundary

Phlo evaluates authorisation context at supported API and Observatory surfaces. Operators configure identity providers, token signing keys, secret storage, TLS, network ingress, tenant provisioning, role assignment, and service credentials.

## Where to look

The configuration model is in [Configuration](configuration.md). Package ownership and the API and Observatory surfaces are in [Package reference](packages.md).
