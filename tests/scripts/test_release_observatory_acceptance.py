"""Acceptance-driver contracts, not live installed-artifact qualification.

These tests need the normal workspace dependencies. The separate Windows
golden-path portability harness intentionally needs only pytest and stdlib.
"""

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from types import SimpleNamespace

import pytest
import yaml


@pytest.fixture
def observatory(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "scripts"))
    import release_observatory_acceptance

    return release_observatory_acceptance


@pytest.fixture
def config(tmp_path, observatory):
    return observatory.golden.RunConfig(
        repo_root=tmp_path,
        project_dir=tmp_path / "project",
        wheelhouse=tmp_path / "wheelhouse",
        operator_env=tmp_path / "operator-env",
        project_name="phlo-acceptance-contract",
    )


def test_preview_layer_preserves_provider_private_volumes(config, observatory, monkeypatch):
    """Layer transformation only; this does not verify image UID access."""
    overrides = config.project_dir / ".phlo/overrides"
    overrides.mkdir(parents=True)
    layer_path = overrides / "compose.yaml"
    layer_path.write_text("volumes:\n  retained-data: {}\n")
    output = config.project_dir / "private/generated"
    output.mkdir(parents=True)
    provider = {
        "services": {
            "trino-preview-init": {
                "image": "example.invalid/trino@sha256:synthetic",
                "network_mode": "none",
                "volumes": ["./trino:/source/trino:ro", "preview-config:/target/trino"],
            },
            "trino-preview": {
                "image": "example.invalid/trino@sha256:synthetic",
                "volumes": ["preview-config:/etc/trino:ro"],
                "networks": ["preview"],
                "depends_on": {
                    "trino-preview-init": {"condition": "service_completed_successfully"}
                },
            },
        },
        "volumes": {"preview-config": {}},
        "networks": {"preview": {"external": True, "name": "owned-network"}},
    }
    provider_path = output / "compose.yaml"
    provider_path.write_text(yaml.safe_dump(provider))
    original = provider_path.read_bytes()
    (output / "api-settings.json").write_text("{}")
    inputs = output.parent / "preview.json"
    monkeypatch.chdir(config.project_dir)
    monkeypatch.setattr(observatory, "preview_config", lambda *args: inputs)
    monkeypatch.setattr(observatory.golden, "run", lambda *args, **kwargs: None)
    monkeypatch.setattr(observatory.golden, "pin_candidate_images", lambda _: ({}, []))
    monkeypatch.setattr(
        observatory.golden,
        "compose_config_json",
        lambda _: {
            "networks": {"default": {"name": "owned-network"}},
            "services": {
                **provider["services"],
                "phlo-api": {"image": "example.invalid/api@sha256:synthetic"},
            },
        },
    )

    def run_installed_layer(_config, source, *args):
        monkeypatch.setattr(sys, "argv", ["-c", *args])
        exec(source, {})
        return {}

    monkeypatch.setattr(observatory, "installed", run_installed_layer)
    monkeypatch.setattr(observatory, "compose", lambda *args: None)
    monkeypatch.setattr(
        observatory.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0)
    )
    observatory.provision_preview(config, {"env": "analytics"})

    result = yaml.safe_load(layer_path.read_text())
    assert result["volumes"] == {"retained-data": {}, "preview-config": {}}
    coordinator = result["services"]["trino-preview"]
    assert coordinator["volumes"] == ["preview-config:/etc/trino:ro"]
    assert coordinator["depends_on"] == provider["services"]["trino-preview"]["depends_on"]
    assert coordinator["ports"] == ["127.0.0.1::8443"]
    init = result["services"]["trino-preview-init"]
    assert init["network_mode"] == "none"
    assert init["volumes"] == [
        f"{output}/trino:/source/trino:ro",
        "preview-config:/target/trino",
    ]
    assert result["services"]["phlo-api"]["volumes"] == [
        f"{output}/api-secrets:/opt/phlo-preview/api-secrets:ro"
    ]
    assert provider_path.read_bytes() == original


