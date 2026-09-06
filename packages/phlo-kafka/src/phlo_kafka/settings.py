"""Kafka settings resolved from the project environment."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from phlo.config.base import BaseConfig
from phlo.config.cache import project_root_cached
from phlo.config.network import resolve_host
from pydantic import Field


class KafkaSettings(BaseConfig):
    """Settings for the Phlo Kafka service and consumer assets."""

    kafka_host: str = Field(default="kafka", description="Kafka broker host")
    kafka_port: int = Field(default=9092, description="Kafka broker port")
    kafka_consumer_group_prefix: str = Field(
        default="phlo", description="Prefix applied to consumer group ids"
    )
    kafka_dead_letter_suffix: str = Field(
        default=".dlq", description="Suffix for dead-letter topics"
    )
    kafka_checkpoint_topic: str = Field(
        default="phlo-ingestion-checkpoints",
        description="Compacted topic mirroring durable ingestion checkpoints",
    )
    kafka_retention_ms: int = Field(
        default=604800000, description="Source topic retention in milliseconds (default 7d)"
    )
    kafka_dead_letter_retention_ms: int = Field(
        default=2592000000,
        description="Dead-letter topic retention in milliseconds (default 30d)",
    )
    kafka_schema_policy: str = Field(
        default="additive",
        description="Default schema policy: additive compatible changes auto-register",
    )

    def model_post_init(self, __context: Any) -> None:
        host, port = resolve_host(self.kafka_host, self.kafka_port, port_env_var="KAFKA_PORT")
        object.__setattr__(self, "kafka_host", host)
        object.__setattr__(self, "kafka_port", port)

    def bootstrap_servers(self) -> str:
        """Return the bootstrap server list."""
        return f"{self.kafka_host}:{self.kafka_port}"

    def dead_letter_topic(self, source_topic: str) -> str:
        """Return the dead-letter topic name for a source topic."""
        return f"{source_topic}{self.kafka_dead_letter_suffix}"


@project_root_cached
def get_settings(project_root: Path) -> KafkaSettings:
    """Return cached Kafka settings for the selected project root."""
    return KafkaSettings()
