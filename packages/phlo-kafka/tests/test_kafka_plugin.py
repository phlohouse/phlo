"""Tests for the Kafka service definition and plugin registrations."""

from __future__ import annotations

from phlo_kafka.plugin import KafkaAssetProvider, KafkaIngestionProvider, KafkaServicePlugin
from phlo_kafka.resource_provider import KafkaResourceProvider


def test_service_definition_is_digest_pinned_and_kraft() -> None:
    plugin = KafkaServicePlugin()
    definition = plugin.service_definition
    assert definition["name"] == "kafka"
    assert definition["default"] is False
    assert definition["image"].startswith("apache/kafka:4.2.1@sha256:")
    environment = definition["compose"]["environment"]
    assert environment["KAFKA_PROCESS_ROLES"] == "broker,controller"
    assert "KAFKA_CONTROLLER_QUORUM_VOTERS" in environment


def test_resource_provider_exposes_kafka_client() -> None:
    provider = KafkaResourceProvider()
    resources = provider.get_resources()
    assert resources[0].name == "kafka"


def test_ingestion_provider_wires_decorator_and_retriever() -> None:
    provider = KafkaIngestionProvider()
    assert callable(provider.get_decorator())
    assert callable(provider.get_asset_retriever())


def test_asset_provider_starts_empty() -> None:
    provider = KafkaAssetProvider()
    provider.clear_registries()
    assert list(provider.get_assets()) == []
