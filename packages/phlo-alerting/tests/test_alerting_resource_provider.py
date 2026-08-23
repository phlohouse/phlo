"""Tests for the phlo-alerting resource provider.

Verifies the provider-neutral surface of AlertingResourceProvider: it
registers exactly one alert sink named "alerting" that exposes send_alert.
"""

from __future__ import annotations

from phlo_alerting.resource_provider import AlertingResourceProvider


def test_alerting_resource_provider_exposes_alert_sink() -> None:
    """Alerting package should register a neutral alert sink."""
    provider = AlertingResourceProvider()

    specs = provider.get_alert_sinks()

    assert [spec.name for spec in specs] == ["alerting"]
    assert hasattr(specs[0].provider, "send_alert")
