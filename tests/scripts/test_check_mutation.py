"""A successful mutmut process must not conceal survivors or unexecuted mutations."""

import hashlib
import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "check_mutation", Path(__file__).resolve().parents[2] / "scripts/check_mutation.py"
)
assert SPEC and SPEC.loader
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)


@pytest.mark.parametrize(
    "status", ["survived", "no tests", "not checked", "timeout", "suspicious", "skipped"]
)
def test_unproved_selected_mutant_blocks(status):
    assert gate.verdict(f"policy.x_check__mutmut_1: {status}\n", ["policy.x_check__mutmut_*"], [])


def test_killed_selected_mutants_pass_and_unrelated_results_do_not_count():
    output = "policy.x_check__mutmut_1: killed\nother.x_unused__mutmut_1: no tests\n"
    assert gate.verdict(output, ["policy.x_check__mutmut_*"], []) == []
    assert gate.verdict(output, ["policy.x_missing__mutmut_*"], []) == [
        "No mutant results for policy.x_missing__mutmut_*"
    ]


def test_exception_is_bound_to_the_actual_diff_not_just_mutant_number(monkeypatch):
    diff = "--- original\n+++ mutant\n- default={}\n+ default=None"
    monkeypatch.setattr(
        gate.subprocess, "check_output", lambda *args, **kwargs: "# mutant: survived\n" + diff
    )
    record = {
        "mutant": "policy.x_check__mutmut_1",
        "diff_sha256": hashlib.sha256(diff.encode()).hexdigest(),
        "reason": "Missing mapping remains empty",
    }
    output = "policy.x_check__mutmut_1: survived\n"
    assert gate.verdict(output, ["policy.x_check__mutmut_*"], [record]) == []
    record["diff_sha256"] = "0" * 64
    assert gate.verdict(output, ["policy.x_check__mutmut_*"], [record]) == [
        "policy.x_check__mutmut_1: survived"
    ]
