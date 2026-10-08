# Export a complete set of files

This example publishes `catalog.csv` and `summary.json` as one export asset, then reads both through one manifest.

## Before you start

- Install `phlo` and `phlo-dagster` in your Python environment.
- Run the commands from your project directory. In this repository, prefix Python commands with `uv run --locked`.
- Choose a local destination that every worker and consumer can access at the same absolute path. A container-local directory is not shared storage.

## 1. Write the export

Create the `workflows` directory if it does not exist.
Save this file as `workflows/sky_survey.py`:

```python
import csv
import json

import phlo
from phlo.exports import ExportContext


@phlo.export(
	name="monthly_sky_survey",
	destination=".phlo/exports",
	outputs={"catalog": "catalog.csv", "summary": "summary.json"},
	max_retries=2,
)
def export_sky_survey(context: ExportContext) -> None:
	stars = [
		{"name": "Sirius", "ra": 101.287},
		{"name": "Vega", "ra": 279.235},
	]
	with (context.staging_dir / "catalog.csv").open(
		"w", encoding="utf-8", newline=""
	) as handle:
		writer = csv.DictWriter(handle, fieldnames=["name", "ra"])
		writer.writeheader()
		writer.writerows(stars)
	(context.staging_dir / "summary.json").write_text(
		json.dumps({"count": len(stars), "run_id": context.run_id}),
		encoding="utf-8",
	)
```

The example uses fixed observations and needs no database. For a real source, declare its asset key in `depends_on` and select data using `context.runtime.routing` and `context.runtime.resources`. Record available snapshot or version references in `context.upstream_versions` under the declared dependency key at the time you read the data. Leave unavailable versions absent. Phlo does not infer versions from a later "latest" lookup.

Close every output before the writer returns. Create parent directories for nested output paths inside `context.staging_dir`.

## 2. Discover and run the asset

Save the following as `run_export.py` and run `python run_export.py`:

```python
from pathlib import Path

import dagster as dg
from phlo_dagster.framework.discovery import discover_user_workflows

from phlo.exports import resolve_export_manifest
from phlo.helpers.artifacts import verify_manifest_checksums


definitions = discover_user_workflows("workflows", clear_registries=True)
assets = [
	asset for asset in definitions.assets
	if isinstance(asset, dg.AssetsDefinition)
	and dg.AssetKey("monthly_sky_survey") in asset.keys
]
result = dg.materialize(assets)
assert result.success

manifest = resolve_export_manifest(".phlo/exports", "monthly_sky_survey")
assert all(verify_manifest_checksums(manifest).values())
print("Published", manifest.metadata["run_id"])
for artifact in manifest.artifacts:
	print(artifact.metadata["name"], artifact.size_bytes, artifact.checksum)
	print(Path(artifact.uri).read_text(encoding="utf-8"))
```

The output includes `Published`, the run ID, both artifact names, SHA-256 checksums, and their file contents. The summary has `count` equal to `2`.

The same workflow loader powers project discovery. The export registers an executable `AssetSpec` with a `RunSpec`, not a deprecated flow declaration. Dagster shows one materialisation with the named artifact manifest and immutable manifest path in its metadata.

## 3. Read one complete set

Call `resolve_export_manifest` once per consumer operation, as the script does. Read every artifact using the paths in that returned manifest. Do not resolve current separately for each file, because another run can publish between reads.

If a writer raises or omits an output, inspect the orchestration failure and fix the writer. The previous complete set remains current. If no run has succeeded, resolving current raises `FileNotFoundError`.

For storage guarantees, retry identity, and all decorator parameters, see the [file export API reference](../reference/python-api.md#phloexport).