def test_http_request_handles_json_denials_and_redacts_errors(observatory):
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(403 if self.path == "/denied" else 200)
            self.end_headers()
            self.wfile.write(b'{"private":"synthetic-secret"}' if self.path == "/denied" else b"")

        def do_POST(self):
            received.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(202)
            self.end_headers()
            self.wfile.write(b'{"id":"execution-19"}')

        def log_message(self, *_args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        worker = Thread(target=server.serve_forever)
        worker.start()
        try:
            base = f"http://127.0.0.1:{server.server_port}"
            assert observatory.request(base) == {}
            assert observatory.request(base, payload={"sql": "SELECT 19"}, expected=(202,)) == {
                "id": "execution-19"
            }
            assert received == [{"sql": "SELECT 19"}]
            assert observatory.request(base + "/denied", expected=(403,)) == {
                "private": "synthetic-secret"
            }
            with pytest.raises(observatory.golden.CandidateError) as error:
                observatory.request(base + "/denied")
            assert str(error.value) == "Observatory request failed with HTTP 403"
        finally:
            server.shutdown()
            worker.join()


@pytest.mark.parametrize("state", ["ready", "disabled", "configured", "unavailable", "empty"])
def test_readiness_requires_every_capability_ready(state, observatory, monkeypatch):
    capabilities = {
        name: [{"state": "ready"}]
        for name in ("orchestration", "incidents", "query_usage", "query_preview")
    }
    capabilities["query_preview"] = [] if state == "empty" else [{"state": state}]
    report = {"capabilities": capabilities}
    monkeypatch.setattr(observatory, "request", lambda *_: report)
    if state == "ready":
        assert (
            observatory.verify_readiness("http://synthetic.invalid", {"env": "analytics"}) == report
        )
    else:
        with pytest.raises(observatory.golden.CandidateError, match="query_preview"):
            observatory.verify_readiness("http://synthetic.invalid", {"env": "analytics"})


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "0.0.0.0", None])
def test_published_ports_require_explicit_loopback(host, config, observatory, monkeypatch):
    monkeypatch.setattr(
        observatory.golden,
        "compose_config_json",
        lambda _: {"services": {"api": {"ports": [{"host_ip": host}]}, "private": {}}},
    )
    if host in ("127.0.0.1", "::1"):
        observatory.verify_ports(config)
    else:
        with pytest.raises(observatory.golden.CandidateError, match="beyond loopback"):
            observatory.verify_ports(config)


@pytest.mark.parametrize("fault", [None, "failed", "rows", "evidence", "ref", "history"])
def test_query_acceptance_requires_retained_rows_and_durable_history(
    fault, observatory, monkeypatch
):
    completed = {
        "status": "completed",
        "result": {
            "rows": [
                {"id": 19, "label": "synthetic", "active": False},
                {"id": 7, "label": "retained", "active": True},
            ]
        },
        "evidence_available": True,
        "nessie_ref": "candidate-ref",
    }
    if fault == "failed":
        completed["status"] = "failed"
    elif fault == "rows":
        completed["result"]["rows"][0]["label"] = "changed"
    elif fault == "evidence":
        completed["evidence_available"] = False
    elif fault == "ref":
        completed["nessie_ref"] = "wrong-ref"
    responses = iter(
        [
            {"id": "query-19"},
            {"status": "queued"},
            completed,
            {},
            {},
            {"items": [] if fault == "history" else [{"id": "query-19"}]},
        ]
    )
    monkeypatch.setattr(observatory, "request", lambda *args, **kwargs: next(responses))
    monkeypatch.setattr(observatory, "time", SimpleNamespace(sleep=lambda _: None))
    if fault is not None:
        with pytest.raises(observatory.golden.CandidateError, match="evidence|history"):
            observatory.query_contract(
                "http://synthetic.invalid", {"env": "analytics", "nessie_ref": "candidate-ref"}
            )
    else:
        assert observatory.query_contract(
            "http://synthetic.invalid", {"env": "analytics", "nessie_ref": "candidate-ref"}
        ) == {
            "query_id": "query-19",
            "durable_evidence": True,
            "write_denied": True,
            "session_override_denied": True,
        }


@pytest.mark.parametrize("fault", ["cursor loop", "bounded pagination", "seeded commits"])
def test_history_rejects_incomplete_or_unbounded_pages(fault, observatory, monkeypatch):
    pages = iter(
        [
            {
                "items": [{"hash": str(i)}],
                "next_cursor": "repeated" if fault == "cursor loop" else str(i),
            }
            for i in range(10)
        ]
        if fault != "seeded commits"
        else [{"items": [{"hash": "only-one"}], "next_cursor": None}]
    )
    monkeypatch.setattr(observatory, "request", lambda _: next(pages))
    with pytest.raises(observatory.golden.CandidateError, match=fault):
        observatory.verify_history(
            "http://synthetic.invalid", {"env": "analytics", "nessie_ref": "candidate-ref"}
        )
