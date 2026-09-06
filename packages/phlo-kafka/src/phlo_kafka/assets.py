"""Kafka consumer assets: checkpoint-driven effectively-once ingestion.

The lifecycle per consumed batch is:

1. **Claim** the consumed offset ranges in the durable checkpoint store.
2. **Stage** records through an idempotent unique-key merge. Under the
   snapshot WAP strategy the batch lands in a candidate branch of the
   destination (at-least-once Kafka, effectively-once Iceberg results,
   invisible to readers until promotion); otherwise it merges directly into
   the destination via the table store.
3. **Audit** via schema policy: incompatible schema changes dead-letter the
   batch and retain offsets uncommitted.
4. **Promote**: record the output snapshot on the checkpoint; under the
   snapshot strategy the promoter advances the release pointer.
5. **Commit** the checkpoint (and consumer offsets) only after promotion.

Replaying a committed range skips cleanly, and replaying an uncommitted
range merges the same keyed rows again — either way no duplicate logical
destination records.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass, field
from typing import Any

from phlo.capabilities import AssetSpec, MaterializeResult, RunSpec
from phlo.capabilities.interfaces import SourceOffsetRange
from phlo.capabilities.resolver import resolve_capability
from phlo.capabilities.runtime import RuntimeContext
from phlo.exceptions import PhloConfigError
from phlo.logging import get_logger, log_event

from phlo_kafka.checkpoints import KafkaCheckpointAdapter
from phlo_kafka.settings import get_settings

# Populated at decoration time and kept for the process lifetime. Cleared only
# by clear_kafka_assets(), which tests and plugin reloads use to reset state.
_KAFKA_ASSETS: list[AssetSpec] = []


def get_kafka_assets() -> list[AssetSpec]:
    """Return registered Kafka consumer asset specifications."""
    return list(_KAFKA_ASSETS)


def clear_kafka_assets() -> None:
    """Clear all registered Kafka assets (for tests and plugin reloads)."""
    _KAFKA_ASSETS.clear()


@dataclass
class KafkaConsumerConfig:
    """Declaration of one Kafka-consumer-backed destination table."""

    name: str
    group: str
    topic_pattern: str
    destination_table: str
    unique_key: list[str]
    group_id: str | None = None
    schema_policy: str = "additive"
    schema: dict[str, str] = field(default_factory=dict)
    dead_letter_topic: str | None = None
    max_records_per_batch: int = 10_000
    metadata: dict[str, Any] = field(default_factory=dict)

    def resolved_group_id(self) -> str:
        """Return the consumer group id with the configured prefix."""
        if self.group_id:
            return self.group_id
        return f"{get_settings().kafka_consumer_group_prefix}-{self.name}"

    def resolved_dead_letter_topic(self) -> str:
        """Return the dead-letter topic, deriving one from settings by default."""
        if self.dead_letter_topic:
            return self.dead_letter_topic
        return get_settings().dead_letter_topic(self.topic_pattern)


def _validate_consumer_config(config: KafkaConsumerConfig) -> None:
    if not config.unique_key:
        raise PhloConfigError(
            message=(
                f"Kafka consumer asset {config.name!r} must declare a unique key; "
                "idempotent merges are what make retries produce no duplicates."
            ),
            suggestions=["Declare the record fields that uniquely identify a logical row."],
        )


def _release_field(record: Any, name: str) -> Any:
    if isinstance(record, dict):
        return record.get(name)
    return getattr(record, name, None)


def ingest_batch(
    *,
    config: KafkaConsumerConfig,
    records: list[dict[str, Any]],
    ranges: list[SourceOffsetRange],
    checkpoint_adapter: KafkaCheckpointAdapter,
    stager: Callable[[str, list[dict[str, Any]]], dict[str, Any]],
    promoter: Callable[[str], list[Any]] | None = None,
    dead_letter_sink: Callable[[str, list[dict[str, Any]]], int] | None = None,
    known_fields: dict[str, str] | None = None,
    logger: Any = None,
) -> dict[str, Any]:
    """Drive one consumed batch through claim→stage→audit→promote→commit.

    ``stager(checkpoint_id, records)`` lands the batch and returns at least
    ``snapshot_id``; ``promoter(checkpoint_id)`` (snapshot strategy only)
    advances the release pointer and returns release records. On any failure
    the checkpoint is marked failed and its offsets stay uncommitted so the
    range replays into an idempotent stage.
    """
    logger = logger or get_logger(__name__)
    if not records:
        return {"checkpoint_id": None, "status": "no_data", "rows": 0, "dead_lettered": 0}

    checkpoint = checkpoint_adapter.claim_ranges(
        target_table=config.destination_table, ranges=ranges
    )

    if checkpoint.status == "committed":
        # Replay of an already-committed range (crash between checkpoint
        # commit and offset commit): the ranges are durably represented by
        # their snapshot, so skip instead of merging again.
        log_event(
            logger, "info", "kafka_range_already_committed", checkpoint_id=checkpoint.checkpoint_id
        )
        return {
            "checkpoint_id": checkpoint.checkpoint_id,
            "status": "already_committed",
            "rows": 0,
            "dead_lettered": 0,
            "ranges": [asdict(item) for item in checkpoint.ranges],
        }

    # Audit: schema policy. Incompatible changes dead-letter the batch and
    # retain the offsets uncommitted (the checkpoint stays open).
    from phlo_kafka.schema_policy import evaluate_field_types

    decision = evaluate_field_types(existing_schema=known_fields or {}, records=records)
    if decision.decision == "incompatible":
        dead_lettered = 0
        if dead_letter_sink is not None:
            dead_lettered = dead_letter_sink(config.resolved_dead_letter_topic(), records)
        checkpoint_adapter.fail(
            checkpoint_id=checkpoint.checkpoint_id,
            reason=decision.reason or "schema policy",
        )
        log_event(
            logger,
            "warning",
            "kafka_schema_policy_halted",
            reason=decision.reason,
            dead_lettered=dead_lettered,
        )
        return {
            "checkpoint_id": checkpoint.checkpoint_id,
            "status": "dead_lettered",
            "reason": decision.reason,
            "rows": 0,
            "dead_lettered": dead_lettered,
        }

    # Stage: idempotent unique-key merge into the candidate (snapshot
    # strategy) or the destination (legacy table-store path).
    try:
        stage_result = stager(checkpoint.checkpoint_id, records) or {}
        snapshot_id = stage_result.get("snapshot_id")
    except Exception as exc:
        checkpoint_adapter.fail(
            checkpoint_id=checkpoint.checkpoint_id, reason=f"stage failed: {exc}"
        )
        raise

    checkpoint = checkpoint_adapter.bind_snapshot(
        checkpoint_id=checkpoint.checkpoint_id,
        snapshot_id=snapshot_id if snapshot_id is not None else "unknown",
    )

    # Promote: advance the release pointer when a promoter is configured.
    release_id: Any = checkpoint.checkpoint_id
    released_snapshot: Any = snapshot_id
    if promoter is not None:
        try:
            promoted = promoter(checkpoint.checkpoint_id)
        except Exception as exc:
            checkpoint_adapter.fail(
                checkpoint_id=checkpoint.checkpoint_id, reason=f"promote failed: {exc}"
            )
            raise
        if promoted:
            release_id = _release_field(promoted[0], "release_id")
            released_snapshot = _release_field(promoted[0], "snapshot_id")
            checkpoint_adapter.bind_snapshot(
                checkpoint_id=checkpoint.checkpoint_id,
                snapshot_id=released_snapshot if released_snapshot is not None else "unknown",
                release_id=release_id,
            )

    checkpoint = checkpoint_adapter.commit(checkpoint_id=checkpoint.checkpoint_id)
    return {
        "checkpoint_id": checkpoint.checkpoint_id,
        "status": "committed",
        "snapshot_id": released_snapshot,
        "release_id": release_id,
        "rows": stage_result.get("rows_merged", stage_result.get("appended", len(records))),
        "dead_lettered": 0,
        "ranges": [
            {
                "topic": item.topic,
                "partition": item.partition,
                "start_offset": item.start_offset,
                "end_offset": item.end_offset,
            }
            for item in ranges
        ],
    }


def _resolve_promotion_catalog() -> Any:
    """Resolve the snapshot-promotion catalog when the WAP strategy needs it."""
    from phlo.capabilities.interfaces import SnapshotPromotionCatalog
    from phlo.infrastructure import load_wap_config

    if load_wap_config().strategy != "snapshot":
        return None
    resolution = resolve_capability("catalog")
    if resolution is None or not (
        resolution.support.supports_promote and resolution.support.supports_snapshots
    ):
        raise PhloConfigError(
            message="Kafka snapshot-strategy ingestion requires a snapshot-promotion catalog.",
            suggestions=["Install phlo-polaris, or set wap.strategy to 'branch'."],
        )
    if not isinstance(resolution.provider, SnapshotPromotionCatalog):
        raise PhloConfigError(
            message="Configured catalog does not implement snapshot-based WAP promotion.",
            suggestions=["Install phlo-polaris, or set wap.strategy to 'branch'."],
        )
    return resolution.provider


def _make_stager(config: KafkaConsumerConfig, catalog: Any, table_store: Any):
    """Build the staging callable for the active WAP strategy."""
    if catalog is not None:

        def stager(checkpoint_id: str, records: list[dict[str, Any]]) -> dict[str, Any]:
            catalog.create_candidate(table_name=config.destination_table, run_id=checkpoint_id)
            return catalog.merge_rows_into_candidate(
                table_name=config.destination_table,
                run_id=checkpoint_id,
                rows=records,
                unique_key=config.unique_key,
            )

        return stager

    def table_store_stager(checkpoint_id: str, records: list[dict[str, Any]]) -> dict[str, Any]:
        import tempfile
        from pathlib import Path

        import pyarrow as pa
        import pyarrow.parquet as pq

        with tempfile.TemporaryDirectory(prefix="phlo-kafka-stage-") as tmp:
            path = Path(tmp) / "batch.parquet"
            pq.write_table(pa.Table.from_pylist(records), path)
            result = table_store.merge_parquet(
                table_name=config.destination_table,
                data_path=str(path),
                unique_key=",".join(config.unique_key),
            )
            observed = table_store.observe_table_state(table_name=config.destination_table)
            if observed.revision is None:
                raise RuntimeError("Kafka staging could not resolve the written snapshot")
            return {
                **result,
                "snapshot_id": observed.revision,
                "rows_merged": result.get("rows_inserted", len(records)),
            }

    return table_store_stager


def _build_asset_run(
    config: KafkaConsumerConfig,
    *,
    client_factory: Callable[[], Any] | None,
) -> Callable[[RuntimeContext], Iterator[MaterializeResult]]:
    """Build the run callable that consumes one batch and lands it safely."""

    def run(runtime: RuntimeContext) -> Iterator[MaterializeResult]:
        logger = runtime.logger
        if client_factory is not None:
            client = client_factory()
        else:
            from phlo_kafka.resource import KafkaResource

            client = KafkaResource()
        records, ranges = client.consume(
            topic_pattern=config.topic_pattern,
            group_id=config.resolved_group_id(),
            max_records=config.max_records_per_batch,
        )
        promotion_catalog = _resolve_promotion_catalog()
        table_store = None
        if promotion_catalog is None:
            resolution = resolve_capability("table_store")
            if resolution is None:
                raise PhloConfigError(
                    message="Kafka consumer assets require a table store capability.",
                    suggestions=["Install phlo-iceberg to write Kafka batches to Iceberg."],
                )
            table_store = resolution.provider
        checkpoint_adapter = KafkaCheckpointAdapter(
            source_id=f"kafka:{config.topic_pattern}",
            group_id=config.resolved_group_id(),
        )
        evidence = ingest_batch(
            config=config,
            records=records,
            ranges=ranges,
            checkpoint_adapter=checkpoint_adapter,
            stager=_make_stager(config, promotion_catalog, table_store),
            promoter=(
                (
                    lambda checkpoint_id: promotion_catalog.promote_candidates(
                        namespace=f"pipeline-run-{checkpoint_id}",
                        release_id=checkpoint_id,
                    )
                )
                if promotion_catalog is not None
                else None
            ),
            dead_letter_sink=(
                client.dead_letter_sink if hasattr(client, "dead_letter_sink") else None
            ),
            known_fields=config.schema or None,
            logger=logger,
        )
        # Kafka offsets are committed only after the checkpoint committed;
        # a crash before this point replays the range into an idempotent merge.
        if evidence.get("status") in {"committed", "already_committed"} and hasattr(
            client, "commit_offsets"
        ):
            committed_ranges = [SourceOffsetRange(**item) for item in evidence["ranges"]]
            client.commit_offsets(committed_ranges, group_id=config.resolved_group_id())
        yield MaterializeResult(
            metadata={
                "kafka_topic": config.topic_pattern,
                "kafka_group": config.resolved_group_id(),
                "destination_table": config.destination_table,
                "checkpoint_id": evidence.get("checkpoint_id"),
                "snapshot_id": evidence.get("snapshot_id"),
                "release_id": evidence.get("release_id"),
                "rows_merged": evidence.get("rows"),
                "dead_lettered": evidence.get("dead_lettered", 0),
                "offset_ranges": evidence.get("ranges", []),
                "status": evidence.get("status"),
            },
            status=(
                "ok"
                if evidence.get("status") in {"committed", "already_committed", "no_data"}
                else "failure"
            ),
        )

    return run


def phlo_kafka_consumer(
    name: str,
    topic_pattern: str,
    destination_table: str,
    unique_key: list[str] | str,
    group: str,
    *,
    group_id: str | None = None,
    schema_policy: str = "additive",
    schema: dict[str, str] | None = None,
    dead_letter_topic: str | None = None,
    max_records_per_batch: int = 10_000,
    description: str | None = None,
    client_factory: Callable[[], Any] | None = None,
) -> AssetSpec:
    """Register a Kafka consumer asset and return its specification.

    ``schema`` declares the destination's known field types (used by the
    schema policy to halt on incompatible changes); new fields are additive
    compatible and pass through.
    """
    normalized_key = [unique_key] if isinstance(unique_key, str) else list(unique_key)
    config = KafkaConsumerConfig(
        name=name,
        group=group,
        topic_pattern=topic_pattern,
        destination_table=destination_table,
        unique_key=normalized_key,
        group_id=group_id,
        schema_policy=schema_policy,
        schema=dict(schema or {}),
        dead_letter_topic=dead_letter_topic,
        max_records_per_batch=max_records_per_batch,
    )
    _validate_consumer_config(config)
    asset_key = f"{group}.{name}"
    spec = AssetSpec(
        key=asset_key,
        group=group,
        description=description
        or f"Consumes Kafka topic {topic_pattern!r} into {destination_table!r}",
        kinds={"kafka", "ingestion"},
        tags={
            "provider": "kafka",
            "asset_type": "ingestion",
            "source": "kafka",
            "schema_policy": schema_policy,
        },
        metadata={
            "provider": "kafka",
            "kafka_topic": topic_pattern,
            "kafka_group": config.resolved_group_id(),
            "destination_table": destination_table,
            "unique_key": normalized_key,
            "schema": config.schema,
            "schema_policy": schema_policy,
            "dead_letter_topic": config.resolved_dead_letter_topic(),
            "group": group,
        },
        partitions=None,
        resources=set(),
        run=RunSpec(
            fn=_build_asset_run(config, client_factory=client_factory),
            max_runtime_seconds=3600,
            max_retries=1,
            retry_delay_seconds=30,
            cron=None,
            freshness_hours=None,
        ),
        checks=[],
    )
    _KAFKA_ASSETS.append(spec)
    return spec
