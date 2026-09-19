"""The observatory service definition must resolve a working build context.

The web application lives in ``packages/phlo-observatory/web`` — outside the
``phlo_observatory`` Python package — so ``service.yaml`` stages its build
inputs via ``files:`` entries. These tests pin that contract: every staged
source exists, the Dockerfile it lands in the generated compose context, and
the compose service resolves ``context: web`` + ``dockerfile: Dockerfile``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from phlo.plugins.discovery._service_definition import ServiceDefinition

PACKAGE_DIR = Path(__file__).resolve().parents[1]
SERVICE_YAML = PACKAGE_DIR / "src" / "phlo_observatory" / "service.yaml"
WEB_DIR = PACKAGE_DIR / "web"


@pytest.fixture()
def service() -> ServiceDefinition:
    return ServiceDefinition.from_yaml(SERVICE_YAML)


def test_service_definition_loads(service: ServiceDefinition) -> None:
    assert service.name == "observatory"
    assert service.source_path == SERVICE_YAML.parent


def test_build_context_is_staged_web_directory(service: ServiceDefinition) -> None:
    """The compose build context must be the staged ``web`` dir, not a path
    that only exists in the author's checkout."""
    assert service.build is not None
    assert service.build["context"] == "web"
    # Compose resolves `dockerfile` relative to the context directory.
    assert service.build["dockerfile"] == "Dockerfile"


def test_every_staged_file_source_exists(service: ServiceDefinition) -> None:
    assert service.files, "service.yaml must stage the web build inputs"
    missing = [
        spec["source"]
        for spec in service.files
        if not (service.source_path / spec["source"]).exists()
    ]
    assert not missing, f"staged sources missing: {missing}"
    staged_dests = {spec["dest"] for spec in service.files}
    # Without any one of these the generated build cannot succeed.
    for required in (
        "web/Dockerfile",
        "web/package.json",
        "web/package-lock.json",
        "web/serve.mjs",
        "web/src",
    ):
        assert required in staged_dests


def test_dockerfile_uses_deterministic_install_and_real_server() -> None:
    """The image must install from the lockfile and run the compiled server —
    never `npm install` (drift) or `vite preview` (dev artifact)."""
    dockerfile = (WEB_DIR / "Dockerfile").read_text()
    assert "npm ci" in dockerfile
    assert "npm install" not in dockerfile
    assert "vite preview" not in dockerfile
    assert "serve.mjs" in dockerfile


def test_healthcheck_executable_exists_in_final_image(service: ServiceDefinition) -> None:
    """The healthcheck must run inside ``node:22-slim`` — which ships node but
    no curl/wget — so it must use the node runtime, and the compose service
    must declare one."""
    healthcheck = (service.compose or {}).get("healthcheck")
    assert healthcheck, "service.yaml must declare a healthcheck"
    test_cmd = " ".join(str(part) for part in healthcheck.get("test", []))
    assert "node" in test_cmd
    assert "wget" not in test_cmd and "curl" not in test_cmd


def test_production_start_script_runs_compiled_server() -> None:
    """``npm start`` must run the srvx launcher, never ``vite preview``."""
    import json

    package = json.loads((WEB_DIR / "package.json").read_text())
    assert package["scripts"]["start"] == "node serve.mjs"
    assert "vite preview" not in json.dumps(package["scripts"])
    # srvx must be a declared runtime dependency — it ships in the prod stage.
    assert "srvx" in package["dependencies"]


def test_proxy_route_is_server_side_only() -> None:
    """The /api/observatory proxy must read PHLO_API_URL from the server
    process env — never import.meta.env (which bakes into the client bundle)."""
    proxy = WEB_DIR / "src" / "routes" / "api" / "observatory" / "$.ts"
    text = proxy.read_text()
    assert "process.env.PHLO_API_URL" in text
    assert "import.meta.env" not in text


def test_no_author_absolute_paths_in_package() -> None:
    """Nothing under the package may bake in the author's checkout path."""
    for path in (
        *WEB_DIR.glob("Dockerfile"),
        WEB_DIR / "serve.mjs",
        SERVICE_YAML,
        WEB_DIR / "package.json",
        WEB_DIR / "vite.config.ts",
    ):
        text = path.read_text()
        assert "/Users/" not in text, f"absolute author path leaked into {path}"
        assert "/home/" not in text, f"absolute path leaked into {path}"
