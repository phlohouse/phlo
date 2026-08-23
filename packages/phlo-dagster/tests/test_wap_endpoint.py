"""Endpoint resolution for WAP-enabled Dagster runs.

Asserts local runs resolve through normal Phlo host resolution while an
explicitly configured endpoint bypasses resolution entirely.
"""

from types import SimpleNamespace

from phlo.config_schema import WapConfig
from phlo_dagster.wap_endpoint import resolve_wap_dagster_url


def test_local_wap_resolves_dagster_host_and_project_port(monkeypatch) -> None:
    monkeypatch.setattr(
        "phlo_dagster.wap_endpoint.resolve_host",
        lambda host, port, *, port_env_var: ("localhost", 18443),
    )
    monkeypatch.setattr(
        "phlo_dagster.wap_endpoint.get_settings",
        lambda: SimpleNamespace(dagster_port=10006),
    )

    assert resolve_wap_dagster_url(WapConfig(enabled=True)) == "http://localhost:18443/graphql"


def test_remote_wap_uses_the_explicit_configured_endpoint(monkeypatch) -> None:
    monkeypatch.setattr(
        "phlo_dagster.wap_endpoint.resolve_host",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not resolve remote")),
    )

    assert (
        resolve_wap_dagster_url(
            WapConfig(enabled=True, dagster_url="https://dagster.example.com/graphql")
        )
        == "https://dagster.example.com/graphql"
    )
