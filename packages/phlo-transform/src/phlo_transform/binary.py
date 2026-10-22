"""Locate, install, and invoke the Phlo Transform CLI binary.

The Python provider is a thin adapter over the Rust engine: every semantic
answer comes from the binary's JSON reports rather than a reimplementation.
The binary is looked up in ``PHLO_TRANSFORM_BIN``, then ``.phlo/bin``, then
``PATH``; ``phlo transform install`` downloads the pinned portable release
into ``.phlo/bin`` so a project can carry its engine version with it.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import tarfile
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.request import urlopen

from phlo.exceptions import PhloCapabilitySetupError
from phlo.logging import get_logger

from phlo_transform.settings import TransformSettings

logger = get_logger(__name__)

RELEASE_BASE_URL = "https://github.com/phlohouse/phlo-transform/releases/download"
BINARY_NAME = "phlo-transform"

_TARGETS = {
    ("linux", "x86_64"): "x86_64-unknown-linux-gnu",
    ("linux", "amd64"): "x86_64-unknown-linux-gnu",
    ("linux", "aarch64"): "aarch64-unknown-linux-gnu",
    ("linux", "arm64"): "aarch64-unknown-linux-gnu",
    ("darwin", "x86_64"): "x86_64-apple-darwin",
    ("darwin", "arm64"): "aarch64-apple-darwin",
    ("windows", "amd64"): "x86_64-pc-windows-msvc",
    ("windows", "x86_64"): "x86_64-pc-windows-msvc",
}


@dataclass(frozen=True, slots=True)
class Invocation:
    """Captured result of one CLI invocation."""

    command: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        """Return True when the process exited successfully."""

        return self.returncode == 0

    def json(self) -> dict | None:
        """Parse the JSON report emitted on stdout, if any."""

        text = self.stdout.strip()
        if not text:
            return None
        try:
            payload = json.loads(text)
        except ValueError:
            return None
        return payload if isinstance(payload, dict) else None


def binary_name() -> str:
    """Return the executable name for the current platform."""

    return f"{BINARY_NAME}.exe" if platform.system().lower() == "windows" else BINARY_NAME


def release_target() -> str:
    """Return the release target triple for the current platform."""

    system = platform.system().lower()
    machine = platform.machine().lower()
    target = _TARGETS.get((system, machine))
    if target is None:
        raise PhloCapabilitySetupError(
            capability="transform",
            required=True,
            message=f"phlo-transform has no portable release for {system}/{machine}",
            suggestions=[
                "Build the engine from source: cargo install --path crates/phlo-transform-cli",
                "Point PHLO_TRANSFORM_BIN at an existing phlo-transform executable",
            ],
        )
    return target


def find_binary(project_root: Path, settings: TransformSettings) -> Path | None:
    """Return the first usable binary path, or None when none is installed."""

    candidates: list[Path] = []
    if settings.binary:
        configured = Path(settings.binary).expanduser()
        candidates.append(configured if configured.is_absolute() else project_root / configured)
    candidates.append(project_root / ".phlo" / "bin" / binary_name())
    on_path = shutil.which(BINARY_NAME)
    if on_path:
        candidates.append(Path(on_path))
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def require_binary(project_root: Path, settings: TransformSettings) -> Path:
    """Return the installed binary or raise actionable setup guidance."""

    binary = find_binary(project_root, settings)
    if binary is not None:
        return binary
    raise PhloCapabilitySetupError(
        capability="transform",
        required=True,
        message="phlo-transform workspace found but the phlo-transform executable is missing",
        suggestions=[
            "Run: phlo transform install",
            "Or install it on PATH: cargo install --path crates/phlo-transform-cli",
            "Or set PHLO_TRANSFORM_BIN to an existing executable",
        ],
    )


def binary_version(binary: Path, settings: TransformSettings) -> str | None:
    """Return the binary's reported version, or None when it cannot be read."""

    invocation = run_cli(binary, [], settings=settings, root=None, extra_args=["--version"])
    text = (invocation.stdout or invocation.stderr).strip().splitlines()
    if not text:
        return None
    return text[0].strip()


def _public_command(command: list[str]) -> list[str]:
    public: list[str] = []
    redact_next = False
    for argument in command:
        if redact_next:
            public.append("[redacted]")
            redact_next = False
        elif argument in {"--trino-password", "--nessie-token"}:
            public.append(argument)
            redact_next = True
        elif argument.startswith(("--trino-password=", "--nessie-token=")):
            public.append(argument.partition("=")[0] + "=[redacted]")
        else:
            public.append(argument)
    return public


