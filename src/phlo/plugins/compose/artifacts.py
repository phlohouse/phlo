"""Path policy shared by runtime artifact export and regeneration."""

from pathlib import Path, PureWindowsPath

_PRIVATE_NAMES = {
    "overrides",
    "secrets",
    "volumes",
    "logs",
    "audit",
    "contracts",
    "wheelhouse",
    "__pycache__",
    "docker-compose.yml",
    "compose.local.yaml",
    "pyproject.toml",
    "uv.lock",
}


def shared_artifact_files(root: Path, relative: str) -> list[Path]:
    """Return explicit regular artifact files without following links or state paths."""
    path = Path(relative)
    windows_path = PureWindowsPath(relative)
    if path.is_absolute() or windows_path.drive or ".." in path.parts or ".." in windows_path.parts:
        raise ValueError(f"Artifact path must be relative: {relative}")
    source = root / path
    if not source.exists():
        raise ValueError(f"Missing artifact: {relative}")
    candidates = [source, *source.rglob("*")] if source.is_dir() else [source]
    files = []
    for candidate in candidates:
        parts = candidate.relative_to(root).parts
        if any(part.startswith(".") or part in _PRIVATE_NAMES for part in parts):
            raise ValueError(f"Private/runtime path cannot be shared: {relative}")
        if any((root / Path(*parts[:index])).is_symlink() for index in range(len(parts) + 1)):
            raise ValueError(f"Symlink cannot be shared: {relative}")
        if candidate.is_file():
            files.append(candidate)
    return files


def render_shared_gitignore(paths: list[str], existing: str = "") -> str:
    """Expose only declared shared files; keep all other runtime state ignored."""
    from phlo.config.layout import SHARED_LAYOUT_MARKER

    end_marker = "# End Phlo shared layout"
    import re

    block_pattern = re.escape(SHARED_LAYOUT_MARKER) + r".*?" + re.escape(end_marker)
    blocks = re.findall(block_pattern, existing, flags=re.DOTALL)
    previous_paths = [
        re.sub(r"\\(.)", r"\1", line[2:])
        for block in blocks
        for line in block.splitlines()
        if line.startswith("!/") and not line.endswith("/")
    ]
    existing = re.sub(block_pattern, "", existing, flags=re.DOTALL)
    lines = (
        [existing.rstrip(), SHARED_LAYOUT_MARKER, "*"]
        if existing.strip()
        else [SHARED_LAYOUT_MARKER, "*"]
    )
    shared = {
        ".gitignore",
        ".gitattributes",
        "docker-compose.yml",
        "compose.shared.yaml",
        "compose.windows.yaml",
        "compose.linux.yaml",
        "compose.macos.yaml",
        *paths,
        *previous_paths,
    }
    parents: set[str] = set()
    for name in sorted(shared):
        path = Path(name)
        if path.is_absolute() or ".." in path.parts or "\n" in name or "\r" in name:
            raise ValueError(f"Invalid shared artifact path: {name}")
        for parent in reversed(path.parents):
            if str(parent) != "." and parent.as_posix() not in parents:
                escaped = _ignore_literal(parent.as_posix())
                lines.append(f"!/{escaped}/")
                parents.add(parent.as_posix())
        lines.append(f"!/{_ignore_literal(path.as_posix())}")
    lines.extend(
        [
            "/overrides/",
            "/secrets/",
            "/volumes/",
            "/logs/",
            "/audit/",
            "/contracts/",
            "/wheelhouse/",
            "/pyproject.toml",
            "/uv.lock",
            ".env",
            ".env.*",
            end_marker,
            "",
        ]
    )
    return "\n".join(lines)


def _ignore_literal(path: str) -> str:
    """Quote Git glob metacharacters so the allowlist names literal files."""
    return "".join("\\" + char if char in "\\*?[] " else char for char in path)


