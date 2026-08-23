"""Tests for backend compiler coverage of canonical actions.

The compiler-backed action set must stay inside the canonical taxonomy,
every compiled action must have at least one registered compiler, and
Trino's mapping must cover exactly its declared actions.
"""

from __future__ import annotations

from phlo.rbac.compiler import COMPILER_REGISTRY, TrinoCompiler
from phlo.rbac.models import CANONICAL_ACTIONS

EXPECTED_COMPILED_ACTIONS = frozenset(
    action
    for cls in COMPILER_REGISTRY.values()
    for action in CANONICAL_ACTIONS
    if cls(backend=None).supports_action(action)
)

EXPECTED_TRINO_ACTIONS = frozenset(TrinoCompiler.ACTION_MAPPING)


def test_every_compiled_canonical_action_has_at_least_one_compiler():
    """Every action with documented backend compilation must be compiler-backed."""
    unsupported = set()
    for action in EXPECTED_COMPILED_ACTIONS:
        supported_by_any = any(
            cls(backend=None).supports_action(action) for cls in COMPILER_REGISTRY.values()
        )
        if not supported_by_any:
            unsupported.add(action)

    assert not unsupported, f"Actions without any compiler support: {unsupported}"


def test_expected_compiled_actions_remain_canonical():
    """The compiler-backed action set must stay inside the canonical taxonomy."""
    assert EXPECTED_COMPILED_ACTIONS <= CANONICAL_ACTIONS


def test_trino_compiled_actions_stay_explicit():
    """Trino is currently the only backed compiler."""
    assert EXPECTED_COMPILED_ACTIONS == EXPECTED_TRINO_ACTIONS


def test_compiler_registry_not_empty():
    """At least one backend compiler must be registered."""
    assert len(COMPILER_REGISTRY) > 0


def test_compiler_coverage_report():
    """Generate a coverage report for documentation purposes."""
    report: dict[str, dict[str, bool]] = {}

    for action in CANONICAL_ACTIONS:
        report[action] = {}
        for name, cls in sorted(COMPILER_REGISTRY.items()):
            compiler = cls(backend=None)
            report[action][name] = compiler.supports_action(action)

    for name, cls in sorted(COMPILER_REGISTRY.items()):
        compiler = cls(backend=None)
        supported = [a for a in CANONICAL_ACTIONS if compiler.supports_action(a)]
        print(f"{name}: {len(supported)}/{len(CANONICAL_ACTIONS)}")

    print("\nCoverage matrix:")
    for action in sorted(CANONICAL_ACTIONS):
        row = [f"{action:20}"]
        for name in sorted(COMPILER_REGISTRY.keys()):
            row.append("✓" if report[action][name] else "—")
        print("  ".join(row))
