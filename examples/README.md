# Phlo examples

Complete lakehouse examples live in [`lakehouses/`](lakehouses/). Each project owns its environment and runtime state. Open the [documentation catalogue](../docs/guides/example-lakehouses.md) to choose by use case, or read [`lakehouses/catalog.json`](lakehouses/catalog.json) for machine-readable discovery.

For a focused runnable demonstration of bounded extraction and historical replay,
see [`data_intervals.py`](data_intervals.py) and the [data interval guide](../docs/guides/data-intervals.md).
It uses your configured development lakehouse rather than creating another project template.

Examples with a checked-in `uv.lock` require a full Phlo repository checkout.
Their locks install Phlo and its providers from relative editable source paths,
so they can run against unpublished release candidates. Keep each example in
its original directory; copying it outside the repository breaks those paths.

Run commands from the selected example directory and follow its README. Do not assume that the root lock file, service profiles, ports, or expected outputs apply to every example.
