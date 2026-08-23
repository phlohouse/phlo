"""Tests for extracted Observatory read models.

Covers ReadModelCache semantics (TTL reuse, per-key single-flight sharing,
SQLite persistence and invalidation, unpersistable values) plus the
service-status helpers: docker status derivation, project-scoped container
lookups, saved-query SQL validation, and search result ranking.
"""

from __future__ import annotations

import threading

from phlo_api.observatory_api.observatory_cache import ReadModelCache
from phlo_api.observatory_api.observatory_models import (
    ObservatoryAsset,
    ObservatoryHealth,
    ObservatoryService,
)
from phlo_api.observatory_api.observatory_saved_queries import validate_saved_query_sql
from phlo_api.observatory_api.observatory_search import search_results
from phlo_api.observatory_api.observatory_services import (
    DOCKER_PS_TIMEOUT_SECONDS,
    configured_compose_services,
    docker_status_from_container,
    load_docker_containers,
    load_project_docker_containers,
    load_docker_service_statuses,
    service_name_from_container,
)


def test_read_model_cache_returns_cached_value_before_ttl() -> None:
    cache = ReadModelCache(project_key=lambda: "demo")
    calls: list[str] = []

    first = cache.cached("services", 30, lambda: calls.append("called") or ["postgres"])
    second = cache.cached("services", 30, lambda: calls.append("called") or ["trino"])

    assert first == ["postgres"]
    assert second == ["postgres"]
    assert calls == ["called"]


def test_read_model_cache_clear_removes_values() -> None:
    cache = ReadModelCache(project_key=lambda: "demo")
    cache.cached("services", 30, lambda: ["postgres"])

    cache.clear()
    value = cache.cached("services", 30, lambda: ["trino"])

    assert value == ["trino"]


def test_read_model_cache_allows_different_key_loaders_to_overlap() -> None:
    cache = ReadModelCache(project_key=lambda: "demo")
    both_loaders_started = threading.Barrier(3)
    release_loaders = threading.Event()
    results: list[str] = []

    def loader(value: str) -> str:
        both_loaders_started.wait(timeout=1)
        assert release_loaders.wait(timeout=1)
        return value

    threads = [
        threading.Thread(
            target=lambda value=value: results.append(
                cache.cached(value, 30, lambda: loader(value))
            )
        )
        for value in ("assets", "tables")
    ]
    for thread in threads:
        thread.start()
    both_loaders_started.wait(timeout=1)
    release_loaders.set()
    for thread in threads:
        thread.join(timeout=1)

    assert sorted(results) == ["assets", "tables"]


def test_read_model_cache_shares_same_key_loader_result() -> None:
    cache = ReadModelCache(project_key=lambda: "demo")
    loader_started = threading.Event()
    release_loader = threading.Event()
    calls: list[str] = []
    results: list[object] = []

    def loader() -> object:
        calls.append("called")
        loader_started.set()
        assert release_loader.wait(timeout=1)
        return {"value": "shared"}

    threads = [
        threading.Thread(target=lambda: results.append(cache.cached("assets", 30, loader)))
        for _ in range(2)
    ]
    threads[0].start()
    assert loader_started.wait(timeout=1)
    threads[1].start()
    release_loader.set()
    for thread in threads:
        thread.join(timeout=1)

    assert calls == ["called"]
    assert results[0] is results[1]


def test_read_model_cache_reuses_sqlite_value(tmp_path) -> None:
    db_path = tmp_path / "read_models.sqlite"
    calls: list[str] = []
    first_cache = ReadModelCache(project_key=lambda: "demo", db_path=lambda: db_path)

    first = first_cache.cached("services", 30, lambda: calls.append("first") or ["postgres"])
    second_cache = ReadModelCache(project_key=lambda: "demo", db_path=lambda: db_path)
    second = second_cache.cached("services", 30, lambda: calls.append("second") or ["trino"])

    assert first == ["postgres"]
    assert second == ["postgres"]
    assert calls == ["first"]


