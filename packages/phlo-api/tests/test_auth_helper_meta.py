"""Mechanical guard for API tests that call protected HTTP routes.

Parses sibling test modules with ast and fails when a direct TestClient call
hits a manifest-listed route without auth headers, an explicit 401
assertion, or an unregulated development-test opt-out.
"""

from __future__ import annotations

import ast
from pathlib import Path

from phlo_api.security_manifest import HTTP_ROUTE_KEY_MANIFEST


def _literal_string(node: ast.AST | None) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _contains_status_assertion(node: ast.AST, status: int) -> bool:
    return any(
        isinstance(candidate, ast.Constant) and candidate.value == status
        for candidate in ast.walk(node)
    )


def _direct_test_client_calls(function: ast.AST) -> list[ast.Call]:
    calls: list[ast.Call] = []
    for node in ast.walk(function):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        client_constructor = node.func.value
        if not isinstance(client_constructor, ast.Call):
            continue
        if (
            not isinstance(client_constructor.func, ast.Name)
            or client_constructor.func.id != "TestClient"
        ):
            continue
        if not client_constructor.args or not isinstance(client_constructor.args[0], ast.Name):
            continue
        if client_constructor.args[0].id == "app":
            calls.append(node)
    return calls


def _is_explicit_development_test(function: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    # Opt-out for tests of unregulated (development-only) routes; the explicit
    # 200 assertion shows the request is meant to succeed without auth headers.
    return "unregulated" in function.name and _contains_status_assertion(function, 200)


def test_protected_testclient_calls_name_an_auth_helper_or_401() -> None:
    test_root = Path(__file__).parent
    violations: list[str] = []
    for path in sorted(test_root.glob("test_*.py")):
        if path.name == Path(__file__).name:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for function in ast.walk(tree):
            if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for call in _direct_test_client_calls(function):
                if not call.args:
                    continue
                route_path = _literal_string(call.args[0])
                if route_path is None:
                    continue
                method = call.func.attr.upper()
                if method == "OPTIONS" or (method, route_path) not in HTTP_ROUTE_KEY_MANIFEST:
                    continue
                has_headers = any(keyword.arg == "headers" for keyword in call.keywords)
                if (
                    has_headers
                    or _contains_status_assertion(function, 401)
                    or _is_explicit_development_test(function)
                ):
                    continue
                violations.append(f"{path.name}:{call.lineno} {method} {route_path}")

    assert not violations, (
        "Protected TestClient calls need authenticated_client(...) or an explicit 401 assertion: "
        + "; ".join(violations)
    )
