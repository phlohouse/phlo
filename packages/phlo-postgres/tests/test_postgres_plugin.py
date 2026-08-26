"""Tests for Postgres service and resource plugins.

Pins digest-pinned upstream images for the server and exporter, the
volume-setup guard that refuses pre-18 data layouts, and the resource
provider's single postgres resource plus serving-role publish target.
"""

import shlex
import subprocess

from phlo.capabilities import PublishTargetSpec
from phlo_postgres.plugin import (
    PostgresExporterServicePlugin,
    PostgresResourceProvider,
    PostgresServicePlugin,
    PostgresVolumeSetupServicePlugin,
    pre_18_volume_guard,
    volume_setup_command,
)
from phlo_postgres.publish_target import PostgresPublishTarget


def test_postgres_service_definition():
    """Validate Postgres service definition fields."""
    plugin = PostgresServicePlugin()
    service_definition = plugin.service_definition

    assert service_definition["name"] == "postgres"
    assert service_definition["category"] == "core"


def test_postgres_service_uses_pinned_upstream_image() -> None:
    service_definition = PostgresServicePlugin().service_definition

    assert service_definition["image"] == (
        "postgres:18.4-alpine3.24@"
        "sha256:9a8afca54e7861fd90fab5fdf4c42477a6b1cb7d293595148e674e0a3181de15"
    )
    assert "build" not in service_definition


def _run_pre_18_volume_guard(data_dir) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/bin/sh", "-c", pre_18_volume_guard(str(data_dir))],
        capture_output=True,
        text=True,
        check=False,
    )


def test_pre_18_volume_guard_exits_1_when_pg_version_present(tmp_path) -> None:
    (tmp_path / "PG_VERSION").write_text("16\n")

    result = _run_pre_18_volume_guard(tmp_path)

    assert result.returncode == 1
    assert result.stderr.startswith("PostgreSQL 16 data volume detected")


def test_pre_18_volume_guard_proceeds_on_fresh_volume(tmp_path) -> None:
    result = _run_pre_18_volume_guard(tmp_path)

    assert result.returncode == 0
    assert result.stderr == ""


def test_postgres_volume_setup_runs_guard_before_volume_writes() -> None:
    command = PostgresVolumeSetupServicePlugin().service_definition["compose"]["command"]

    tokens = shlex.split(command)
    assert tokens[0] == "-c"
    script = tokens[1]

    guard = pre_18_volume_guard()
    init_steps = script.removeprefix(f"{guard} && ").split(" && ")
    data_dir = shlex.quote("/var/lib/postgresql")
    assert init_steps == [
        f"mkdir -p {data_dir}",
        f"chown -R 70:70 {data_dir}",
        f"chmod 700 {data_dir}",
        "echo 'Postgres data volume ownership initialized'",
    ]
    assert volume_setup_command() == command


def test_postgres_exporter_uses_pinned_upstream_image() -> None:
    service_definition = PostgresExporterServicePlugin().service_definition

    assert service_definition["image"] == (
        "quay.io/prometheuscommunity/postgres-exporter:v0.20.1@"
        "sha256:ac5ec343104fae0e2d84a27bb8d69b38430a11910c5382cad85d478d2bab713e"
    )
    assert "build" not in service_definition


def test_postgres_resource_provider():
    """Validate Postgres resource provider output."""
    provider = PostgresResourceProvider()
    resources = provider.get_resources()

    assert len(resources) == 1
    assert resources[0].name == "postgres"


def test_postgres_resource_provider_exposes_publish_target() -> None:
    provider = PostgresResourceProvider()
    publish_targets = provider.get_publish_targets()

    assert publish_targets == [
        PublishTargetSpec(
            name="postgres",
            provider=PostgresPublishTarget(),
            metadata={"target_system": "postgres", "role": "serving"},
        )
    ]
