"""Tests for service command utility helpers.

Covers lifecycle event emission with request correlation preserved,
post-start hooks skipped when a "requires" dependency cannot be
imported, native-process state cleaned up once its PID is gone, and
compose regeneration writing docker-compose.yml plus .env/.env.local.
"""

from __future__ import annotations

from pathlib import Path
from subprocess import CompletedProcess

import pytest

from phlo.cli.commands.services import utils as service_utils
from phlo.hooks.events import ServiceLifecycleEvent
from phlo.plugins.discovery import ServiceDefinition
from tests.helpers import FakeDiscovery, RecordingBus, _service


def test_emit_service_lifecycle_events_preserves_request_correlation(
    monkeypatch,
    tmp_path: Path,
) -> None:
    bus = RecordingBus()
    monkeypatch.setattr("phlo.hooks.emitters.get_hook_bus", lambda: bus)
    monkeypatch.setattr(
        service_utils, "_resolve_container_name", lambda name, project: f"{project}-{name}"
    )

    service_utils._emit_service_lifecycle_events(
        "pre_start",
        ["postgres", "minio"],
        project_name="demo",
        project_root=tmp_path,
        request_id="req-123",
        metadata={"native": False},
    )

    lifecycle_events = [event for event in bus.events if isinstance(event, ServiceLifecycleEvent)]
    assert len(lifecycle_events) == 2
    assert {event.service_name for event in lifecycle_events} == {"postgres", "minio"}
    assert {event.correlation.request_id for event in lifecycle_events} == {"req-123"}
    assert all(event.phase == "pre_start" for event in lifecycle_events)


def test_run_service_hooks_skips_missing_dependency(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    service = ServiceDefinition(
        name="dagster",
        description="Dagster",
        category="orchestration",
        hooks={
            "post_start": [
                {
                    "command": ["python", "-m", "phlo_dbt.hooks", "compile"],
                    "requires": "missing.module",
                    "timeout_seconds": "12",
                }
            ]
        },
    )

    class ServiceFakeDiscovery(FakeDiscovery):
        def get_service(self, name: str):
            return service if name == "dagster" else None

    calls: list[list[str]] = []

    monkeypatch.setattr("phlo.plugins.discovery.ServiceDiscovery", ServiceFakeDiscovery)
    monkeypatch.setattr("importlib.util.find_spec", lambda _name: None)
    monkeypatch.setattr(
        service_utils,
        "run_command",
        lambda cmd, **_kwargs: calls.append(list(cmd)) or CompletedProcess(cmd, 0, "", ""),
    )

    service_utils._run_service_hooks("post_start", ["dagster"], "demo", tmp_path)

    assert calls == []


def test_stop_native_processes_clears_exited_entries(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    project_root = tmp_path
    service_utils._save_native_state(project_root, {"dagster": {"pid": 1234}})

    signals: list[tuple[int, int]] = []

    def _fake_kill(pid: int, sig: int) -> None:
        signals.append((pid, sig))
        if sig == 0:
            raise ProcessLookupError

    def _fake_killpg(pid: int, sig: int) -> None:
        signals.append((pid, sig))
        if sig == 0:
            raise ProcessLookupError

    monkeypatch.setattr(service_utils.os, "killpg", _fake_killpg)
    monkeypatch.setattr(service_utils.os, "kill", _fake_kill)

    service_utils._stop_native_processes(project_root)

    assert signals[0][0] == 1234
    assert not service_utils._native_state_path(project_root).exists()


def test_regenerate_compose_writes_compose_and_env_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    phlo_dir = tmp_path / ".phlo"
    phlo_dir.mkdir()
    service = _service("postgres", default=True)

    class DiscoveryFake(FakeDiscovery):
        def __init__(self) -> None:
            super().__init__({service.name: service}, default_names=(service.name,))

    class ComposeGeneratorFake:
        def __init__(self, discovery) -> None:
            self.discovery = discovery

        def generate_compose(
            self, services_to_install, _phlo_dir, user_overrides=None, env_values=None
        ) -> str:
            assert [service.name for service in services_to_install] == ["postgres"]
            assert user_overrides == {"enabled": [], "disabled": []}
            assert env_values is not None
            return "services:\n  postgres: {}\n"

        def generate_env(self, services_to_install, env_overrides=None) -> str:
            assert [service.name for service in services_to_install] == ["postgres"]
            assert env_overrides == {}
            return "POSTGRES_PORT=5432\n"

        def generate_env_local(
            self, services_to_install, env_overrides=None, existing_values=None
        ) -> str:
            assert [service.name for service in services_to_install] == ["postgres"]
            assert existing_values == {}
            return "POSTGRES_PASSWORD=secret\n"

        def copy_service_files(self, services_to_install, _phlo_dir) -> list[str]:
            assert [service.name for service in services_to_install] == ["postgres"]
            return ["postgres/service.yaml"]

    monkeypatch.setattr(
        "phlo.cli.infrastructure.selection.select_services_to_install",
        lambda **_kwargs: [service],
    )
    monkeypatch.setattr(
        service_utils, "expand_service_dependencies", lambda _discovery, services: services
    )
    monkeypatch.setattr("phlo.plugins.compose.ComposeGenerator", ComposeGeneratorFake)

    service_utils._regenerate_compose(DiscoveryFake(), {}, phlo_dir)

    assert (phlo_dir / "docker-compose.yml").read_text() == "services:\n  postgres: {}\n"
    assert (phlo_dir / ".env").read_text() == "POSTGRES_PORT=5432\n"
    assert (phlo_dir / ".env.local").read_text() == "POSTGRES_PASSWORD=secret\n"
    assert (phlo_dir / ".env.local").stat().st_mode & 0o7777 == 0o600


def test_regenerate_compose_replaces_permissive_env_local_at_0600(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    phlo_dir = tmp_path / ".phlo"
    phlo_dir.mkdir()
    env_local = phlo_dir / ".env.local"
    env_local.write_text("POSTGRES_PASSWORD=old-secret\n")
    env_local.chmod(0o644)
    service = _service("postgres", default=True)

    class DiscoveryFake(FakeDiscovery):
        def __init__(self) -> None:
            super().__init__({service.name: service}, default_names=(service.name,))

    class ComposeGeneratorFake:
        def __init__(self, discovery) -> None:
            self.discovery = discovery

        def generate_compose(self, services_to_install, _phlo_dir, **kwargs) -> str:
            return "services:\n  postgres: {}\n"

        def generate_env(self, services_to_install, **kwargs) -> str:
            return "POSTGRES_PORT=5432\n"

        def generate_env_local(self, services_to_install, **kwargs) -> str:
            return "POSTGRES_PASSWORD=new-secret\n"

        def copy_service_files(self, services_to_install, _phlo_dir) -> list[str]:
            return []

    monkeypatch.setattr(
        "phlo.cli.infrastructure.selection.select_services_to_install",
        lambda **_kwargs: [service],
    )
    monkeypatch.setattr(
        service_utils, "expand_service_dependencies", lambda _discovery, services: services
    )
    monkeypatch.setattr("phlo.plugins.compose.ComposeGenerator", ComposeGeneratorFake)

    service_utils._regenerate_compose(DiscoveryFake(), {}, phlo_dir)

    assert env_local.read_text() == "POSTGRES_PASSWORD=new-secret\n"
    assert env_local.stat().st_mode & 0o7777 == 0o600
