# Optional Observatory preview security bundle

This directory is an opt-in deployment overlay. The Trino service definition does
not install or activate it. Do not copy it into a shared deployment until the
operator has confirmed the real prod/staging Nessie refs, provisioned TLS and
password files, and reviewed the effects of enabling Trino's file access-control
and resource-group managers.

The examples follow this repository's existing catalog convention only:

| API environment | Example catalog | Example Nessie ref |
| --- | --- | --- |
| `prod` | `iceberg_preview_prod` | `main` |
| `staging` | `iceberg_preview_staging` | `dev` |

Those are **not** asserted to be the deployed environment mapping. The API must
be configured with the exact same catalog mapping; requests fail closed if it is
absent or mismatched. Never change a ref by supplying a schema name, SQL value,
or session property.

## Install only after review

1. Render `config.properties.template` with deployment-specific HTTPS keystore
   path/password and a cryptographically random internal communication secret.
   Keep the rendered file and keystore in the deployment secret store. Trino
   authentication disables plaintext HTTP, so the service health check must
   also switch from `http://127.0.0.1:8080` to an authenticated HTTPS check that
   validates the installed CA; do not use `curl -k`.
2. Provision a separate Trino password file containing only the API preview
   identity (`phlo_api_preview`). Mount it at the path in
   `password-authenticator.properties`; do not reuse an operator account.
3. Copy `password-authenticator.properties` to
   `/etc/trino/password-authenticator.properties`, `access-control.properties`
   to `/etc/trino/access-control.properties`, and
   `resource-groups.properties` to `/etc/trino/resource-groups.properties`.
   Copy `session-property-config.properties` to
   `/etc/trino/session-property-config.properties`. Copy the access-control,
   resource-group, and session-property JSON files to `/etc/trino/preview/`,
   and the two catalog files to `/etc/trino/catalog/`.
   The `iceberg.security=READ_ONLY` connector setting and system access-control
   rules both prohibit writes through the preview identity. Verify the catalog
   refs against the actual Nessie deployment first.
4. Configure the API with the HTTPS endpoint, API identity credentials, and
   exact `prod`/`staging` -> catalog/ref mapping. The API enforces small request
   limits and cancels Trino queries when it times out, is disconnected, or
   exceeds response caps.
5. Restart the **Trino service** after installing the overlay; restart the
   **phlo-api service** after setting its endpoint/credentials/ref mapping. Do
   not run either restart from this PR.

The session-property manager fixes `query.max-scan-physical-bytes` and max
run/planning times for the preview resource group. System access control denies
that identity all session-property overrides, and the API sends none. The resource-group
`hardPhysicalDataScanLimit` is a per-quota-period admission/queueing quota, not a
strict per-query object-store or Iceberg-manifest byte ceiling. Trino does not
provide a hard kill guarantee for that quota once a query is running. The API
must retain its own timeout, cancellation, row, and response-byte limits. This
bundle does not make arbitrary ad-hoc Trino access safe, and is not active by
default.

`iceberg_preview_prod.properties` and
`iceberg_preview_staging.properties` are templates, not live environment
assertions. They mirror the package's current Nessie REST endpoint, warehouse,
and MinIO S3 settings; replace endpoint, warehouse, storage, and authentication
properties with the deployment's approved values while preserving the pinned
ref. The `prefix` values follow this repository's existing REST-catalog
convention and must be verified against the deployed Nessie REST extension.
