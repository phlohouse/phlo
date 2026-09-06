"""Kafka service lifecycle hooks (invoked via ``python -m phlo_kafka.hooks``).

``init-topics`` ensures the compacted checkpoint topic exists. Dead-letter
topics are created per consumer asset on first dead-lettered batch (or by
operations with the ``kafka_dead_letter_retention_ms`` policy); the broker
cannot know asset-derived topic names at startup. The hook is idempotent and
best-effort: a missing broker logs a warning and exits 0 so service startup
is not blocked.
"""

from __future__ import annotations

import argparse
import sys
import time
from typing import Any

from phlo.logging import get_logger

logger = get_logger(__name__)

INIT_TIMEOUT_SECONDS = 60


def ensure_topic(admin: Any, *, topic: str, compacted: bool = False, retention_ms: int) -> bool:
    """Create one topic with the requested policy when absent."""
    if topic in admin.list_topics().topics:
        logger.info("kafka_topic_exists", topic=topic)
        return False
    config = {
        "retention.ms": str(retention_ms),
    }
    if compacted:
        config["cleanup.policy"] = "compact"
    admin.create_topics(
        [
            {
                "topic": topic,
                "num_partitions": 1,
                "replication_factor": 1,
                "config": config,
            }
        ]
    )
    logger.info("kafka_topic_created", topic=topic, compacted=compacted)
    return True


def init_topics(*, resource: Any | None = None, settings: Any | None = None) -> int:
    """Ensure the compacted checkpoint topic exists. Always exits 0 (best effort)."""
    if resource is None:
        from phlo_kafka.resource import KafkaResource

        resource = KafkaResource()
    try:
        confluent_kafka = resource._require_confluent()
    except RuntimeError:
        logger.warning("kafka_topic_init_skipped_missing_consumer_dependency")
        return 0
    if settings is None:
        from phlo_kafka.settings import get_settings

        settings = get_settings()

    deadline = time.monotonic() + INIT_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        try:
            admin = confluent_kafka.AdminClient({"bootstrap.servers": resource.bootstrap_servers})
            checkpoint_topic = settings.kafka_checkpoint_topic
            ensure_topic(
                admin,
                topic=checkpoint_topic,
                compacted=True,
                retention_ms=settings.kafka_retention_ms,
            )
            return 0
        except Exception:
            time.sleep(2)
    logger.warning("kafka_topic_init_unavailable")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="phlo_kafka.hooks")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("init-topics", help="Ensure checkpoint and DLQ topics exist")
    args = parser.parse_args(argv)
    if args.command == "init-topics":
        return init_topics()
    return 1


if __name__ == "__main__":
    sys.exit(main())
