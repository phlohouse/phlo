"""Transient source failure injection for the retry_recovery scenario.

WAP launches execute inside the Dagster service, so environment variables
exported next to ``phlo materialize`` do not reach the ingestion asset. The
arm signal therefore lives on the shared project filesystem by default; the
environment variables remain supported for in-process runs (pytest, local
diagnostics):

- ``PHLO_WAP_LAB_FAIL_ONCE=1`` arms the failure directly.
- ``PHLO_WAP_LAB_ARM_FILE`` points at an arm marker file.
- ``PHLO_WAP_LAB_ATTEMPT_FILE`` overrides the attempt counter location.

The counter file is durable evidence: after a successful recovery it records
exactly how many attempts the run consumed.
"""

from __future__ import annotations

import os
from pathlib import Path

FAIL_ONCE_ENV = "PHLO_WAP_LAB_FAIL_ONCE"
ARM_FILE_ENV = "PHLO_WAP_LAB_ARM_FILE"
ATTEMPT_FILE_ENV = "PHLO_WAP_LAB_ATTEMPT_FILE"

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ARM_FILE = PROJECT_ROOT / ".phlo" / "wap-lab" / "retry-arm"
DEFAULT_ATTEMPT_FILE = PROJECT_ROOT / ".phlo" / "wap-lab" / "retry-attempts.txt"

MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 5


class TransientSourceError(RuntimeError):
    """Raised once per armed run to exercise the retry path."""


def _resolve(path_text: str | os.PathLike[str], cwd: Path) -> Path:
    path = Path(path_text)
    return path if path.is_absolute() else cwd / path


def transient_failure_armed(env: dict[str, str] = os.environ, cwd: Path = Path.cwd()) -> bool:
    """Return whether this run should fail its first attempt."""
    if env.get(FAIL_ONCE_ENV) == "1":
        return True
    arm_file = _resolve(env.get(ARM_FILE_ENV, str(DEFAULT_ARM_FILE)), cwd)
    return arm_file.exists()


def attempt_counter_path(env: dict[str, str] = os.environ, cwd: Path = Path.cwd()) -> Path:
    """Return the durable attempt-counter file location."""
    return _resolve(env.get(ATTEMPT_FILE_ENV, str(DEFAULT_ATTEMPT_FILE)), cwd)


def read_attempts(counter_path: Path) -> int:
    """Read the recorded attempt count; missing files mean zero attempts."""
    try:
        return int(counter_path.read_text(encoding="utf-8").strip() or "0")
    except FileNotFoundError:
        return 0


def write_attempts(counter_path: Path, attempts: int) -> None:
    """Persist the attempt count atomically enough for sibling retries."""
    counter_path.parent.mkdir(parents=True, exist_ok=True)
    counter_path.write_text(f"{attempts}\n", encoding="utf-8")


def raise_if_first_attempt(counter_path: Path, armed: bool) -> int:
    """Record one attempt and fail it only when armed and it is the first.

    Attempt one raises :class:`TransientSourceError` so Dagster's retry policy
    re-executes the op; attempt two succeeds. Returns the attempt number that
    was just recorded.
    """
    attempts = read_attempts(counter_path) + 1
    write_attempts(counter_path, attempts)
    if armed and attempts == 1:
        raise TransientSourceError(
            "transient source outage injected on first attempt "
            f"(attempt {attempts} of max_retries={MAX_RETRIES})"
        )
    return attempts


def reset_retry_state(
    arm_file: Path = DEFAULT_ARM_FILE, counter_path: Path = DEFAULT_ATTEMPT_FILE
) -> None:
    """Clear arm marker and counter before a fresh scenario run."""
    arm_file.unlink(missing_ok=True)
    counter_path.unlink(missing_ok=True)


if __name__ == "__main__":
    print(f"armed={transient_failure_armed()} counter={read_attempts(attempt_counter_path())}")
