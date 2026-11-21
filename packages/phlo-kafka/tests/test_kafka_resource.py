"""The Kafka client, topic hook and checkpoint lookup against confluent-kafka's real API."""

from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace

import pytest
from phlo.capabilities.registry import CapabilityRegistry
from phlo.capabilities.specs import ResourceSpec
from phlo.exceptions import PhloConfigError
from phlo_kafka import checkpoints, hooks
from phlo_kafka.resource import KafkaResource
from phlo_kafka.settings import KafkaSettings


class _Admin:
    def __init__(self, config: dict, *, topics: dict | None = None, fail: bool = False) -> None:
        self.config = config
        self.topics = {} if topics is None else topics
        self.fail = fail
        self.created: list = []

    def list_topics(self, timeout=None):
        if self.fail:
            raise RuntimeError("broker down")
        return SimpleNamespace(topics=self.topics)

    def create_topics(self, new_topics):
        self.created.extend(new_topics)
        return {
            topic.topic: SimpleNamespace(result=lambda timeout=None: None) for topic in new_topics
        }


class _NewTopic:
    def __init__(self, topic, *, num_partitions, replication_factor, config):
        self.topic = topic
        self.num_partitions = num_partitions
        self.replication_factor = replication_factor
        self.config = config


@pytest.fixture
def confluent(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Install a confluent_kafka shaped like the real one: AdminClient lives in .admin."""
    state = SimpleNamespace(admins=[], fail=False, consumer=None)
    package = ModuleType("confluent_kafka")
    admin = ModuleType("confluent_kafka.admin")

    def admin_client(config):
        client = _Admin(config, fail=state.fail)
        state.admins.append(client)
        return client

    admin.AdminClient = admin_client
    admin.NewTopic = _NewTopic
    package.admin = admin
    package.Consumer = lambda config: state.consumer
    monkeypatch.setitem(sys.modules, "confluent_kafka", package)
    monkeypatch.setitem(sys.modules, "confluent_kafka.admin", admin)
    return state


def test_health_check_uses_the_admin_client(confluent) -> None:
    assert KafkaResource("broker:9092").health_check() is True
    assert confluent.admins[0].config == {"bootstrap.servers": "broker:9092"}


def test_health_check_reports_an_unreachable_broker(confluent) -> None:
    confluent.fail = True
    assert KafkaResource("broker:9092").health_check() is False


def test_consume_fails_when_the_broker_does_not_answer(confluent) -> None:
    """An unreachable broker must not look like an empty topic."""

    class _Consumer:
        closed = False

        def list_topics(self, timeout=None):
            raise RuntimeError("Local: Broker transport failure")

        def subscribe(self, topics):
            pass

        def close(self):
            self.closed = True

    confluent.consumer = _Consumer()
    with pytest.raises(RuntimeError, match="did not respond"):
        KafkaResource("broker:9092").consume(topic_pattern="orders", group_id="g", max_records=10)
    assert confluent.consumer.closed


def test_init_topics_creates_the_compacted_checkpoint_topic(confluent) -> None:
    settings = SimpleNamespace(kafka_checkpoint_topic="checkpoints", kafka_retention_ms=1000)

    assert hooks.init_topics(resource=KafkaResource("broker:9092"), settings=settings) == 0

    [created] = confluent.admins[0].created
    assert (created.topic, created.num_partitions, created.replication_factor) == (
        "checkpoints",
        1,
        1,
    )
    assert created.config == {"retention.ms": "1000", "cleanup.policy": "compact"}


def test_the_broker_port_is_not_the_host_published_port(monkeypatch: pytest.MonkeyPatch) -> None:
    """KAFKA_PORT is the host port; inside the network the broker listens on 9092."""
    monkeypatch.setenv("KAFKA_PORT", "10021")
    monkeypatch.setattr("phlo.config.network.socket.gethostbyname", lambda host: "10.0.0.2")

    assert KafkaSettings().bootstrap_servers() == "kafka:9092"


def test_the_host_falls_back_to_the_published_port(monkeypatch: pytest.MonkeyPatch) -> None:
    import socket

    def unresolvable(host):
        raise socket.gaierror(host)

    monkeypatch.setenv("KAFKA_PORT", "10021")
    monkeypatch.setattr("phlo.config.network.socket.gethostbyname", unresolvable)

    assert KafkaSettings().bootstrap_servers() == "localhost:10021"


class _Store:
    def claim(self, **_kwargs): ...
    def record_snapshot(self, **_kwargs): ...
    def commit(self, **_kwargs): ...
    def fail(self, **_kwargs): ...
    def latest_committed(self, **_kwargs): ...
    def find_by_idempotency_key(self, **_kwargs): ...
    def list_open(self, **_kwargs): ...


def test_the_checkpoint_store_is_the_registered_resource(monkeypatch: pytest.MonkeyPatch) -> None:
    """phlo-postgres registers the store as the ingestion_checkpoints resource."""
    registry = CapabilityRegistry()
    store = _Store()
    registry.register("resource", ResourceSpec(name="postgres", resource=object()))
    registry.register("resource", ResourceSpec(name="ingestion_checkpoints", resource=store))
    monkeypatch.setattr(checkpoints, "get_capability_registry", lambda: registry)

    assert checkpoints.resolve_checkpoint_store() is store


def test_a_missing_checkpoint_store_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(checkpoints, "get_capability_registry", lambda: CapabilityRegistry())

    with pytest.raises(PhloConfigError, match="checkpoint store"):
        checkpoints.resolve_checkpoint_store()
