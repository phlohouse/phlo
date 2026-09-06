"""CLI plugin registration for the Kafka package."""

from __future__ import annotations

from phlo.plugins.base import cli_command_plugin_class

from phlo_kafka.cli import kafka_group

KafkaCliPlugin = cli_command_plugin_class(
    "KafkaCliPlugin",
    name="kafka",
    version="0.1.0",
    description="Kafka broker status and topic commands",
    commands=[kafka_group],
)
