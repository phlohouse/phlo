# Develop with native Dagster mode

Use `phlo dev` when you want Dagster to run on the host instead of inside the Compose Dagster container. Storage, catalog, query, and other service dependencies still need their configured services.

## Before you start

Run the command from a Phlo project that has `pyproject.toml` and a workflows directory. Install the project dependencies in the project environment, including `phlo-dagster`.

## 1. Start the host Dagster server

Start the development server with the host, port, and workflows path you need.

```bash
phlo dev --host 127.0.0.1 --port 10006 --workflows-path workflows
```

The command starts the Dagster development server for your workflows. Its options are `--host`, `--port`, and `--workflows-path`.

## 2. Start native service processes when supported

`NativeProcessManager` starts only services whose manifest has a `dev.command`. The current service manifests provide native commands for `phlo-api` and Observatory. Use `phlo services start --native` when you want those service processes on the host.

```bash
phlo services start --native
```

## 3. Keep service dependencies available

Start the storage, catalog, query, and object-store services that your workflows require. `phlo dev` changes where Dagster runs, but it does not replace PostgreSQL, MinIO, Nessie, Trino, or other services without a native `dev.command`.

```bash
phlo services start
```

Use `phlo services status` to check the generated service state before launching a run.

## 4. Run assets through Dagster

Use the normal Dagster-backed commands after the host server is ready.

```bash
phlo materialize <asset-name> --partition <YYYY-MM-DD>
```

Provider command groups remain inspection and debugging paths. They do not create the Dagster run record, lineage, or asset-check results produced by the Dagster-backed path.

## Common failures

If `pyproject.toml` is not found, run `phlo dev` from the project root. If the Dagster command fails, the CLI exits and reports the failure instead of leaving a native server running.

## Verify

Open `http://localhost:10006` or the host and port you selected. Confirm that the discovered assets appear in the Dagster asset graph before launching a materialisation.

## Related

- [How Phlo works](../concepts/how-phlo-works.md)
- [Run in production](run-in-production.md)
- [Configuration](../reference/configuration.md)
