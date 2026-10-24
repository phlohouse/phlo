# Observatory acceptance with an independent lakehouse

On 23 October 2026, Observatory was exercised against a second real local
lakehouse based on `examples/lakehouses/iot-telemetry`. This was a separate
deployment, not renamed Mission Control records or an HTTP fixture server.

## Configuration and independence

The project used environment `research`, primary Nessie reference `release_test`,
and Compose project `independent-observatory`. It had its own PostgreSQL database,
Nessie catalog, MinIO storage, Trino, Dagster, authorization policies and API.
Observatory was served on port 3011 and the API on 4010. Preview used a separate
authenticated TLS coordinator on 10125 with newly generated credentials and CA.
No Mission Control data, state volumes, credentials, policies or saved entities
were copied into it.

Python imports were inspected in the running service. Phlo, API, Dagster, DLT,
Nessie and Trino all resolved under the current checkout mounted at
`/opt/phlo-dev`. Existing container dependency layers were reused; installed
PyPI Phlo code was not the execution source. Observatory used the locally built
production bundle from this branch.

The independent project explicitly selected its providers:

```yaml
capabilities:
  defaults:
    catalog: nessie
    object_store: minio
    table_store: iceberg
    query_engine: trino
environments:
  research:
    dagster_location: phlo_dagster.framework.definitions
    nessie_ref: release_test
    compose_project: independent-observatory
default_environment: research
```

Explicit provider selection matters when a development image contains multiple
catalog or object-store providers. Container clients use internal service ports;
the API's writable Trino connection used `trino:8080`, independently of host port
10105. Preview remained a separate read-only TLS connection.

## Real operations exercised

The IoT example's deterministic generator supplied device/site registry data,
compressed hourly telemetry and late corrections. The four source assets were
launched through the current Phlo CLI with `--environment research` and partition
`2026-08-20`. Each run succeeded and its verified WAP report recorded promotion
to `release_test`.

| Operation | Observed result |
| --- | --- |
| Environment discovery | The default resolved to `research`; requests for unconfigured `sandbox` and `prod` were rejected |
| Discovery and navigation | All 11 primary Observatory destinations loaded against the real API; the original example declared 11 assets and five jobs |
| Ingestion and WAP history | Four successful source runs and their verified promoted lifecycle reports appeared under the custom target |
| Real preview SQL | Device count was 8; telemetry count was 533, checked against the original compressed source files |
| Source and transform inventory | Independent device/telemetry ownership, contracts, source names and dbt models were discovered without Mission Control identifiers |
| Draft product | A new `fleet_registry` collection referenced `raw.device_registry`; it remained a draft without sharing or publication |
| Draft metric SQL | Device counts grouped by site were 3, 3 and 2; generated SQL ran against the independent preview catalog |
| Branch comparison | `research-inspection` was created from `release_test`; its actual comparison graph named the configured target |
| Reviewed CSV candidate | A new three-row sensor calibration CSV was retained, reviewed, loaded and previewed on `research-upload-…`; `release_test` remained unchanged |
| Empty state isolation | Initial incidents, products and metrics were empty; no Mission Control records appeared |

Adding the reviewed upload registered one additional source asset. Inventory
totals therefore changed during acceptance. They are observations, not constants
that Observatory assumes for a project.

## Defects reproduced and fixed

- Candidate environment validation and Trino candidate ref validation only
  accepted `sandbox`, `staging` or `prod`. Both now use the canonical environment
  identifier contract while preserving exact candidate operation ownership.
- Branch rendering and merge-reason policy assumed `main`. They now use the
  configured comparison/publication target. Candidate copy names its actual base.
- Run reads filtered solely on the physical `phlo/ref`. WAP runs write on an
  isolated ref, so successful promoted runs disappeared from target history.
  Current v2 reports and immutable launch manifests now correlate those runs to
  their configured target. Digest, run, launch identity, tags,
  environment and code-location checks remain mandatory. Staged materializations
  become target freshness evidence only after verified promotion.
  Promoted audit evaluations use the same correlation; unpromoted evaluations
  cannot replace the released target's verdicts.
- Global WAP gating also rejected reviewed CSV candidates, whose immutable
  contract intentionally writes to a separate branch without promotion. A
  source-owned `RunSpec.isolated_write` contract now carries the required ref and
  actor. The adapter rejects mismatches, WAP tags and every configured primary
  ref. Caller tags alone cannot create an exemption, and ordinary assets retain
  the WAP-manifest gate. DLT still checks identity before reading private bytes.

Initial direct GraphQL launches lacked Phlo's launch manifest and failed. Those
attempts were retained, not relabelled successful. A candidate attempt blocked by
the global WAP gate was also retained; its explicit retry required a new plan,
verification that no candidate table had been written, and confirmation.

## Scope of the evidence

This establishes compatibility with another source-powered Nessie/Iceberg/Trino
lakehouse, custom environment names, a custom primary ref and enabled WAP. It
does not qualify every backend or production authentication topology. The dbt
models were discovered but the complete downstream dbt pipeline was not built.
Unexecuted models and checks retain unknown or unevaluated observations.

Runtime volumes, private TLS material, authorization files and machine-specific
acceptance scripts remain outside the repository. The original local acceptance
workspace retains API responses, exact run/snapshot identities and browser
captures. Regression tests in the API, Dagster, DLT, Trino and Observatory suites
cover the defects described above.