def split_host_compose(compose: dict) -> tuple[dict, dict]:
    """Separate generated host identity and local Phlo source mounts."""
    import re
    from copy import deepcopy

    shared = deepcopy(compose)
    local: dict = {"services": {}}
    for name, config in shared.get("services", {}).items():
        if not isinstance(config, dict):
            continue
        changes: dict = {}
        environment = config.get("environment", {})
        keys = {"PHLO_DEV_MODE"}
        if name in {"dagster", "dagster-daemon"}:
            keys.update({"PHLO_RUNTIME_UID", "PHLO_RUNTIME_GID"})
        if isinstance(environment, dict):
            identities = {key: environment.pop(key) for key in keys if key in environment}
            if "PHLO_RUNTIME_UID" in identities and environment.get("HOME") == "/opt/dagster":
                identities["HOME"] = environment.pop("HOME")
        elif isinstance(environment, list):
            identities = {}
            for item in environment:
                if isinstance(item, str) and item.split("=", 1)[0] in keys:
                    key, _, value = item.partition("=")
                    identities[key] = value
            config["environment"] = [
                item
                for item in environment
                if not isinstance(item, str) or item.split("=", 1)[0] not in keys
            ]
        else:
            identities = {}
        if identities:
            changes["environment"] = identities
        mounts = config.get("volumes", [])
        local_mounts = [
            mount
            for mount in mounts
            if (isinstance(mount, dict) and mount.get("target") == "/opt/phlo-dev")
            or (isinstance(mount, str) and "/opt/phlo-dev" in mount.split(":")[1:])
        ]
        if local_mounts:
            changes["volumes"] = local_mounts
            config["volumes"] = [mount for mount in mounts if mount not in local_mounts]
        if name == "phlo-api" and re.fullmatch(r"\d+:\d+", str(config.get("user", ""))):
            changes["user"] = config.pop("user")
        if changes:
            local["services"][name] = changes
    return shared, local


def compose_changes(shared: dict, local: dict) -> dict:
    """Render changed fields for the generated, ignored host override."""
    changes = {}
    for key, value in local.items():
        if key not in shared:
            changes[key] = value
        elif isinstance(value, dict) and isinstance(shared[key], dict):
            nested = compose_changes(shared[key], value)
            if nested:
                changes[key] = nested
        elif value != shared[key]:
            changes[key] = value
    # Dev mode removes an image while retaining its build; Compose accepts an
    # empty image and uses the local project/service image built from that context.
    if "image" in shared and "image" not in local:
        changes["image"] = ""
    return changes


def write_compose_layers(
    output_dir: Path,
    rendered: str,
    *,
    portable_rendered: str | None = None,
    preserve_shared: bool = False,
    production: bool = False,
) -> None:
    """Write a portable base and an ignored overlay without moving build contexts."""
    import yaml

    from phlo.config.layout import SHARED_LAYOUT_MARKER

    marker = output_dir / ".gitignore"
    base = output_dir / "docker-compose.yml"
    if not marker.exists() or SHARED_LAYOUT_MARKER not in marker.read_text():
        base.write_text(rendered)
        return
    host_path = output_dir / "overrides" / "compose.host.yaml"
    if production:
        if not preserve_shared or not base.exists():
            base.write_text(rendered)
        # This file is generated by Phlo; personal overrides remain separate.
        host_path.unlink(missing_ok=True)
        return
    shared, _ = split_host_compose(yaml.safe_load(portable_rendered or rendered))
    local = yaml.safe_load(rendered)
    changes = compose_changes(shared, local)
    if not preserve_shared or not base.exists():
        base.write_text(
            "# Phlo shared Compose configuration\n# Dev mode: false\n"
            + yaml.safe_dump(shared, sort_keys=False)
        )
    host_path.parent.mkdir(parents=True, exist_ok=True)
    if changes:
        header = "# Generated host overrides; put personal changes in compose.yaml.\n"
        host_path.write_text(header + yaml.safe_dump(changes, sort_keys=False))
    else:
        host_path.unlink(missing_ok=True)
