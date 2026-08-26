"""Tests for backend compiler coverage of canonical actions.

The compiler-backed action set is pinned to its documented contract: the
golden set below is deliberately literal, so any compiler gaining or losing
action coverage fails here instead of silently redefining the expectation.
"""

from __future__ import annotations

from phlo.rbac.compiler import COMPILER_REGISTRY, TrinoCompiler
from phlo.rbac.models import CANONICAL_ACTIONS

GOLDEN_COMPILED_ACTIONS = frozenset({"dataset.query", "dataset.read"})


def _compiled_actions() -> set[str]:
    """Return every canonical action at least one registered compiler supports."""
    return {
        action
        for cls in COMPILER_REGISTRY.values()
        for action in CANONICAL_ACTIONS
        if cls(backend=None).supports_action(action)
    }


def test_compiled_actions_match_the_documented_contract():
    """Compiler-backed coverage must equal the pinned golden action set."""
    assert _compiled_actions() == GOLDEN_COMPILED_ACTIONS


def test_trino_declared_mapping_matches_the_documented_contract():
    """Trino's declared ACTION_MAPPING must cover exactly the golden set."""
    assert frozenset(TrinoCompiler.ACTION_MAPPING) == GOLDEN_COMPILED_ACTIONS


def test_compiler_registry_not_empty():
    """At least one backend compiler must be registered."""
    assert len(COMPILER_REGISTRY) > 0
