"""Removal notices must name a release, and public exports must be reproducible."""

import ast
import importlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.core_regression
ROOT = Path(__file__).resolve().parents[2]


def test_deprecation_notice_source_audit_names_a_removal_release() -> None:
    """Audit optional-package notice coverage separately from runtime warning tests."""
    paths = [*ROOT.joinpath("src/phlo").rglob("*.py")]
    for package in ROOT.joinpath("packages").iterdir():
        paths.extend(package.joinpath("src").rglob("*.py"))
    checked = 0
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        assignments = {
            target.id: node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Assign)
            for target in node.targets
            if isinstance(target, ast.Name)
        }
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Attribute) or node.func.attr != "warn":
                continue
            if not any(
                isinstance(arg, ast.Name) and arg.id == "DeprecationWarning"
                for arg in [*node.args, *(kw.value for kw in node.keywords)]
            ):
                continue
            message = node.args[0]
            if isinstance(message, ast.Name):
                message = assignments[message.id]
            text = "".join(
                part.value
                for part in ast.walk(message)
                if isinstance(part, ast.Constant) and isinstance(part.value, str)
            )
            assert re.search(r"removed in \d+\.\d+\.\d+", text), f"{path}:{node.lineno}: {text}"
            checked += 1
    assert checked, "The source audit must find the compatibility warning sites"


@pytest.mark.parametrize(
    ("module_name", "name", "kwargs"),
    [
        ("phlo.operations.adapters", "SyncToAsyncIngesterAdapter", {}),
        ("phlo.operations.adapters", "AsyncToSyncIngesterAdapter", {}),
        ("phlo.operations.adapters", "SyncToAsyncTransformerAdapter", {}),
        ("phlo.operations.adapters", "AsyncToSyncTransformerAdapter", {}),
        ("phlo.identity.bridge", "create_regulated_mode_bridge", {}),
        ("phlo.infrastructure.config", "get_regulated_mode_config", {}),
        ("phlo.security.mode", "is_regulated_mode_enabled", {}),
        ("phlo.security.validation", "run_regulated_mode_validation", {"config_regulated": False}),
        (
            "phlo.security.validation",
            "require_regulated_mode_validation",
            {"config_regulated": False},
        ),
    ],
)
def test_public_compatibility_apis_emit_the_removal_release(
    module_name: str,
    name: str,
    kwargs: dict,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    monkeypatch.setenv("PHLO_REGULATED", "false")
    (tmp_path / "phlo.yaml").write_text("regulated_mode: true\n", encoding="utf-8")
    callback = getattr(importlib.import_module(module_name), name)
    args = (SimpleNamespace(context=None, logger=None),) if name.endswith("Adapter") else ()
    with pytest.warns(DeprecationWarning, match=r"removed in 0\.19\.0") as notices:
        callback(*args, **kwargs)
    assert all("removed in 0.19.0" in str(notice.message) for notice in notices)


def test_all_is_a_sorted_tuple_across_hash_seeds() -> None:
    outputs = []
    for seed in ("1", "7", "123"):
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import json, phlo; assert isinstance(phlo.__all__, tuple); "
                "print(json.dumps(phlo.__all__))",
            ],
            env={**os.environ, "PYTHONHASHSEED": seed},
            check=True,
            capture_output=True,
            text=True,
        )
        outputs.append(json.loads(result.stdout))
    assert outputs[0] == outputs[1] == outputs[2]
    assert outputs[0] == sorted(set(outputs[0]))
    assert {"ingestion", "transform", "phlo_ingestion", "get_ingestion_assets"} <= set(outputs[0])
