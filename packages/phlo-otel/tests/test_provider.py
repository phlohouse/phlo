"""Tests for the OTel provider resource configuration.

This module contains tests for the provider module, covering:
- Resource attribute construction from environment variables and settings
- Signal export enablement logic
- Provider lifecycle management (initialization and shutdown)
- Log emitter behavior with cached and disabled providers
"""

from __future__ import annotations

from types import SimpleNamespace

from phlo_otel import provider


def test_build_resource_attributes(monkeypatch):
    """Test resource attributes use OTEL_* environment variables when set.
    Verifies that OTEL_SERVICE_NAME, OTEL_SERVICE_NAMESPACE, etc. take
    precedence over Phlo configuration settings.
    """
    monkeypatch.setenv("OTEL_SERVICE_NAME", "phlo-api")
    monkeypatch.setenv("OTEL_SERVICE_NAMESPACE", "phlohouse")
    monkeypatch.setenv("OTEL_SERVICE_VERSION", "1.2.3")
    monkeypatch.setenv("OTEL_SERVICE_INSTANCE_ID", "instance-7")
    monkeypatch.setenv("PHLO_PROJECT", "demo-lakehouse")
    monkeypatch.setattr(
        "phlo_otel.provider.get_settings",
        lambda: SimpleNamespace(
            phlo_environment="prod",
            phlo_log_service_name="ignored",
            phlo_service_namespace="ignored-namespace",
            phlo_service_version="ignored-version",
            phlo_service_instance_id="ignored-instance",
            phlo_project="ignored-project",
        ),
    )

    resource_attributes = provider._build_resource_attributes()

    assert resource_attributes["service.name"] == "phlo-api"
    assert resource_attributes["service.namespace"] == "phlohouse"
    assert resource_attributes["service.version"] == "1.2.3"
    assert resource_attributes["service.instance.id"] == "instance-7"
    assert resource_attributes["deployment.environment"] == "prod"
    assert resource_attributes["phlo.package"] == "phlo-otel"
    assert resource_attributes["phlo.runtime"] == "python"
    assert resource_attributes["phlo.project"] == "demo-lakehouse"


def test_build_resource_attributes_uses_phlo_defaults(monkeypatch):
    """Test resource attributes fall back to Phlo defaults when OTEL_* unset.
    Verifies that Phlo configuration settings are used when environment
    variables are not set, with appropriate defaults for hostname and version.
    """
    monkeypatch.delenv("OTEL_SERVICE_NAME", raising=False)
    monkeypatch.delenv("OTEL_SERVICE_NAMESPACE", raising=False)
    monkeypatch.delenv("OTEL_SERVICE_VERSION", raising=False)
    monkeypatch.delenv("OTEL_SERVICE_INSTANCE_ID", raising=False)
    monkeypatch.delenv("PHLO_PROJECT", raising=False)
    monkeypatch.setattr(
        "phlo_otel.provider.get_settings",
        lambda: SimpleNamespace(
            phlo_environment="dev",
            phlo_log_service_name="phlo-worker",
            phlo_service_namespace="phlo",
            phlo_service_version=None,
            phlo_service_instance_id=None,
            phlo_project=None,
        ),
    )
    monkeypatch.setattr("phlo_otel.provider.socket.gethostname", lambda: "worker-host")

    resource_attributes = provider._build_resource_attributes()

    assert resource_attributes["service.name"] == "phlo-worker"
    assert resource_attributes["service.namespace"] == "phlo"
    assert resource_attributes["service.version"] == provider.INSTRUMENTATION_VERSION
    assert resource_attributes["service.instance.id"] == "worker-host"
    assert resource_attributes["deployment.environment"] == "dev"
    assert resource_attributes["phlo.project"] == "phlo-worker"


