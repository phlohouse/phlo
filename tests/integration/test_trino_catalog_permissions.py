"""Generated Trino startup supports catalog writes on rootless and rootful Docker.

Replace only the JVM launcher with a filesystem probe in the pinned Trino image.
This exercises real bind-mount ownership and privilege dropping without starting
another query engine or accessing any running project.
"""

import json
import os
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


@pytest.mark.parametrize("host_umask", [0o022, 0o077])
@pytest.mark.parametrize("catalog_mode", [0o755, 0o750])
def test_generated_trino_catalog_permissions(tmp_path: Path, catalog_mode: int, host_umask: int):
    """Trino reads private config and persists catalogs without gaining bootstrap write access."""
    if shutil.which("docker") is None:
        # reason: Bind-mount ownership requires a real Docker runtime.
        pytest.skip("Docker is required")
    info = subprocess.run(["docker", "info"], capture_output=True, timeout=30)
    if info.returncode:
        # reason: The container regression cannot run without a reachable daemon.
        pytest.skip("Docker daemon is unavailable")

    discovery = ServiceDiscovery()
    trino = discovery.get_service("trino")
    assert trino is not None
    generator = ComposeGenerator(discovery)
    previous_umask = os.umask(host_umask)
    try:
        generator.copy_service_files([trino], tmp_path)
    finally:
        os.umask(previous_umask)
    service = yaml.safe_load(generator.generate_compose([trino], tmp_path))["services"]["trino"]
    image = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{json .Config}}", service["image"]],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if image.returncode:
        # reason: Keep this regression offline and bounded to an already cached image.
        pytest.skip(f"Pull {service['image']} before running this container regression")

    catalog = tmp_path / "trino/catalog"
    catalog.chmod(catalog_mode)
    catalog_gid = catalog.stat().st_gid
    # A provider checkout can also have private file modes, regardless of umask at generation.
    for path in (tmp_path / "trino").rglob("*"):
        if path.is_file():
            path.chmod(0o600)
    private_catalog = catalog / "private.properties"
    private_catalog.write_text("connector.name=memory\n", encoding="utf-8")
    private_catalog.chmod(0o600)
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
test "$(stat -c %g /etc/trino)" = 1000
for config in \\
    /etc/trino/config.properties \\
    /etc/trino/jvm.config \\
    /etc/trino/node.properties \\
    /etc/trino/catalog/iceberg.properties \\
    /etc/trino/catalog/iceberg_dev.properties \\
    /etc/trino/catalog/delta.properties
do
    cat "$config" > /dev/null
    test "$(stat -c %g "$config")" = 1000
    test ! -w "$config"
done
test ! -w /etc/trino
test ! -w /etc/trino/entrypoint.sh
test ! -r /etc/trino/catalog/private.properties
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
            persisted = (catalog / "persisted.properties").stat()
            assert stat.S_IMODE(persisted.st_mode) == 0o600
            assert persisted.st_uid == catalog.stat().st_uid
            assert persisted.st_gid == (catalog.parent / "config.properties").stat().st_gid
            for path, (content, metadata) in original_files.items():
                assert path.read_bytes() == content
                current = path.stat()
                assert current.st_uid == metadata.st_uid
                if path.name in {"entrypoint.sh", "private.properties"}:
                    assert (current.st_gid, current.st_mode) == (metadata.st_gid, metadata.st_mode)
                else:
                    assert stat.S_IMODE(current.st_mode) == (
                        (stat.S_IMODE(metadata.st_mode) | 0o040) & ~0o020
                    )
            current_parent = catalog.parent.stat()
            assert current_parent.st_uid == parent_stat.st_uid
            assert stat.S_IMODE(current_parent.st_mode) == (
                (stat.S_IMODE(parent_stat.st_mode) | 0o050) & ~0o022
            )
            # Host-side regeneration must retain catalogs created by the runtime.
            generator.copy_service_files([trino], tmp_path, overwrite=True)
            assert (catalog / "persisted.properties").exists()
            # Regeneration restores provider modes, which vary by checkout umask.
            for path, (content, _metadata) in original_files.items():
                if path != private_catalog:
                    original_files[path] = (content, path.stat())

        # A runtime-writable catalog must not redirect privileged permission changes.
        target = catalog.parent / "root-private.conf"
        target.write_text("private-root-data\n", encoding="utf-8")
        target.chmod(0o600)
        substituted = catalog / "delta.properties"
        substituted.unlink()
        substituted.symlink_to("/etc/trino/root-private.conf")
        before_rejection = {
            path: path.stat()
            for path in [*original_files, target, catalog.parent, catalog]
            if not path.is_symlink()
        }
        rejected = subprocess.run(command, capture_output=True, text=True, timeout=30)
        assert rejected.returncode != 0
        assert "regular file" in rejected.stderr
        for path, metadata in before_rejection.items():
            current = path.stat()
            assert (current.st_uid, current.st_gid, current.st_mode) == (
                metadata.st_uid,
                metadata.st_gid,
                metadata.st_mode,
            )
        assert target.read_text(encoding="utf-8") == "private-root-data\n"
    finally:
        subprocess.run(["docker", "rm", "--force", name], capture_output=True, timeout=30)
