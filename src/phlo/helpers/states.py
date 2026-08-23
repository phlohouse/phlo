"""Generic state-transition validation helpers.

Transitions are validated against declarative StateTransitionRule tables;
terminal states only ever allow self-transitions. Validation works on
plain mappings ordered by a caller-supplied field, so it needs no model
coupling.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class StateTransitionRule:
    """Allowed state transitions for one state-machine surface."""

    allowed: dict[str, set[str]]
    terminal_states: set[str] = field(default_factory=set)


def assert_valid_transition(
    previous_state: str,
    next_state: str,
    rule: StateTransitionRule,
) -> bool:
    """Return whether a transition is allowed."""
    if previous_state in rule.terminal_states and next_state != previous_state:
        return False
    return next_state in rule.allowed.get(previous_state, set())


def invalid_transitions(
    events: Iterable[Mapping[str, Any]],
    *,
    entity_field: str,
    state_field: str,
    order_field: str,
    rule: StateTransitionRule,
) -> list[dict[str, Any]]:
    """Return adjacent state transitions that violate a rule."""
    by_entity: dict[str, list[Mapping[str, Any]]] = {}
    for event in events:
        by_entity.setdefault(str(event[entity_field]), []).append(event)

    invalid: list[dict[str, Any]] = []
    for entity, entity_events in by_entity.items():
        ordered = sorted(entity_events, key=lambda event: event[order_field])
        for previous, current in zip(ordered, ordered[1:], strict=False):
            if not assert_valid_transition(
                str(previous[state_field]),
                str(current[state_field]),
                rule,
            ):
                invalid.append(
                    {
                        "entity": entity,
                        "from": previous[state_field],
                        "to": current[state_field],
                        "at": current[order_field],
                    }
                )
    return invalid


def terminal_state_filter(
    rows: Iterable[Mapping[str, Any]],
    *,
    state_field: str,
    terminal_states: set[str],
) -> list[dict[str, Any]]:
    """Return rows in terminal states."""
    return [dict(row) for row in rows if str(row.get(state_field)) in terminal_states]
