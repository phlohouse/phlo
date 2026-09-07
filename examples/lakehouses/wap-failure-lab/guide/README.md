# Annotated WAP end-to-end guide

Open [index.html](index.html) in a browser. The guide runs locally without a server or network connection and includes the Mermaid renderer and recorded WAP JSON files.

The scrolling guide explains inputs, outputs, checks, failures, publication and recovery with actual fixture rows and saved run evidence from 7 September 2026. Implementation links are pinned to the tested commit `ee7e65851e5bc80b8520c2c054e9eb0db1bccc99` (PR #920). These are historical examples, not a live status view.

## Files

- `index.html`: the complete guide, explanatory text and embedded diagram definition.
- `workflow.mmd`: editable source for the continuous Mermaid workflow. Keep the embedded `diagram` constant in `index.html` in sync when editing it.
- `records/`: original WAP report JSON and the before/after failed-merge regression record.
- `mermaid.min.js`: pinned Mermaid 11.4.1 browser bundle, gzip-packed for the repository file-size limit, with bundled dependency notices retained. Uses the modern browser `DecompressionStream` API.

The guide retains the test boundaries: host Dagster, containerized backend services and a separate dbt invocation. It does not claim full production acceptance or unified ingestion/transformation evidence.
