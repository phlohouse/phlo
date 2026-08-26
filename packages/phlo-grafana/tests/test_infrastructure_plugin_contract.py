"""Cross-package contract for infrastructure service plugins.

Per-plugin smoke tests live beside each package (test_integration_<name>.py);
this suite keeps only the shared assertions those files do not make: every
infrastructure plugin carries named, versioned metadata and a service
definition whose name resolves and whose compose fragment is a mapping.
"""

import pytest

pytestmark = pytest.mark.integration

INFRASTRUCTURE_PLUGINS = [
    ("phlo_grafana.plugin", "GrafanaServicePlugin", "grafana"),
    ("phlo_loki.plugin", "LokiServicePlugin", "loki"),
    ("phlo_prometheus.plugin", "PrometheusServicePlugin", "prometheus"),
    ("phlo_hasura.plugin", "HasuraServicePlugin", "hasura"),
    ("phlo_postgrest.plugin", "PostgrestServicePlugin", "postgrest"),
    ("phlo_pgweb.plugin", "PgwebServicePlugin", "pgweb"),
    ("phlo_alloy.plugin", "AlloyServicePlugin", "alloy"),
    ("phlo_superset.plugin", "SupersetServicePlugin", "superset"),
]


@pytest.mark.parametrize(("module_name", "class_name", "expected_name"), INFRASTRUCTURE_PLUGINS)
def test_infrastructure_plugin_contract(module_name, class_name, expected_name):
    """Every infra plugin exposes versioned metadata and a compose-bearing definition."""
    from importlib import import_module

    plugin = getattr(import_module(module_name), class_name)()

    assert plugin.metadata.name == expected_name
    assert plugin.metadata.version
    service_def = plugin.service_definition

    assert service_def.get("name") == expected_name
    assert isinstance(service_def.get("compose"), dict)
