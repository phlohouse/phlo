# Optional Observatory preview security bundle

This directory contains an optional example for a Phlo installation. The Trino
service definition does not install or activate it. The repository does not
identify a running Trino service. An installation must configure its own
Nessie refs, storage, TLS, credentials, and policy before enabling API preview.

The examples follow this repository's existing catalog convention only:

| API environment | Example catalog | Example Nessie ref |
| --- | --- | --- |
| `prod` | `iceberg_preview_prod` | `main` |
| `staging` | `iceberg_preview_staging` | `dev` |

These catalog names and refs are examples, not required API names or a deployed
mapping. Replace them consistently in the catalog files, access rules, and API
settings for each installation. The API uses separate fixed identities,
`phlo_api_preview_prod` and `phlo_api_preview_staging`; each can read only its
matching catalog. Configure their separate passwords in the API environment.
The API requires two distinct catalog names and an exact match to its
configured environment refs. Never select a ref with SQL or a session property.

## Install only after review

1. Review the installation's existing Trino configuration and clients. Merge
   the HTTPS and password-authentication settings from `config.properties.template`
   into its existing config. Do not replace other settings such as dynamic catalog
   management or existing client access. Supply a keystore password and random
   internal communication secret through the installation's secret store. Update
   the health check to authenticated HTTPS with a trusted CA; do not use `curl -k`.
2. Provision the two dedicated identities shown in the example, with different
   strong passwords. If other clients use the same Trino service, retain their
   authentication paths and credentials. Do not reuse an operator account for
   previews.
3. Integrate the sample password authenticator, access-control, resource-group,
   and session-property policies with the installation's existing policies.
   Trino uses one configuration manager of each type; copying these sample
   files over existing ones can block other clients. Adapt catalog names and
   the matching user-specific catalog rules together; do not grant either
   preview user access to the other environment. The preview catalogs set
   `iceberg.security=READ_ONLY`. Confirm their Nessie refs and storage mapping.
4. Configure the API with the HTTPS endpoint, the two API identity passwords, and
   exact `prod`/`staging` -> catalog/ref mapping. The API enforces small request
   limits and cancels Trino queries when it times out, is disconnected, or
   exceeds response caps.
5. Apply configuration and restart services through the installation's own
   deployment process. This PR starts and restarts no services.

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
