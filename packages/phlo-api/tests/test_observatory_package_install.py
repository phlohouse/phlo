"""Contract tests for the extracted Observatory package-install slice.

Locks the route/OpenAPI contract of the /packages/install router against
the full app operation set, and the install behavior: only trusted
registry packages are accepted (unknown ones rejected before execution)
and uv-managed projects install through `uv add`.
"""

from __future__ import annotations

from pathlib import Path
import subprocess

from phlo_api.main import app
from phlo_api.observatory_api import observatory, package_install, run_report
from security_test_support import authenticated_client


def test_package_install_router_preserves_the_observatory_route_contract() -> None:
    """The extracted router replaces exactly one existing Observatory operation."""
    observatory_operations = {
        (method, route.path)
        for route in observatory.router.routes
        for method in route.methods or ()
    }
    package_install_operations = {
        (method, route.path)
        for route in package_install.router.routes
        for method in route.methods or ()
    }
    run_report_operations = {
        (method, route.path) for route in run_report.router.routes for method in route.methods or ()
    }
    registered_operations = {
        (method, route.path.removeprefix("/api/observatory"))
        for route in app.routes
        if getattr(route, "methods", None) and route.path.startswith("/api/observatory")
        for method in route.methods
    }

    assert package_install_operations == {
        ("POST", "/packages/install"),
    }
    assert ("POST", "/packages/install") not in observatory_operations
    assert (
        observatory_operations | package_install_operations | run_report_operations
        == registered_operations
    )
    assert len(registered_operations) == 66


def test_package_install_openapi_contract_is_unchanged() -> None:
    operation = app.openapi()["paths"]["/api/observatory/packages/install"]["post"]

    assert operation == {
        "description": "Install a trusted Phlo Python package into the current environment.",
        "operationId": "post_observatory_package_install_api_observatory_packages_install_post",
        "requestBody": {
            "content": {
                "application/json": {
                    "schema": {"$ref": "#/components/schemas/ObservatoryPackageInstallRequest"}
                }
            },
            "required": True,
        },
        "responses": {
            "200": {
                "content": {
                    "application/json": {
                        "schema": {"$ref": "#/components/schemas/ObservatoryPackageInstallResult"}
                    }
                },
                "description": "Successful Response",
            },
            "422": {
                "content": {
                    "application/json": {
                        "schema": {"$ref": "#/components/schemas/HTTPValidationError"}
                    }
                },
                "description": "Validation Error",
            },
        },
        "summary": "Post Observatory Package Install",
        "tags": ["observatory"],
    }


def test_observatory_package_install_uses_trusted_registry_package(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    monkeypatch.setenv(
        "PHLO_API_TOKENS",
        '{"admin-token":{"subject":"admin","scopes":["admin"]}}',
    )
    monkeypatch.setattr(
        package_install,
        "get_registry_data",
        lambda: {
            "plugins": {
                "openmetadata": {
                    "type": "service",
                    "package": "phlo-openmetadata",
                    "version": "0.1.0",
                }
            }
        },
    )
    installed: list[str] = []

    def fake_install(package_spec: str) -> tuple[bool, str]:
        installed.append(package_spec)
        return True, "installed"

    monkeypatch.setattr(package_install, "_run_python_package_install", fake_install)
    monkeypatch.setattr(package_install, "_load_services", lambda: [])

    response = authenticated_client("admin").post(
        "/api/observatory/packages/install",
        json={"package_name": "openmetadata"},
        headers={"Authorization": "Bearer admin-token"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "succeeded"
    assert response.json()["package_name"] == "phlo-openmetadata"
    assert installed == ["phlo-openmetadata==0.1.0"]


def test_observatory_package_install_rejects_unknown_package_before_execution(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    monkeypatch.setenv(
        "PHLO_API_TOKENS",
        '{"admin-token":{"subject":"admin","scopes":["admin"]}}',
    )
    monkeypatch.setattr(package_install, "get_registry_data", lambda: {"plugins": {}})
    monkeypatch.setattr(
        package_install,
        "_run_python_package_install",
        lambda _package_spec: (_ for _ in ()).throw(AssertionError("installer must not run")),
    )

    response = authenticated_client("admin").post(
        "/api/observatory/packages/install",
        json={"package_name": "not-a-phlo-package"},
        headers={"Authorization": "Bearer admin-token"},
    )

    assert response.status_code == 400


def test_observatory_package_install_prefers_uv_add_for_uv_projects(
    monkeypatch,
    tmp_path: Path,
) -> None:
    commands: list[tuple[list[str], Path | None]] = []

    def fake_run(command, **kwargs):
        commands.append((command, kwargs.get("cwd")))
        return subprocess.CompletedProcess(command, returncode=0, stdout="ok")

    monkeypatch.setattr(package_install.shutil, "which", lambda name: "/usr/bin/uv")
    monkeypatch.setattr(package_install, "_uv_project_root", lambda: tmp_path)
    monkeypatch.setattr(subprocess, "run", fake_run)

    succeeded, message = package_install._run_python_package_install("phlo-openmetadata==0.1.0")

    assert succeeded is True
    assert message == "ok"
    assert commands == [(["/usr/bin/uv", "add", "--active", "phlo-openmetadata==0.1.0"], tmp_path)]
