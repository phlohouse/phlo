"""Shared exception-chain utilities.

Walks an exception's __cause__/__context__ chain root-first so callers can
classify Trino failures by the originating error rather than the wrapper.
"""

from __future__ import annotations

from collections.abc import Iterable


def iter_exception_chain(exc: BaseException) -> Iterable[BaseException]:
    """Yield an exception and its chained causes/contexts."""
    current: BaseException | None = exc
    while current is not None:
        yield current
        current = current.__cause__ or current.__context__
