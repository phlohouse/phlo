"""Regression tests for YAML-backed service plugin inheritance.

Parametrized over every shipped YAML-only plugin class; each must inherit the
package YAML loader base and resolve its packaged service definition under
its expected service name.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from phlo_api.plugin import PhloApiServicePlugin
from phlo_clickhouse.plugin import ClickHouseServicePlugin, ClickHouseSetupServicePlugin
from phlo_dagster.plugin import DagsterDaemonServicePlugin, DagsterServicePlugin
from phlo_hasura.plugin import HasuraServicePlugin
from phlo_minio.plugin import MinioServicePlugin, MinioSetupServicePlugin
from phlo_observatory.plugin import ObservatoryServicePlugin
from phlo_postgrest.plugin import PostgrestServicePlugin
from phlo_prometheus.plugin import PrometheusServicePlugin
from phlo_rustfs.plugin import RustfsServicePlugin, RustfsSetupServicePlugin
from phlo_superset.plugin import SupersetServicePlugin

from phlo.plugins import PackageYamlServicePlugin

pytestmark = pytest.mark.core_regression


@pytest.mark.parametrize(
    ("plugin_class", "service_name"),
    [
        (PhloApiServicePlugin, "phlo-api"),
        (ClickHouseServicePlugin, "clickhouse"),
        (ClickHouseSetupServicePlugin, "clickhouse-setup"),
        (DagsterServicePlugin, "dagster"),
        (DagsterDaemonServicePlugin, "dagster-daemon"),
        (HasuraServicePlugin, "hasura"),
        (MinioServicePlugin, "minio"),
        (MinioSetupServicePlugin, "minio-setup"),
        (ObservatoryServicePlugin, "observatory"),
        (PostgrestServicePlugin, "postgrest"),
        (PrometheusServicePlugin, "prometheus"),
        (RustfsServicePlugin, "rustfs"),
        (RustfsSetupServicePlugin, "rustfs-setup"),
        (SupersetServicePlugin, "superset"),
    ],
)
def test_yaml_backed_service_plugins_use_package_yaml_base(
    plugin_class: type[PackageYamlServicePlugin],
    service_name: str,
) -> None:
    """YAML-only service plugins share the package YAML loader."""
    plugin = plugin_class()

    assert isinstance(plugin, PackageYamlServicePlugin)
    assert plugin.service_definition["name"] == service_name


def test_package_yaml_reads_share_the_manifest_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from phlo.plugins.base.service import service_plugin_class
    from phlo.plugins.discovery._service_definition import ServiceDefinition

    package = tmp_path / "cached_service_fixture"
    package.mkdir()
    (package / "__init__.py").write_text("")
    manifest = package / "service.yaml"
    manifest.write_text("name: cached\ndescription: Cached\ncompose:\n  ports: [1234]\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    plugin_class = service_plugin_class(
        "CachedPlugin",
        name="cached",
        version="1.0.0",
        description="Cached",
        service_definition_package="cached_service_fixture",
    )
    original_open = Path.open
    reads: list[Path] = []

    def open_file(path: Path, *args, **kwargs):
        reads.append(path)
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", open_file)
    try:
        plugin = plugin_class()
        first = plugin.service_definition
        first["compose"]["ports"].append(5678)
        assert plugin.service_definition["compose"] == {"ports": [1234]}
        assert ServiceDefinition.from_yaml(manifest).compose == {"ports": [1234]}
        assert reads == [manifest]
    finally:
        sys.modules.pop("cached_service_fixture", None)
