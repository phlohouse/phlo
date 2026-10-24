# Lakehouse presets

A preset is a **complete, independently runnable lakehouse**: sources,
contracts, transforms, analysis, a report, fixtures, tests and a proof. You
copy one when you start a new domain, then replace its domain pack.

Presets are deliberately not `phlo init` templates and are not wired into the
Phlo CLI. A template writes a skeleton and leaves you to fill it in. A preset
already works end to end, and you learn the pattern by reading it.

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
4. Add a row to the table above. Do not add it to `examples/lakehouses/`; that
   catalogue is for platform fixtures, and its tests require every directory
   there to be an example.

## Why a separate folder

`examples/lakehouses/` proves platform features and is exercised by the
repository's own tests. A preset is a starting point for someone else's
project: smaller, self-contained, and written to be read and edited. Keeping
the two apart makes each honest about what it is.

When a preset has proved itself across more than one real domain, it can
become a blueprint package (the `phlo.project_templates` entry point) without
changing the preset itself. Until then, copying a folder is the whole
mechanism, and that is intentional.
