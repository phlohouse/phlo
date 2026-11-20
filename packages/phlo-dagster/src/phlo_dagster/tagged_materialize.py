"""Run ``dagster asset materialize`` with extra run tags.

``phlo materialize`` executes the Dagster CLI inside the code-location
container. That CLI accepts no run tags, so its runs carried no ``phlo/ref``
and phlo-api, which attributes runs to environments by that tag, never showed
them in Observatory. This entry point adds the tags to the run the CLI starts.

Usage: ``python -m phlo_dagster.tagged_materialize KEY=VALUE [...] -- <dagster asset materialize args>``
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from typing import Any


def _split(argv: Sequence[str]) -> tuple[dict[str, str], list[str]]:
    if "--" not in argv:
        raise SystemExit("usage: tagged_materialize KEY=VALUE [...] -- <materialize args>")
    index = list(argv).index("--")
    tags: dict[str, str] = {}
    for item in argv[:index]:
        key, sep, value = item.partition("=")
        if not sep or not key or not value:
            raise SystemExit(f"invalid run tag {item!r}; expected KEY=VALUE")
        tags[key] = value
    return tags, list(argv[index + 1 :])


def main(argv: Sequence[str] | None = None) -> int:
    """Start the Dagster materialize command with ``tags`` merged into its run."""
    tags, rest = _split(sys.argv[1:] if argv is None else argv)

    import dagster._cli.asset as asset_cli
    from dagster._cli import main as dagster_main

    execute_job = asset_cli.execute_job
    extra = tags

    def tagged_execute_job(*args: Any, tags: dict[str, str] | None = None, **kwargs: Any) -> Any:
        # Tags the CLI computed itself, such as the partition, win.
        return execute_job(*args, tags={**extra, **(tags or {})}, **kwargs)

    # The Dagster CLI calls the module-level name, so replace it there.
    setattr(asset_cli, "execute_job", tagged_execute_job)  # noqa: B010
    sys.argv = ["dagster", "asset", "materialize", *rest]
    result = dagster_main()
    return int(result or 0)


if __name__ == "__main__":
    raise SystemExit(main())
