"""Kafka event ingestion declaring the schema policy and unique key.

Registered through the phlo-kafka decorator so the Dagster framework (when
enabled) picks the asset up like any other Phlo workflow; the e2e driver in
scripts/e2e.py exercises the same lifecycle directly against real services.
"""

from phlo_kafka.assets import phlo_kafka_consumer

phlo_kafka_consumer(
    name="events",
    topic_pattern="events",
    destination_table="bronze.events",
    unique_key=["event_id"],
    group="ingestion",
    schema={
        "event_id": "string",
        "user_id": "string",
        "event_type": "string",
        "value": "int",
    },
    dead_letter_topic="events.dlq",
    description="Kafka events landed in Iceberg under Polaris with checkpoint semantics",
)


def get_config():
    """Return the consumer config for direct (script-driven) lifecycles."""
    from phlo_kafka.assets import KafkaConsumerConfig

    return KafkaConsumerConfig(
        name="events",
        group="ingestion",
        topic_pattern="events",
        destination_table="bronze.events",
        unique_key=["event_id"],
        schema={
            "event_id": "string",
            "user_id": "string",
            "event_type": "string",
            "value": "int",
        },
        dead_letter_topic="events.dlq",
    )
