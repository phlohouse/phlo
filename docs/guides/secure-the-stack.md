# Secure the stack

This guide keeps deployment secrets out of source control, enables API authorisation, and applies the canonical RBAC control plane to supported services.

## Before you start

- You have a deployment environment that can supply secrets and an owner for the access policy.
- You have run `phlo services init` so that the shared environment-file layout exists.
- You know whether the API should fail open for development (`optional`) or fail closed (`required`).
- You have reviewed the regulated-surface boundary in [Auth and access](../reference/auth-and-access.md).

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

The generated stack receives the values without placing them in `phlo.yaml`, workflow code, or a tracked file. Existing projects can still use the legacy `.phlo/.env.local` path, but new projects should use the shared layout.

## 2. Configure API authorisation

Set the authorisation backend and mode in `phlo.yaml`:

```yaml
api:
  authorization:
    backend: static
    mode: required
```

You can instead set `authorization` under `services.phlo-api`. The service-specific value takes precedence over top-level `api.authorization`.

`optional` permits guarded routes when no backend resolves. `required` fails closed with HTTP `503` until the configured backend is available.

| Provider | Use |
| --- | --- |
| `static` | Resolve principals and policies from the local Phlo authorisation configuration. |
| `proxy` | Trust identity headers supplied by an authenticated reverse proxy. |
| `service_token` | Authenticate service-to-service calls with a configured token. |

## 3. Choose an authentication provider

Set provider-specific values in `.phlo/secrets/.env` and keep the mode declaration in project configuration:

```bash
PHLO_AUTHENTICATION_PROVIDER=proxy
PHLO_AUTHORIZATION_BACKEND=static
PHLO_AUTHORIZATION_MODE=required
```

The API middleware resolves the authentication provider first, then evaluates authorisation for the route and tenant context. A proxy provider still requires the ingress layer to enforce authentication.

## 4. Define and validate RBAC policy

The `.phlo/authorization/` directory is the source for roles, policies, and compiled backend artifacts. Validate and preview before synchronising:

```bash
phlo authz validate
phlo authz plan
```

`validate` checks the policy files, and `plan` shows intended changes without applying them. The current CLI does not expose `authz check` or `authz explain`. Use `phlo authz verify` after synchronisation to compare backend state with the desired policy.

## 5. Regenerate and preflight

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
