"""Tests for the Polaris bootstrap hook (idempotent)."""

from __future__ import annotations

from types import SimpleNamespace

from phlo_polaris.hooks import bootstrap, ensure_catalog, ensure_principal, wait_for_polaris


class FakeClient:
    def __init__(self, *, healthy: bool = True, catalogs: list | None = None) -> None:
        self.healthy = healthy
        self.catalogs = list(catalogs or [])
        self.created_catalogs: list[str] = []
        self.created_principals: list[str] = []
        self.granted: list[tuple[str, str]] = []

    def health_check(self) -> bool:
        return self.healthy

    def get_catalog(self, name: str):
        return next((c for c in self.catalogs if c.get("name") == name), None)

    def create_catalog(self, *, name: str, warehouse: str, endpoint: str | None = None):
        self.created_catalogs.append(name)
        self.catalogs.append({"name": name})

    def list_principals(self):
        return [{"name": name} for name in self.created_principals]

    def create_principal(self, *, name: str):
        self.created_principals.append(name)
        return {
            "principal": {"name": name},
            "credentials": {"clientId": name, "clientSecret": "generated"},
        }

    def bootstrap_grants(self):
        self.grants_requested = True
        return {"phlo_writer": ["TABLE_WRITE_DATA"], "phlo_reader": ["TABLE_READ_DATA"]}


def test_wait_for_polaris_polls_until_healthy() -> None:
    assert wait_for_polaris(FakeClient(healthy=True), timeout_seconds=1) is True
    assert wait_for_polaris(FakeClient(healthy=False), timeout_seconds=0) is False


def test_ensure_catalog_creates_once(monkeypatch) -> None:
    client = FakeClient()
    assert ensure_catalog(client, name="phlo", warehouse="s3://lake") is True
    assert ensure_catalog(client, name="phlo", warehouse="s3://lake") is False
    assert client.created_catalogs == ["phlo"]


def test_ensure_principal_captures_generated_credentials() -> None:
    client = FakeClient()
    credentials: dict[str, str] = {}
    assert ensure_principal(client, name="phlo_writer", credentials=credentials) is True
    assert credentials == {"phlo_writer": "phlo_writer:generated"}


def test_ensure_principal_creates_once(monkeypatch) -> None:
    client = FakeClient()
    assert ensure_principal(client, name="phlo_writer", credentials={}) is True
    client_created = client.created_principals
    assert ensure_principal(client, name="phlo_writer") is False
    assert client_created == ["phlo_writer"]


def test_bootstrap_is_idempotent(monkeypatch) -> None:
    client = FakeClient(catalogs=[{"name": "phlo"}])
    monkeypatch.setattr(
        "phlo_polaris.settings.get_settings",
        lambda: SimpleNamespace(
            polaris_catalog="phlo",
            polaris_writer_client_id="phlo_writer",
            polaris_reader_client_id="phlo_reader",
        ),
    )
    assert bootstrap(client=client) == 0
    assert client.created_catalogs == []
    assert sorted(client.created_principals) == ["phlo_reader", "phlo_writer"]
    assert getattr(client, "grants_requested", False)
