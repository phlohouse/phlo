"""Kafka client resource with lazy ``confluent-kafka`` import.

The Kafka client is optional infrastructure: imports resolve only when a
consumer asset actually runs, so unit tests and plugin discovery never
require the compiled dependency. Install with ``pip install phlo-kafka[consumer]``.
"""

from __future__ import annotations

import json
from typing import Any

from phlo.capabilities.interfaces import SourceOffsetRange
from phlo.logging import get_logger
from phlo_kafka.settings import get_settings

_CONSUME_POLL_TIMEOUT_SECONDS = 5.0

logger = get_logger(__name__)


class KafkaResource:
    """Consumer/producer/admin facade over confluent-kafka."""

    def __init__(self, bootstrap_servers: str | None = None) -> None:
        self._bootstrap_servers = bootstrap_servers

    @property
    def bootstrap_servers(self) -> str:
        if self._bootstrap_servers is None:
            self._bootstrap_servers = get_settings().bootstrap_servers()
        return self._bootstrap_servers

    def _require_confluent(self) -> Any:
        try:
            import confluent_kafka  # ty: ignore[unresolved-import]  # noqa: PLC0415
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise RuntimeError(
                "confluent-kafka is required for Kafka ingestion; install phlo-kafka[consumer]"
            ) from exc
        return confluent_kafka

    def consumer(self, *, group_id: str, auto_offset_reset: str = "earliest") -> Any:
        """Create a consumer with manual commit semantics."""
        confluent_kafka = self._require_confluent()
        return confluent_kafka.Consumer(
            {
                "bootstrap.servers": self.bootstrap_servers,
                "group.id": group_id,
                "auto.offset.reset": auto_offset_reset,
                "enable.auto.commit": False,
            }
        )

    def producer(self) -> Any:
        """Create a producer for dead-letter routing."""
        confluent_kafka = self._require_confluent()
        return confluent_kafka.Producer({"bootstrap.servers": self.bootstrap_servers})

    def consume(
        self,
        *,
        topic_pattern: str,
        group_id: str,
        max_records: int,
    ) -> tuple[list[dict[str, Any]], list[SourceOffsetRange]]:
        """Poll one batch of records and return them with their offset ranges.

        Offsets are never committed here: the consumer asset commits only
        after the checkpoint is audited and promoted.
        """
        self._require_confluent()
        consumer = self.consumer(group_id=group_id)
        consumer.subscribe([topic_pattern])
        records: list[dict[str, Any]] = []
        ranges: dict[tuple[str, int], SourceOffsetRange] = {}
        try:
            while len(records) < max_records:
                message = consumer.poll(timeout=_CONSUME_POLL_TIMEOUT_SECONDS)
                if message is None:
                    break
                if message.error():
                    continue
                value = message.value()
                record = json.loads(value.decode("utf-8")) if isinstance(value, bytes) else value
                records.append(record)
                key = (message.topic(), message.partition())
                offset = message.offset()
                existing = ranges.get(key)
                if existing is None:
                    ranges[key] = SourceOffsetRange(
                        topic=message.topic(),
                        partition=message.partition(),
                        start_offset=offset,
                        end_offset=offset + 1,
                    )
                else:
                    ranges[key] = SourceOffsetRange(
                        topic=existing.topic,
                        partition=existing.partition,
                        start_offset=existing.start_offset,
                        end_offset=offset + 1,
                    )
        finally:
            consumer.close()
        return records, list(ranges.values())

    def commit_offsets(self, ranges: list[SourceOffsetRange], *, group_id: str) -> None:
        """Commit consumed consumer-group offsets after checkpoint promotion."""
        self._require_confluent()
        from confluent_kafka import TopicPartition  # ty: ignore[unresolved-import]

        consumer = self.consumer(group_id=group_id)
        try:
            consumer.commit(
                offsets=[
                    TopicPartition(item.topic, item.partition, item.end_offset) for item in ranges
                ],
                asynchronous=False,
            )
        finally:
            consumer.close()

    def dead_letter_sink(self, topic: str, records: list[dict[str, Any]]) -> int:
        """Produce records to a dead-letter topic; returns the produced count."""
        producer = self.producer()
        for record in records:
            producer.produce(topic, value=json.dumps(record, default=str).encode("utf-8"))
        producer.flush(timeout=30)
        return len(records)

    def health_check(self) -> bool:
        """Return whether the broker responds to metadata requests."""
        try:
            admin = self._require_confluent().AdminClient(
                {"bootstrap.servers": self.bootstrap_servers}
            )
            metadata = admin.list_topics(timeout=10)
            return bool(metadata.topics is not None)
        except Exception:
            logger.warning("kafka_health_check_failed", exc_info=True)
            return False
