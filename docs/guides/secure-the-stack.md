# Secure the stack

This guide keeps deployment secrets out of source control, enables API authorisation, and applies the canonical RBAC control plane to supported services.

## Before you start

- You have a deployment environment that can supply secrets and an owner for the access policy.
- You have run `phlo services init` so that the shared environment-file layout exists.
- You know whether the API should fail open for development (`optional`) or fail closed (`required`).
- You have reviewed the regulated-surface boundary in [Auth and access](../reference/auth-and-access.md).

`phlo services init` publishes host ports on `127.0.0.1` by default. To expose
them on all interfaces, regenerate with `phlo services init --publish-all-interfaces`
and protect every exposed service with appropriate network controls and authentication.
This flag affects host publishing only; services continue to listen on their
container networks. Reapply the flag when regenerating with `services init`.

## 1. Keep secrets in local configuration

Put credentials in `.phlo/secrets/.env`, which is the highest-precedence project environment file and should be mode `0600`:

```bash
umask 077
cat >> .phlo/secrets/.env <<'EOF'
POSTGRES_PASSWORD=replace-me
MINIO_ROOT_PASSWORD=replace-me
PHLO_ICEBERG_S3_SECRET_KEY=replace-me
EOF
chmod 600 .phlo/secrets/.env
```

The generated stack receives the values without placing them in `phlo.yaml`, workflow code, or a tracked file.
Legacy Dagster `.phlo/.env` and `.phlo/.env.local` attachments were removed in 0.19.0.
Generating services for a project that still has either file fails and names the migration.
Run `phlo services migrate --dry-run` to preview the move, then run
`phlo services migrate`. Defaults move to `.phlo/overrides/.env` and secrets
move to `.phlo/secrets/.env`. The old files no longer supply configuration
after migration.
The [removal schedule](../reference/python-api.md#removal-schedule) lists the removed compatibility paths and their replacements.

## 2. Configure API authorisation

Set the authorisation backend and mode in `phlo.yaml`:

```yaml
api:
  authorization:
    backend: default
    mode: required
```

You can instead set `authorization` under `services.phlo-api`. The service-specific value takes precedence over top-level `api.authorization`.

`optional` permits guarded routes when no backend resolves. `required` fails closed with HTTP `503` until the configured backend is available.

The `default` authorisation backend evaluates the canonical project role
assignments and policies. Authentication providers are separate from this backend.

## 3. Choose an authentication provider

Set provider-specific values in `.phlo/secrets/.env` and keep the mode declaration in project configuration:

```bash
PHLO_AUTHENTICATION_PROVIDER=jwt
PHLO_AUTH_JWT_ISSUER=https://identity.example.com/realms/phlo
PHLO_AUTH_JWT_AUDIENCE=phlo-observatory
PHLO_AUTH_JWT_JWKS_URL=https://identity.example.com/realms/phlo/protocol/openid-connect/certs
PHLO_AUTHORIZATION_MODE=required
```

Set `authentication.provider: jwt` in `phlo.yaml` too, or omit the YAML provider
when the deployment supplies it through the environment. Nonempty provider
selections must agree; conflicts fail startup. For a private issuer CA, mount its
certificate and set `PHLO_AUTH_JWT_CA_FILE` to the container path. Configure the
same issuer, client, and trusted CA on oauth2-proxy.

The API verifies authentication before authorising the route and environment.
Use the `jwt` provider for forwarded OIDC bearer tokens. The `static` provider
and the signed-header `proxy` provider have different credential contracts;
neither is a substitute for JWT verification in this flow.

## 4. Authenticate Observatory

Protect Observatory with your authenticated ingress. Its server-side API client forwards
the incoming `Authorization: Bearer <credential>` header to phlo-api. If that header
is absent or is not a bearer header, the client forwards `X-Auth-Request-Access-Token`
as a bearer token instead. phlo-api independently verifies that credential and
authorises each operation against the canonical policy and selected environment.

Configure `PHLO_API_URL` on the Observatory server. Keep the API behind the same
trusted network boundary and configure its authentication provider to verify the
forwarded tokens, including issuer and audience where applicable. Strip
caller-supplied proxy headers at ingress before setting trusted headers.

The generated `proxy` profile routes `observatory.<TRAEFIK_DOMAIN>` through the
same oauth2-proxy forward-auth middleware as the API. Its `/oauth2/` paths go to
oauth2-proxy, not the UI. For browser sign-in on this host, configure the issuer's
registered redirect URI and `OAUTH2_PROXY_REDIRECT_URL` to
`https://observatory.<TRAEFIK_DOMAIN>/oauth2/callback`. The existing API-host
callback remains the default for API-only deployments. Cross-host cookie sharing
is not automatic. Configure it explicitly only for trusted sibling hosts.

Terminate HTTPS at the trusted ingress and install the issuer's CA where the
proxy and API require it. Do not disable TLS verification. The generated
production profile removes direct API and UI host-port mappings while retaining
their authenticated ingress routes. Review resolved Compose overrides too;
publishing a separate UI port would bypass the browser session gate.

Ordinary `phlo services start` honours the saved provider and enabled services.
Without a caller session it reports infrastructure startup and
`verification_required`, not authenticated dashboard readiness. Sign in through
the configured ingress and inspect `/api/v1/readiness?env=<configured-id>`.
All orchestration and incident checks must be `ready`; disabled query preview
does not block the dashboard. A local authenticated deployment with loopback
API/UI ports can use its existing `PHLO_AUTHZ_TOKEN` for the same non-mutating
checks on startup. The token is never saved or forwarded across redirects.
Protected external deployments require verification through their real ingress
session, not a development identity.

There is no shared Observatory token or browser `authToken` setup. Do not put
service credentials in browser code. An unauthenticated API response remains
HTTP `401`, and an authenticated policy denial remains HTTP `403`.

For a loopback-only development identity and a single configured target, follow
[Set up Observatory locally](set-up-observatory-locally.md). Do not use that
development profile for a network-accessible deployment.

## 5. Define and validate RBAC policy

The `.phlo/authorization/` directory is the source for roles, policies, and compiled backend artifacts. Validate and preview before synchronising:

```bash
phlo authz validate
phlo authz plan
```

`validate` checks the policy files, and `plan` shows intended changes without applying them. The current CLI does not expose `authz check` or `authz explain`. Use `phlo authz verify` after synchronisation to compare backend state with the desired policy.

## 6. Regenerate and preflight

Render the service settings, then run the production checks without contacting Docker:

```bash
phlo services init
phlo services preflight --production
phlo doctor
```

Preflight reports authorisation, authentication, secret-file, and protected-port checks. Doctor then checks project discovery and live service health.

## Support boundary

Phlo records request-time authentication and authorisation evidence for its governed surfaces, but it does not secure arbitrary direct ports or implement row-level policies inside Superset. Operators still own TLS termination, identity-provider availability, backend-native grants, credential rotation, log retention, and network controls.

## Verify

```bash
stat -c '%a' .phlo/secrets/.env
phlo authz verify
```

The file mode is `600`, and verification reports whether backend state matches the desired policy.

## Related

- [Run in production](run-in-production.md) for deployment preflight and operations.
- [Monitor and debug](monitor-and-debug.md) for auth and service failures.
- [Auth and access](../reference/auth-and-access.md) for the decorator and policy model.
