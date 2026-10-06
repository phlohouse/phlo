# phlo-observatory-example

This package demonstrates a Phlo Observatory extension with a route, a
navigation item, dashboard slots, a settings panel, and a bundled JavaScript
asset. Phlo discovers the extension through the
`phlo.plugins.observatory` entry point when the package is installed.

## Install the extension

Install the package in the Phlo environment so plugin discovery can read its
manifest:

```sh
pip install phlo-observatory-example
```

The current Observatory UI does not load extension manifests. The package
demonstrates the plugin contract and asset packaging; installing it does not
add a visible route or navigation item.

To develop against this repository, install the package in editable mode from
the repository root:

```sh
uv pip install --python .venv/bin/python -e packages/phlo-observatory-example
```

## Verify the package

From the repository root, run its smoke tests:

```sh
uv run --locked pytest packages/phlo-observatory-example/tests/test_smoke.py
```

The tests check the extension manifest and confirm that the packaged
`example.js` asset is present.
