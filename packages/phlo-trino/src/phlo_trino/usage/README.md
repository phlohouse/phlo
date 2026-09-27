# Optional observed query usage

This bundle is not activated by `service.yaml`. It targets Trino 483 with
`catalog.management=dynamic`. It does not change the installation's existing
Iceberg REST catalogs or route existing clients to new catalogs.

To enable observed usage for selected clients:

1. Apply `packages/phlo-api/src/phlo_api/sql/002_query_usage.sql` explicitly to
   the installation's own `PHLO_RUN_EVIDENCE_DB_URL` PostgreSQL database.
   Do not apply it to another installation.
2. Create direct Nessie Iceberg catalogs for the selected environments. The
   two `catalog/` files are examples. Replace their refs, Nessie URI, warehouse,
   storage, and catalog names for this installation. Keep an explicit literal
   `iceberg.nessie-catalog.ref`; do not use Nessie REST `prefix`, a default ref,
   or a property substituted from the process environment. Trino 483 includes
   the catalog's raw properties in each query input's `catalogVersion`.
3. Set `PHLO_V1_USAGE_TRINO_SOURCES` on phlo-api. Its JSON object maps a unique
   Trino source ID to `service_subject` and `catalogs`, with exactly `prod` and
   `staging`. Each catalog contains its name and the **complete raw property
   map**, including `connector.name`, from the matching `.properties` file.
   Its explicit Nessie ref must match `PHLO_V1_ENVIRONMENTS` for that
   environment. Omit catalog credentials: this feature refuses configurations
   with secret-bearing property keys or substitutions. The API computes the
   Trino 483 property hash and accepts only a matching query-selected version.
   This proves the named ref, not a fixed commit; branch heads can advance.
4. Give the HTTP listener its own service credential through the installation's
   credential store. Authorize only that identity for `service.manage` on
   `source_id=<source-id>`. Install the listener template with the API's HTTPS
   URI and token; register its file in Trino's `event-listener.config-files`.
   Protect the file because its static header holds the token. Enable completed
   events only. Follow the installation's change process before any restart.
5. Point clients whose usage you want to observe at the direct Nessie catalogs.
   A query against a different catalog has no verified ref and is not attributed.

`POST /api/v1/trino/query-completed?source_id=...` authenticates and authorizes
the service principal, checks its configured subject, and accepts only Trino
483 events. It limits the body to 256 KiB and the input list to 100. It stores
only source ID, query ID, successful query-input table identities, exact Nessie
ref, timestamp, and a digest for retry conflicts in the installation's
PostgreSQL database. It discards SQL, user, session, connector details, and the
raw event. Failed queries are not counted as completed table reads. The
`/assets/{asset_id}/query-usage` read checks the selected Dagster asset and
environment/ref, limits pages to 500, and shows retained observations from
the last 30 days. The asset definition must declare a `schema.table` physical
relation in text metadata under `phlo/relation` or the existing `target_table`
key. Conflicting declarations are rejected; the asset key is never treated as
a table name. The response names the declared table. It associates observed
inputs with the **current** asset declaration and cannot prove that a past
version of the asset referred to the same table. Without a declaration it
returns `unavailable`, not a fabricated match. The API removes older rows on
the next successful ingest.

This is **observed partial usage**. HTTP delivery has no durable Trino spool;
even with retries, listener outages can lose events. Query inputs show tables
in a completed plan, not how many rows a client saw. Direct storage access,
REST catalogs, unregistered direct catalogs, dynamic WAP catalogs without a
registered version, failed queries, and other query engines are not covered.
The existing `/assets/{asset_id}/usage` still reports only successful API
preview reads from its separate operation journal. The two routes have
independent sources, retention, and cursors. A qualifying preview can appear
in both routes, so do not sum their counts to estimate total access.
Neither route is the phase-6 compliance audit chain.
