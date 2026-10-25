"""Shared contract helpers for package CLI authorization surface adapters.

Every regulated provider package ships a surface adapter generated from its
command tables via ``cli_surface_adapter_class``. The contract is identical
everywhere, so it is expressed once here:

- reads allow without enforcement,
- unknown commands deny closed with ``unknown_command``,
- mutation commands flow a policy allow through enforcement and block a
  policy deny with the backend's reason code.

Package suites call these helpers instead of maintaining near-identical
copies of the same checks. The core ``CliPrincipalResolver`` contract lives
with its owner in ``tests/cli/test_cli_authorization.py``.
"""

from __future__ import annotations

import contextlib
from typing import Iterator
from unittest.mock import MagicMock, patch


@contextlib.contextmanager
def reset_surface_adapter_singleton(adapter_cls) -> Iterator[None]:
    """Clear an adapter singleton around a test that mutates wiring."""
    adapter_cls._instance = None
    try:
        yield
    finally:
        adapter_cls._instance = None


@contextlib.contextmanager
def patched_enforcement(*, allowed: bool, reason_code: str | None = None) -> Iterator[None]:
    """Route ``enforce()`` through a stubbed context with a fixed decision."""
    enter_stack = contextlib.ExitStack()
    mock_ctx = enter_stack.enter_context(patch("phlo.security.enforcement.EnforcementContext"))
    instance = MagicMock()
    mock_ctx.get_instance.return_value = instance
    instance.canonicalize.side_effect = lambda principal: principal
    instance.authorization_backend.explain_decision.return_value = MagicMock(
        allowed=allowed,
        reason_code=reason_code,
        policy_id=None,
        explanation=None,
    )
    with enter_stack:
        yield


def assert_read_commands_allow_without_enforcement(adapter) -> None:
    """Every classified read command must allow without calling ``enforce``.

    ``phlo.cli.authorization`` binds ``enforce`` at import time, so the patch
    target must be that module rather than the definition module.
    """
    with patch("phlo.cli.authorization.enforce") as mock_enforce:
        for command in adapter.read_commands:
            result = adapter.check_command_authorization(command)
            assert result.allowed is True, f"read command {command} should be allowed"
        mock_enforce.assert_not_called()


def assert_unknown_command_denied(adapter, unknown_command: str) -> None:
    """Unclassified commands deny closed so new commands cannot skip auth."""
    result = adapter.check_command_authorization(unknown_command)
    assert result.allowed is False
    assert result.reason_code == "unknown_command"


def assert_mutation_enforcement_allows_and_denies(adapter, mutation_command: str) -> None:
    """A mutation command must honor both policy outcomes through enforcement."""
    with patched_enforcement(allowed=True):
        allowed_result = adapter.check_command_authorization(mutation_command)
    assert allowed_result.allowed is True

    with patched_enforcement(allowed=False, reason_code="policy_denied"):
        denied_result = adapter.check_command_authorization(mutation_command)
    assert denied_result.allowed is False
    assert denied_result.reason_code == "policy_denied"