def test_read_model_cache_clear_removes_sqlite_values(tmp_path) -> None:
    db_path = tmp_path / "read_models.sqlite"
    cache = ReadModelCache(project_key=lambda: "demo", db_path=lambda: db_path)
    cache.cached("services", 30, lambda: ["postgres"])

    cache.clear()
    value = ReadModelCache(project_key=lambda: "demo", db_path=lambda: db_path).cached(
        "services", 30, lambda: ["trino"]
    )

    assert value == ["trino"]


def test_read_model_cache_skips_unpersistable_values(tmp_path) -> None:
    db_path = tmp_path / "read_models.sqlite"
    cache = ReadModelCache(project_key=lambda: "demo", db_path=lambda: db_path)
    calls: list[str] = []
    value = cache.cached(
        "locked",
        30,
        lambda: calls.append("first") or {"lock": __import__("threading").Lock()},
    )

    assert "lock" in value
    assert calls == ["first"]
    assert (
        ReadModelCache(project_key=lambda: "demo", db_path=lambda: db_path).cached(
            "locked", 30, lambda: calls.append("second") or "fallback"
        )
        == "fallback"
    )


def test_docker_status_from_running_container() -> None:
    status, health = docker_status_from_container({"State": "running", "Status": "Up 10 seconds"})

    assert status == "running"
    assert health.state == "unknown"


def test_load_docker_containers_allows_multi_stack_local_daemon_latency(monkeypatch) -> None:
    observed: dict[str, object] = {}

    def fake_run(*args, **kwargs):
        observed["timeout"] = kwargs.get("timeout")

        class Result:
            returncode = 0
            stdout = ""

        return Result()

    monkeypatch.setattr("subprocess.run", fake_run)

    assert load_docker_containers() == []
    assert observed["timeout"] == DOCKER_PS_TIMEOUT_SECONDS
    assert DOCKER_PS_TIMEOUT_SECONDS >= 10


def test_load_project_docker_containers_skips_global_scan_without_compose_project(
    monkeypatch, tmp_path
) -> None:
    def fail_run(*args, **kwargs):
        raise AssertionError("global docker scan should not run without a project scope")

    monkeypatch.delenv("PHLO_COMPOSE_PROJECT", raising=False)
    monkeypatch.delenv("COMPOSE_PROJECT_NAME", raising=False)
    monkeypatch.setattr("subprocess.run", fail_run)

    assert load_project_docker_containers(tmp_path) == []


def test_service_name_from_container_matches_known_service_id() -> None:
    assert service_name_from_container("demo-postgres-1", {"postgres"}) == "postgres"


def test_docker_statuses_require_project_scope(monkeypatch) -> None:
    containers = [
        {
            "Names": "pokehunt-postgres",
            "State": "running",
            "Status": "Up 10 seconds",
            "Labels": "",
        }
    ]

    monkeypatch.delenv("PHLO_COMPOSE_PROJECT", raising=False)
    monkeypatch.delenv("COMPOSE_PROJECT_NAME", raising=False)

    assert load_docker_service_statuses({"postgres"}, containers) == {}


def test_configured_compose_services_reads_generated_compose(tmp_path) -> None:
    phlo_dir = tmp_path / ".phlo"
    phlo_dir.mkdir()
    (phlo_dir / "docker-compose.yml").write_text(
        "services:\n  postgres:\n    image: postgres\n  trino:\n    image: trino\n"
    )

    assert configured_compose_services(tmp_path) == {"postgres", "trino"}


def test_validate_saved_query_sql_accepts_select_star_limit() -> None:
    assert validate_saved_query_sql("select * from raw.events limit 10") is None


def test_validate_saved_query_sql_rejects_delete() -> None:
    assert (
        validate_saved_query_sql("delete from raw.events")
        == "Only simple SELECT preview queries can be saved."
    )


def test_search_results_matches_services_and_assets() -> None:
    results = search_results(
        query="post",
        services=[
            ObservatoryService(
                id="postgres",
                name="Postgres",
                kind="service",
                status="running",
                health=ObservatoryHealth(state="ok"),
            )
        ],
        assets=[ObservatoryAsset(id="raw.events", name="Raw Events", group="raw")],
        tables=[],
        operations=[],
    )

    assert [result.id for result in results] == ["service:postgres"]
