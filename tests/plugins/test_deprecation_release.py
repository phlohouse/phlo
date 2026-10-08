"""Removal notices must name a release, and public exports must be reproducible."""

import ast
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.core_regression
ROOT = Path(__file__).resolve().parents[2]


def test_every_deprecation_warning_names_a_removal_release() -> None:
    """Inspect all warning sites, including optional plugins not installed in CI."""
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
    assert checked >= 12, "The scan must include core aliases, adapters and optional plugins"


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
