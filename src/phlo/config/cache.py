"""Project-root-aware caches for configuration entry points.

``project_root_cached`` keys an lru_cache on the resolved project root and
keeps the context var active while the factory runs, so nested config objects
resolve their env files against the same root. ``cache_clear`` and
``cache_info`` are re-exposed for deliberate invalidation by tests and
long-running processes.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import lru_cache, wraps
from pathlib import Path
from typing import Any, Protocol, TypeVar, cast

from phlo.config.env import resolve_project_root, use_project_root

T = TypeVar("T")


class ProjectRootCached(Protocol[T]):
    """Callable configuration cache with the standard invalidation hook."""

    def __call__(self, project_root: Path | str | None = None) -> T: ...

    def cache_clear(self) -> None:
        """Clear the underlying cache."""
        ...

    def cache_info(self) -> Any:
        """Return the underlying cache statistics."""
        ...


def project_root_cached(factory: Callable[[Path], T]) -> ProjectRootCached[T]:
    """Cache a configuration factory by its resolved project root.

    The returned callable accepts an optional ``project_root`` argument and
    keeps the usual ``cache_clear`` and ``cache_info`` methods used by tests
    and long-running processes to invalidate configuration deliberately.
    """
    cached = lru_cache(maxsize=16)(factory)

    @wraps(factory)
    def get_cached(project_root: Path | str | None = None) -> T:
        root = resolve_project_root(project_root)
        # The context var must be active while the factory runs: nested
        # BaseConfig instances resolve their env files through
        # resolve_project_root(None) and must land on this same root.
        with use_project_root(root):
            return cached(root)

    result = cast(Any, get_cached)
    result.cache_clear = cached.cache_clear
    result.cache_info = cached.cache_info
    return cast(ProjectRootCached[T], result)