def test_build_resource_attributes_uses_phlo_observability_settings(monkeypatch):
    """Test resource attributes respect Phlo observability settings.
    Verifies that all Phlo-specific settings are properly mapped to resource
    attributes when OTEL_* environment variables are not present.
    """
    monkeypatch.delenv("OTEL_SERVICE_NAME", raising=False)
    monkeypatch.delenv("OTEL_SERVICE_NAMESPACE", raising=False)
    monkeypatch.delenv("OTEL_SERVICE_VERSION", raising=False)
    monkeypatch.delenv("OTEL_SERVICE_INSTANCE_ID", raising=False)
    monkeypatch.delenv("PHLO_PROJECT", raising=False)
    monkeypatch.setattr(
        "phlo_otel.provider.get_settings",
        lambda: SimpleNamespace(
            phlo_environment="staging",
            phlo_log_service_name="phlo-api",
            phlo_service_namespace="lakehouse",
            phlo_service_version="2.4.0",
            phlo_service_instance_id="api-7",
            phlo_project="acme-analytics",
        ),
    )

    resource_attributes = provider._build_resource_attributes()

    assert resource_attributes["service.name"] == "phlo-api"
    assert resource_attributes["service.namespace"] == "lakehouse"
    assert resource_attributes["service.version"] == "2.4.0"
    assert resource_attributes["service.instance.id"] == "api-7"
    assert resource_attributes["deployment.environment"] == "staging"
    assert resource_attributes["phlo.project"] == "acme-analytics"


def test_get_log_emitter_uses_cached_logger_provider(monkeypatch):
    """Test get_log_emitter returns cached logger when available."""
    provider._initialized = True
    fake_logger = object()
    fake_provider = SimpleNamespace(get_logger=lambda name, version: fake_logger)
    monkeypatch.setattr(provider, "_logger_provider", fake_provider)

    assert provider.get_log_emitter() is fake_logger


def test_get_log_emitter_returns_none_when_logs_disabled(monkeypatch):
    """Test get_log_emitter returns None when log export is disabled."""
    monkeypatch.setattr(provider, "_initialized", True)
    monkeypatch.setattr(provider, "_logger_provider", None)

    assert provider.get_log_emitter() is None


def test_signal_export_enabled_defaults_off_without_endpoint(monkeypatch):
    """Test trace export defaults to disabled without OTLP endpoint.
    Verifies that traces are not exported by default when no OTLP endpoint
    is configured and OTEL_TRACES_EXPORTER is not explicitly set.
    """
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", raising=False)
    monkeypatch.delenv("OTEL_TRACES_EXPORTER", raising=False)

    assert provider._traces_export_enabled() is False


def test_signal_export_enabled_uses_endpoint_when_exporter_unset(monkeypatch):
    """Test trace export enabled when OTLP endpoint is configured.
    Verifies that traces are exported when OTEL_EXPORTER_OTLP_ENDPOINT is
    set, even without explicit OTEL_TRACES_EXPORTER setting.
    """
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector:4317")
    monkeypatch.delenv("OTEL_TRACES_EXPORTER", raising=False)

    assert provider._traces_export_enabled() is True


def test_shutdown_otel_resets_cached_providers(monkeypatch):
    """Test shutdown_otel properly shuts down and clears all providers.
    Verifies that shutdown_otel calls shutdown on all providers and
    resets internal state for clean re-initialization.
    """
    calls: list[str] = []

    class FakeProvider:
        """Fake provider that records shutdown calls."""

        def __init__(self, name: str) -> None:
            """Initialize with a name for tracking."""
            self._name = name

        def shutdown(self) -> None:
            """Record shutdown call."""
            calls.append(self._name)

    monkeypatch.setattr(provider, "_initialized", True)
    monkeypatch.setattr(provider, "_logger_provider", FakeProvider("logs"))
    monkeypatch.setattr(provider, "_meter_provider", FakeProvider("metrics"))
    monkeypatch.setattr(provider, "_tracer_provider", FakeProvider("traces"))

    provider.shutdown_otel()

    assert calls == ["logs", "metrics", "traces"]
    assert provider._initialized is False
    assert provider._logger_provider is None
    assert provider._meter_provider is None
    assert provider._tracer_provider is None
