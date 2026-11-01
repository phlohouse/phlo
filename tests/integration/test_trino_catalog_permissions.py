"""Generated Trino startup supports catalog writes on rootless and rootful Docker.

Replace only the JVM launcher with a filesystem probe in the pinned Trino image.
This exercises real bind-mount ownership and privilege dropping without starting
another query engine or accessing any running project.
"""

import json
import shutil
import stat
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest
import yaml

from phlo.plugins.compose.generator import ComposeGenerator
from phlo.plugins.discovery import ServiceDiscovery

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("catalog_mode", [0o755, 0o750])
def test_generated_trino_catalog_permissions(tmp_path: Path, catalog_mode: int):
    """Trino can persist catalogs as UID 1000 without changing existing files."""
    if shutil.which("docker") is None:
        pytest.skip("Docker is required")
    info = subprocess.run(["docker", "info"], capture_output=True, timeout=30)
    if info.returncode:
        pytest.skip("Docker daemon is unavailable")

    discovery = ServiceDiscovery()
    trino = discovery.get_service("trino")
    assert trino is not None
    generator = ComposeGenerator(discovery)
    generator.copy_service_files([trino], tmp_path)
    service = yaml.safe_load(generator.generate_compose([trino], tmp_path))["services"]["trino"]
    image = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{json .Config}}", service["image"]],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if image.returncode:
        pytest.skip(f"Pull {service['image']} before running this container regression")

    # Isolate catalog ownership from the checkout/account's config-directory umask.
    (tmp_path / "trino").chmod(0o755)
    catalog = tmp_path / "trino/catalog"
    catalog.chmod(catalog_mode)
    catalog_gid = catalog.stat().st_gid
    original_files = {
        path: (path.read_bytes(), path.stat())
        for path in (tmp_path / "trino").rglob("*")
        if path.is_file()
    }
    parent_stat = (tmp_path / "trino").stat()
    probe = tmp_path / "launcher-probe.sh"
    probe.write_text(
        """#!/bin/sh
set -eu
test "$(id -u)" = 1000
test "$(id -g)" = 1000
test "$(id -G)" = 1000
test "$$" = 1
cd /etc/trino/catalog
printf 'connector.name=memory\n' > permission-probe.properties.tmp
mv permission-probe.properties.tmp permission-probe.properties
test "$(cat permission-probe.properties)" = 'connector.name=memory'
rm permission-probe.properties
if [ -e persisted.properties ]; then
    test "$(cat persisted.properties)" = 'connector.name=memory'
else
    (umask 077; printf 'connector.name=memory\n' > persisted.properties)
fi
""",
        encoding="utf-8",
    )
    probe.chmod(0o755)
    launcher = json.loads(image.stdout)["Cmd"][0]
    name = f"phlo-trino-permissions-{uuid4().hex}"
    command = [
        "docker",
        "run",
        "--rm",
        "--name",
        name,
        "--network",
        "none",
        "--memory",
        "64m",
        "--cpus",
        "0.1",
        "--volume",
        f"{probe}:{launcher}:ro",
    ]
    for mount in service["volumes"]:
        source, target, mode = mount.rsplit(":", 2)
        command.extend(["--volume", f"{tmp_path / source}:{target}:{mode}"])
    if "user" in service:
        command.extend(["--user", service["user"]])
    entrypoint = service.get("entrypoint", [])
    if entrypoint:
        command.extend(["--entrypoint", entrypoint[0]])
    command.extend([service["image"], *entrypoint[1:], *service.get("command", [])])
    try:
        for _ in range(2):
            result = subprocess.run(command, capture_output=True, text=True, timeout=30)
            assert result.returncode == 0, result.stdout + result.stderr
            assert catalog.stat().st_gid == catalog_gid
            assert stat.S_IMODE(catalog.stat().st_mode) == (catalog_mode | 0o070)
            for path, (content, metadata) in original_files.items():
                assert path.read_bytes() == content
                current = path.stat()
                assert (current.st_uid, current.st_gid, current.st_mode) == (
                    metadata.st_uid,
                    metadata.st_gid,
                    metadata.st_mode,
                )
            current_parent = catalog.parent.stat()
            assert (current_parent.st_uid, current_parent.st_gid, current_parent.st_mode) == (
                parent_stat.st_uid,
                parent_stat.st_gid,
                parent_stat.st_mode,
            )
            # Host-side regeneration must retain catalogs created by the runtime.
            generator.copy_service_files([trino], tmp_path, overwrite=True)
            assert (catalog / "persisted.properties").exists()
    finally:
        subprocess.run(["docker", "rm", "--force", name], capture_output=True, timeout=30)
