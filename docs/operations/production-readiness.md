# Production Readiness

Use this checklist before treating a Phlo stack as production-capable. The
`phlo services preflight` command makes the locally inspectable part of this
contract executable and machine-readable (ADR 0047).

## The preflight command

```bash
phlo services preflight --production          # human table
phlo services preflight --production --json   # stable JSON report
phlo services preflight --production --json --output .phlo/preflight.json
```

- `--production` evaluates the production posture. It defaults from the
  generated `.phlo/.env` (`PHLO_ENVIRONMENT`); pass it explicitly to evaluate
  production on a development project.
- `--json` emits the stable report to stdout.
- `--output PATH` persists the same JSON envelope atomically at mode `0600`.
- The command is read-only apart from that explicit output file. It never
  contacts a container backend and never mutates configuration or policy.
- A failed or unavailable required check exits non-zero.

`phlo services start` runs the same evaluator automatically when the effective
environment is `production`, and fails **before** any container-backend contact
if any required check is `failed` or `unavailable`.

## Report contract

The report is versioned (`schema_version: "1"`) and JSON-serializable:

```json
{
  "schema_version": "1",
  "environment": "production",
  "generated_at": "...",
  "passed": false,
  "services": ["postgres", "minio"],
  "checks": [
    {"id": "env.production", "state": "passed", "message": "...",
     "remediation": "...", "source": "...", "details": {}}
  ]
}
```

Each check has a stable `id`, a state from the closed set `passed`, `failed`,
`unavailable`, `not_applicable`, a sanitized `message`, a `remediation`, a
`source`, and non-secret `details`. Messages and details never contain DSNs,
tokens, secret values, private keys, or complete environment dumps. A report is
point-in-time operational evidence, not an RPO/RTO, HA, compliance, or
release-acceptance claim.

For production-required checks, both `failed` and `unavailable` fail the report.
`unavailable` means the required evidence cannot be obtained yet; a check is
never optimistically passed.

## Check ownership

| Check ID | What Phlo verifies | Owner |
| --- | --- | --- |
| `env.production` | Effective environment is production | core (config) |
| `compose.non_dev` | Generated compose is not in dev mode | core (compose header) |
| `http.authorization_required` | No development auth bypass in production; enforcement posture | core (enforcement) |
| `authn.provider` | A verified JWT provider is fully configured | core (config) |
| `authz.backend` | An authorization backend is configured and registered | core (config) |
| `tls.external_endpoint` | TLS termination is represented in the generated stack | core (compose) |
| `oidc.issuer_audience_jwks` | Issuer, audience, and verification material are configured | core (config) |
| `identity.workload.*` | Distinct per-workload identities (API, orchestration, query, catalog, maintenance) | Plans 004–005 |
| `audit.key_backend` | Audit and signature HMAC keys are configured | core (config) |
| `policy.compiled_verification` | Compiled RBAC policy loads; backend drift verification | core local + provider adapters |
| `secrets.no_bundled_shared` | No default, empty, or shared production credentials | core (config) |
| `secrets.env_local_0600` | `.phlo/.env.local` owner and mode are `0600` | core (filesystem) |
| `network.protected_ports` | Protected backends expose no host ports | core (compose) |

Workload-identity and backend enforcement/audit/drift checks are reported as
`unavailable` until Plans 004–005 add their contributors; they never optimistically
pass.

## Operator checklist

The preflight covers the locally inspectable contract. These remain operator or
external-system responsibilities:

- TLS is terminated at the edge and its termination point is recorded in the
  generated stack.
- The OIDC IdP is reachable and its JWKS is valid; tokens are issued with the
  declared audience.
- Backend-native authorization and audit facilities are enabled and retained
  outside container-local logs.
- Workload identities are distinct even if one IdP issues them; credentials are
  rotated and never shared.
- Backup, restore, upgrade, and recovery runbooks exist and have been drilled.
- A passing preflight is point-in-time evidence; it is not an ongoing security
  certification.

## Related Pages

- [Security](../setup/security.md)
- [Audit Logging](audit-logging.md)
- [Deployment Profiles](../guides/deployment-profiles.md)
- [Operations Guide](operations-guide.md)
