"""Regression tests for YAML-backed service plugin inheritance.

Parametrized over every shipped YAML-only plugin class; each must inherit the
package YAML loader base and resolve its packaged service definition under
its expected service name.
"""

from __future__ import annotations

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
