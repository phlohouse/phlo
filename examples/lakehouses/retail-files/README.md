# Retail Files blueprint

The Retail Files lakehouse is distributed as the **`phlo-retail-files`** blueprint
package. The canonical project lives inside the package; this directory is a
thin pointer and is not an executable source. The package is a project template,
its phlo-family dependencies use exact released versions, and its resources must
match the recorded digest. The wheel is tested in a clean environment.

## Use the blueprint

```bash
# Route 1: direct install
uv pip install phlo-retail-files

# Route 2: bundled extra
uv pip install "phlo[blueprints]"

phlo init my-project --template retail-files
```

The generated project is network-independent: deterministic fixtures, local
Iceberg/Trino/Nessie services, sequential WAP, and an intentional failure mode
that leaves published data unchanged. See the generated project's README and
`docs/retail-files-e2e.md` for the verified table counts and run evidence.

## Development

The package source lives in `src/phlo_retail_files/`:

- `provider.py` — the `phlo.project_templates` entry-point callable and renderer.
- `contract.py` — loads `blueprint_contract.json` and recomputes the resource digest.
- `resources/retail_files/` — the canonical project shipped as package resources.
- `blueprint_contract.json` — static contract: pins, allowlist, starter facts, digest.

Build and verify locally:

```bash
uv build
uv run --with pytest pytest <repository>/tests/cli/test_blueprint_retail_files.py
```
