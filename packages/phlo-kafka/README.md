# phlo-kafka

Kafka ingestion service plugin for Phlo.

## Description

`phlo-kafka` runs a pinned KRaft-mode Apache Kafka broker and provides
checkpoint-driven consumer assets. Kafka delivery is **at-least-once**; Iceberg
results are **effectively-once**: every consumed batch claims its offset range
in the durable ingestion checkpoint store (Phlo Postgres), lands through an
idempotent merge on a declared unique key, records the output snapshot, and
commits only after audit and promotion. An exact replay of a committed batch
skips the merge and finishes the broker commit using the checkpoint's saved
ranges. Batch identity includes the source, destination, consumer group, and
every partition's start and end offsets. A retry containing additional records
is a new batch and merges those records using the declared unique key.

Schema policy is enforced per batch: additive compatible changes auto-register;
incompatible changes halt the consumer, retain the source offsets uncommitted,
and route the offending records to a dead-letter topic so an explicit schema
migration is required.

Lineage records the consumed topic/partition/offset ranges with the exact
output snapshot id.

## Installation

```bash
pip install phlo-kafka
pip install "phlo-kafka[consumer]"  # with confluent-kafka
# or
phlo plugin install kafka
```

## Configuration

| Variable | Default | Description |
| -------- | ------- | ----------- |
| `KAFKA_PORT` | `10021` | Kafka broker host port |
| `KAFKA_RETENTION_MS` | `604800000` | Source topic retention (7 days) |
| `KAFKA_DEAD_LETTER_SUFFIX` | `.dlq` | Dead-letter topic suffix (30-day retention) |

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

```bash
phlo kafka status
phlo kafka topics
```
