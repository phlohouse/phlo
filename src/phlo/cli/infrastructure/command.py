"""Subprocess execution helpers that redact sensitive arguments before logging.

Secret-valued arguments are redacted in both `--name value` and
`--name=value` shapes, so logs never contain credentials. Non-zero exits
raise CommandError carrying the redacted command, status, and captured
output.
"""

from __future__ import annotations

import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from subprocess import CompletedProcess

from phlo.exceptions import redact_sensitive_text
from phlo.logging import get_logger

logger = get_logger(__name__)

_SENSITIVE_ARGUMENT_NAMES = frozenset(
    {
        "api-key",
        "apikey",
        "authorization",
        "credential",
        "password",
        "passwd",
        "secret",
        "signing-key",
        "token",
    }
)


# Redact secret values in both argument shapes: `--name value` consumes the
# next token, while `--name=value` is caught by pattern matching alone.
def _redact_command_args(cmd: tuple[str, ...]) -> tuple[str, ...]:
    redacted: list[str] = []
    redact_next = False
    for part in cmd:
        if redact_next:
            redacted.append("<redacted>")
            redact_next = False
            continue

        redacted_part = redact_sensitive_text(part)
        redacted.append(redacted_part)
        option_name = part.lstrip("-").split("=", 1)[0].lower()
        if option_name in _SENSITIVE_ARGUMENT_NAMES and "=" not in part:
            redact_next = True
    return tuple(redacted)


@dataclass(frozen=True, slots=True)
class CommandError(RuntimeError):
    """Error raised when a subprocess command exits with a non-zero status.

    Arguments, stdout, and stderr are redacted at construction time, so an
    instance is safe to log or display without leaking credentials.
    """

    cmd: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str

    def __post_init__(self) -> None:
        """Populate RuntimeError args tuple for consistent exception rendering."""

        redacted_cmd = _redact_command_args(self.cmd)
        object.__setattr__(self, "cmd", redacted_cmd)
        object.__setattr__(self, "stdout", redact_sensitive_text(self.stdout))
        object.__setattr__(self, "stderr", redact_sensitive_text(self.stderr))
        object.__setattr__(self, "args", (self.cmd, self.returncode, self.stdout, self.stderr))

    def __str__(self) -> str:
        """Render a readable command failure message."""

        cmd = redact_sensitive_text(" ".join(self.cmd))
        stderr = self.stderr.strip()
        if stderr:
            return f"Command failed ({self.returncode}): {cmd}\n{stderr}"
        return f"Command failed ({self.returncode}): {cmd}"


def run_command(
    cmd: Sequence[str],
    *,
    timeout_seconds: int | None = None,
    cwd: str | None = None,
    env: Mapping[str, str] | None = None,
    capture_output: bool = True,
    check: bool = True,
) -> CompletedProcess[str]:
    """Run a subprocess command with optional timeout, working directory, and
    environment overrides, returning the CompletedProcess.

    Raises CommandError when check is True and the command exits non-zero, and
    subprocess.TimeoutExpired when it exceeds timeout_seconds.
    """
    command_name = cmd[0] if cmd else "<empty>"
    logger.debug(
        "subprocess_command_started",
        command_name=command_name,
        arg_count=max(len(cmd) - 1, 0),
        cwd=cwd,
        timeout_seconds=timeout_seconds,
        capture_output=capture_output,
    )

    result = subprocess.run(
        list(cmd),
        capture_output=capture_output,
        text=capture_output,
        timeout=timeout_seconds,
        cwd=cwd,
        env=None if env is None else dict(env),
        check=False,
    )
    stdout = result.stdout or ""
    stderr = result.stderr or ""
    if check and result.returncode != 0:
        logger.error(
            "subprocess_command_failed",
            command_name=command_name,
            returncode=result.returncode,
            stdout_length=len(stdout),
            stderr_length=len(stderr),
        )
        raise CommandError(
            cmd=tuple(cmd),
            returncode=result.returncode,
            stdout=stdout,
            stderr=stderr,
        )
    logger.debug(
        "subprocess_command_completed",
        command_name=command_name,
        returncode=result.returncode,
    )
    return result
