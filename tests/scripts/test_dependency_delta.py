"""Regression contracts for dependency risk and failed assessment handling."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "dependency_delta", Path(__file__).resolve().parents[2] / "scripts/dependency_delta.py"
)
assert SPEC and SPEC.loader
delta = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(delta)


def test_new_advisory_on_unchanged_dependency_does_not_block() -> None:
    package = ("PyPI", "example", "1.0")
    inventory = {"uv.lock": {package}}
    result = delta.compare(inventory, inventory, {package: ["NEW-ADVISORY"]})
    assert result["introduced"] == []
    assert result["existing"][0]["advisories"] == ["NEW-ADVISORY"]


def test_added_or_changed_vulnerable_version_blocks_even_with_same_advisory() -> None:
    old, new = ("npm", "example", "1.0.0"), ("npm", "example", "1.0.1")
    report = delta.compare({"app": {old}}, {"app": {new}}, {new: ["CVE-1"]})
    assert report["introduced"][0]["version"] == "1.0.1"
    assert report["existing"] == []


def test_moving_vulnerable_dependency_to_another_product_is_new_risk() -> None:
    package = ("npm", "example", "1.0.0")
    report = delta.compare({"docs": {package}}, {"agent": {package}}, {package: ["CVE-1"]})
    assert report["introduced"][0]["lockfile"] == "agent"


def test_fixed_dependency_and_removed_dependency_do_not_block() -> None:
    old, new = ("npm", "example", "1.0.0"), ("npm", "example", "2.0.0")
    assert delta.compare({"app": {old}}, {"app": {new}}, {new: []})["introduced"] == []
    assert delta.compare({"app": {old}}, {"app": set()}, {})["introduced"] == []


def test_uv_registry_names_are_normalized_and_workspace_packages_excluded() -> None:
    content = b"""[[package]]
name = "Some_Package"
version = "1.2"
source = { registry = "https://pypi.org/simple" }
[[package]]
name = "phlo"
version = "0.14.0"
source = { editable = "." }
"""
    assert delta.parse_lock("uv.lock", content) == {("PyPI", "some-package", "1.2")}


def test_npm_nested_scopes_and_aliases_preserve_real_package_identity() -> None:
    content = json.dumps(
        {
            "lockfileVersion": 3,
            "packages": {
                "": {"name": "local-app", "version": "1.0.0"},
                "node_modules/a/node_modules/@scope/dep": {
                    "version": "2.0.0",
                    "resolved": "https://registry.npmjs.org/@scope/dep/-/dep-2.0.0.tgz",
                },
                "node_modules/alias": {
                    "name": "real-name",
                    "version": "1.0.0",
                    "resolved": "https://registry.npmjs.org/real-name/-/real-name-1.0.0.tgz",
                },
                "node_modules/local": {"link": True},
            },
        }
    ).encode()
    assert delta.parse_lock("package-lock.json", content) == {
        ("npm", "@scope/dep", "2.0.0"),
        ("npm", "real-name", "1.0.0"),
    }


def test_all_current_product_locks_are_readable() -> None:
    root = Path(__file__).resolve().parents[2]
    for path in delta.LOCKFILES:
        assert delta.parse_lock(path, (root / path).read_bytes())


@pytest.mark.parametrize("response", [{"results": []}, {"results": [{"next_page_token": "x"}]}])
def test_incomplete_scanner_response_is_not_clean(monkeypatch, response) -> None:
    import io

    monkeypatch.setattr(
        delta.urllib.request, "urlopen", lambda *a, **kw: io.BytesIO(json.dumps(response).encode())
    )
    with pytest.raises(ValueError):
        delta.query_osv([("npm", "example", "1.0.0")])


def test_bad_base_writes_unavailable_report_and_fails(tmp_path) -> None:
    root = Path(__file__).resolve().parents[2]
    output = tmp_path / "report.json"
    result = subprocess.run(
        [
            sys.executable,
            str(root / "scripts/dependency_delta.py"),
            "--base",
            "not-a-sha",
            "--head",
            "0" * 40,
            "--output",
            str(output),
        ],
        capture_output=True,
    )
    assert result.returncode == 2
    assert json.loads(output.read_text())["assessment"] == "unavailable"


@pytest.mark.parametrize(
    "source",
    ["git+https://github.com/example/dep.git", "file:../dep", "https://example.com/dep.tgz", ""],
)
def test_non_registry_npm_source_is_unavailable_even_with_a_normal_version(source):
    content = json.dumps(
        {
            "lockfileVersion": 3,
            "packages": {"node_modules/example": {"version": "1.0.0", "resolved": source}},
        }
    ).encode()
    with pytest.raises(ValueError, match="Unsupported npm source"):
        delta.parse_lock("package-lock.json", content)
