# phlo-kafka

Kafka ingestion service plugin for Phlo.

## Overview

`phlo-kafka` runs a pinned KRaft-mode Apache Kafka broker and provides
checkpoint-driven consumer assets. Kafka delivery is at-least-once; Iceberg
results are effectively-once: consumed batches claim offset ranges in the
durable ingestion checkpoint store (Phlo Postgres), land through an idempotent
merge on a declared unique key, record the output snapshot, and commit only
after audit and promotion. Schema policy enforcement dead-letters incompatible
changes while retaining source offsets uncommitted.

### Key features

- Digest-pinned `apache/kafka` KRaft broker with health checks and topic hooks
- `phlo_kafka_consumer` asset decorator (topic, group, unique key, DLQ, schema policy)
- Claim→stage→audit→promote→commit lifecycle over the shared checkpoint contract
- Dead-letter topics with explicit retention; compacted checkpoint topic
- Lineage from topic/partition/offset ranges to the exact output snapshot

## Installation

```bash
pip install phlo-kafka
pip install "phlo-kafka[consumer]"  # with confluent-kafka
```

## Configuration

| Variable | Default | Description |
| -------- | ------- | ----------- |
| `KAFKA_PORT` | `10021` | Kafka broker host port |
| `KAFKA_RETENTION_MS` | `604800000` | Source topic retention (7 days) |
| `KAFKA_DEAD_LETTER_SUFFIX` | `.dlq` | Dead-letter topic suffix |

## Usage

```python
from phlo_kafka.assets import phlo_kafka_consumer

phlo_kafka_consumer(
    name="events",
    topic_pattern="events",
    destination_table="bronze.events",
    unique_key=["event_id"],
    group="ingestion",
)
```
