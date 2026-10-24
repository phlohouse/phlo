# Lakehouse presets

A preset is a **complete, independently runnable lakehouse**: sources,
contracts, transforms, analysis, a report, fixtures, tests and a proof. You
copy one when you start a new domain, then replace its domain pack.

## Catalogue

| Preset | Shape | Runs on | What it proves |
| --- | --- | --- | --- |
| [source-to-report](source-to-report/) | Source → evidence → insight → analysis → report | DuckDB with no services; the same assets and dbt on Trino/Iceberg | A published figure can be checked against the measurement behind it, and two builds are byte-identical. |

Each preset owns its environment, lock file, fixtures and commands. Run
everything from the preset directory.

## Add a preset

1. Copy an existing preset and rename its Python package and `phlo.yaml` name.
2. Follow the preset's `DOMAIN.md`: vocabulary, sources, contracts, ingestion,
   dbt, extraction, analysis, report, fixtures, verification.
3. Keep the proof: a deterministic pull, a manifest of independent expected
   values, and a `verify` script that builds twice.
4. Add a row to the table above. Keep presets out of `examples/lakehouses/`:
   that catalogue is for platform fixtures, and its test requires every
   directory there to be an example.