def run_cli(
    binary: Path,
    args: list[str],
    *,
    settings: TransformSettings,
    root: Path | None,
    extra_args: list[str] | None = None,
    environment: Mapping[str, str | None] | None = None,
) -> Invocation:
    """Invoke the CLI and capture its output without raising on failure."""

    command = [str(binary)]
    if root is not None:
        command.extend(["--root", str(root)])
    command.extend(extra_args or [])
    command.extend(args)
    public_command = _public_command(command)
    env = {**os.environ}
    for key, value in (environment or {}).items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    try:
        # reason: the executable path is resolved from settings or PATH, not shell input.
        completed = subprocess.run(  # noqa: S603 - the executable path is resolved locally
            command,
            capture_output=True,
            text=True,
            check=False,
            env=env,
            timeout=settings.timeout_seconds,
        )
    except FileNotFoundError as exc:
        raise PhloCapabilitySetupError(
            capability="transform",
            required=True,
            message=f"phlo-transform executable not found: {binary}",
            suggestions=["Run: phlo transform install"],
            cause=exc,
        ) from exc
    except subprocess.TimeoutExpired as exc:
        exc.cmd = public_command
        raise PhloCapabilitySetupError(
            capability="transform",
            required=True,
            message=f"phlo-transform exceeded {settings.timeout_seconds:.0f}s: {' '.join(public_command)}",
            suggestions=["Raise PHLO_TRANSFORM_TIMEOUT_SECONDS or narrow the selection"],
            cause=exc,
        ) from exc
    logger.debug(
        "phlo_transform_cli_finished",
        command=" ".join(public_command),
        returncode=completed.returncode,
    )
    return Invocation(
        command=tuple(public_command),
        returncode=completed.returncode,
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
    )


def run_json(
    binary: Path,
    args: list[str],
    *,
    settings: TransformSettings,
    root: Path,
    environment: Mapping[str, str | None] | None = None,
) -> tuple[dict | None, Invocation]:
    """Invoke the CLI with ``--json`` and return the parsed report and process."""

    invocation = run_cli(
        binary, ["--json", *args], settings=settings, root=root, environment=environment
    )
    return invocation.json(), invocation


def _download(url: str, timeout: float) -> bytes:
    # reason: the URL is a fixed GitHub release asset built by this package.
    with urlopen(url, timeout=timeout) as response:  # noqa: S310 - fixed GitHub release URL
        return response.read()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_extract(archive: Path, destination: Path) -> list[Path]:
    """Extract a release archive without path traversal and return members."""

    extracted: list[Path] = []
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar.getmembers():
            target = (destination / member.name).resolve()
            if not target.is_relative_to(destination.resolve()):
                raise ValueError(f"archive member escapes the destination: {member.name}")
            if not member.isfile():
                continue
            tar.extract(member, path=destination, filter="data")
            extracted.append(target)
    return extracted


def install_binary(
    project_root: Path,
    settings: TransformSettings,
    *,
    version: str | None = None,
    force: bool = False,
) -> Path:
    """Download the pinned release binary into ``.phlo/bin`` and return it."""

    destination = project_root / ".phlo" / "bin"
    destination.mkdir(parents=True, exist_ok=True)
    target_path = destination / binary_name()
    if target_path.exists() and not force:
        return target_path

    release = version or settings.release_version
    target = release_target()
    archive_url = f"{RELEASE_BASE_URL}/v{release}/{BINARY_NAME}-{target}.tar.gz"
    archive_bytes = _download(archive_url, timeout=max(settings.timeout_seconds, 60.0))
    checksum_bytes = _download(f"{archive_url}.sha256", timeout=max(settings.timeout_seconds, 60.0))
    expected = checksum_bytes.decode("utf-8").split()[0].strip().lower()
    actual = _sha256(archive_bytes)
    if actual != expected:
        raise PhloCapabilitySetupError(
            capability="transform",
            required=True,
            message=(
                f"phlo-transform {release} checksum mismatch for {target}: "
                f"expected {expected}, got {actual}"
            ),
            suggestions=[
                "Retry phlo transform install",
                "Pin a different PHLO_TRANSFORM_RELEASE_VERSION",
            ],
        )

    with tempfile.TemporaryDirectory(dir=destination) as temporary:
        archive = Path(temporary) / "release.tar.gz"
        archive.write_bytes(archive_bytes)
        extracted = _safe_extract(archive, Path(temporary))
        candidate = next(
            (path for path in extracted if path.name in {BINARY_NAME, f"{BINARY_NAME}.exe"}),
            None,
        )
        if candidate is None:
            raise PhloCapabilitySetupError(
                capability="transform",
                required=True,
                message=f"release archive {archive_url} does not contain {BINARY_NAME}",
                suggestions=["Report the broken release or pin another version"],
            )
        staged = destination / f".{binary_name()}.tmp"
        shutil.copyfile(candidate, staged)
    staged.chmod(0o755)
    staged.replace(target_path)
    logger.info(
        "phlo_transform_binary_installed",
        version=release,
        target=target,
        path=str(target_path),
    )
    return target_path


__all__ = [
    "BINARY_NAME",
    "RELEASE_BASE_URL",
    "Invocation",
    "binary_name",
    "binary_version",
    "find_binary",
    "install_binary",
    "release_target",
    "require_binary",
    "run_cli",
    "run_json",
]
