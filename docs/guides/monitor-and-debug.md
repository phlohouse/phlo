# Monitor and debug

This guide uses Phlo's health, status, log, lineage, and metrics commands to narrow a failed run to a service, partition, validation, or catalog problem.

## Before you start

- You have a project with `.phlo/` initialised and permission to read service logs.
- The local stack is running, or you have the deployment's Phlo CLI context.
- You know the asset and partition that failed.

## 1. Check project and service health

Start with the diagnostic commands before reading individual logs:

```bash
phlo doctor
phlo status
phlo services status
```

`doctor` summarises environment and live checks, `status` reports assets and runs, and `services status` prints Docker service names, health, and ports. A healthy tutorial stack reports `Summary: 14 ok, 0 warnings, 0 failures, 0 skipped`.

## 2. Read the right log stream

Inspect command options, then select a bounded time window:

```bash
phlo logs --help
phlo logs --lines 100 --timestamps
phlo services logs --help
phlo services logs trino --lines 100
```

`phlo logs` reads the Phlo log backend, while `phlo services logs` reads infrastructure container output when that subcommand is available in the installed service plugin.

| Option | Effect |
| --- | --- |
| `--lines N` | Limit the number of log lines. |
| `--since VALUE` | Show entries after a relative or absolute time. |
| `--until VALUE` | Stop at a time boundary. |
| `--timestamps` | Include timestamps in the output. |
| `--backend NAME` | Select a configured log backend. |

The file backend writes daily files under `.phlo/logs/`, using the configured `PHLO_LOG_FILE_TEMPLATE` path.

## 3. Inspect lineage, metrics, and the catalog

Use metadata commands to establish whether the failure occurred before or after a table commit:

```bash
phlo lineage dlt_events
phlo metrics
phlo catalog tables
phlo catalog history raw.events
```

Lineage shows upstream and downstream assets, metrics summarises platform measurements, and catalog history proves whether a successful snapshot was committed.

## 4. Reproduce one partition

Materialise the failed asset with its exact partition instead of selecting every asset:

```bash
phlo materialize dlt_events --partition 2025-01-15
```

The streamed log identifies the first failing step. A successful retry ends with `Successfully materialized dlt_events` and adds a catalog snapshot.

## 5. Open Observatory when enabled

Observatory is the optional browser surface for asset health, lineage, table previews, quality evidence, and service status. Enable it and regenerate the stack:

```yaml
infrastructure:
  services:
    observatory:
      enabled: true
```

```bash
phlo services init
phlo services start --service observatory
```

The UI is exposed on port `3001` when the package's default mapping is used. `phlo services ports` is authoritative for a customised deployment.

| Failure | First check | Fix |
| --- | --- | --- |
| Service unhealthy | `phlo services status` and service logs. | Restart the named service, then inspect its dependency and port mapping. |
| Partition rejected | Materialisation output and partition format. | Use `--partition YYYY-MM-DD` and confirm the asset declares compatible partitions. |
| Validation failure | Dagster asset checks and validation log. | Correct the source rows or schema constraint before retrying. |
| Catalog not found | `phlo catalog tables` and Nessie/Trino logs. | Start the catalog services and use `phlo trino --catalog iceberg`. |

## Verify

```bash
phlo doctor
phlo metrics
```

The diagnostic summary has no failures, and metrics returns a response rather than an unavailable-backend message.

## Related

- [Test a project](test-a-project.md) for repeatable workflow checks.
- [Add quality checks](add-quality-checks.md) for validation failures.
- [Expose data](expose-data.md) for Observatory and API surfaces.
