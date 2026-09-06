"""Phlo Kafka ingestion package.

Provides the KRaft-mode Kafka broker service, the checkpoint-driven consumer
asset lifecycle (claim offset range → staged candidate → audit → promote →
commit), schema policy enforcement with dead-letter routing, and topic
administration.
"""

from importlib.metadata import version

from phlo_kafka.plugin import KafkaServicePlugin

__all__ = ["KafkaServicePlugin"]
__version__ = version("phlo-kafka")
