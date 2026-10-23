"""Disposable-driver safety and protocol contracts, not live-stack qualification."""

import hashlib
import json
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest
import yaml


@pytest.fixture
def demo(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "scripts"))
    import observatory_demo

    return observatory_demo


@pytest.fixture
def config(tmp_path, demo):
    return demo.golden.RunConfig(
        repo_root=tmp_path,
        project_dir=tmp_path / "project",
        wheelhouse=tmp_path / "wheelhouse",
        operator_env=tmp_path / "operator",
        project_name="phlo-demo-contract",
    )


@pytest.fixture
def endpoint(monkeypatch, demo):
    requests = []
    responses = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            headers = {key.lower(): value for key, value in self.headers.items()}
            requests.append((self.command, self.path, headers, body))
            status, payload = responses.pop(0)
            self.send_response(status)
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode() if payload is not None else b"")

        do_POST = do_GET
        do_PUT = do_GET

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(
        demo.golden,
        "service_url",
        lambda config, service, port, suffix="": f"http://127.0.0.1:{server.server_port}{suffix}",
    )
    monkeypatch.setattr(demo.time, "sleep", lambda seconds: None)
    try:
        yield requests, responses
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def test_http_json_csv_denial_and_redacted_errors(demo, config, endpoint):
    requests, responses = endpoint
    responses.extend([(201, {"id": "upload"}), (200, None), (403, {"denied": True})])
    client = demo.DemoClient(config, "private-token")
    assert client.request("uploads/csv", raw=b"id\n17\n", expected=(201,)) == {"id": "upload"}
    assert client.request("drafts?ref=candidate", {"sql": "SELECT 17"}, method="PUT") == {}
    assert client.request("queries", {"sql": "SELECT 1"}, expected=(403,)) == {"denied": True}
    method, path, headers, body = requests[0]
    assert (method, path, body) == ("POST", "/api/v1/uploads/csv?env=sandbox", b"id\n17\n")
    assert headers["content-type"] == "text/csv"
    assert headers["x-phlo-upload-name"] == "stability.csv"
    assert headers["authorization"] == "Bearer private-token"
    assert requests[1][0:2] == ("PUT", "/api/v1/drafts?ref=candidate&env=sandbox")
    assert json.loads(requests[1][3]) == {"sql": "SELECT 17"}
    responses.append((503, {"secret": "do-not-disclose"}))
    with pytest.raises(demo.golden.CandidateError, match="HTTP 503") as error:
        client.request("health")
    assert "do-not-disclose" not in str(error.value)
    assert "private-token" not in str(error.value)


@pytest.mark.parametrize("terminal", ["completed", "failed", "cancelled", "running"])
def test_query_waits_for_success_and_bounds_other_outcomes(demo, config, endpoint, terminal):
    requests, responses = endpoint
    result = {"id": "query-17", "status": terminal, "result": {"rows": [{"count": 17}]}}
    responses.append((202, {"id": "query-17"}))
    responses.append((200, {"id": "query-17", "status": "pending"}))
    responses.extend([(200, result)] * 300)
    client = demo.DemoClient(config, "token")
    if terminal == "completed":
        assert client.query("SELECT 17") == result
    else:
        message = "bounded polling" if terminal == "running" else "did not complete successfully"
        with pytest.raises(demo.golden.CandidateError, match=message):
            client.query("SELECT 17")
    assert json.loads(requests[0][3]) == {"sql": "SELECT 17", "row_limit": 25}
    assert all(request[1] == "/api/v1/queries/query-17?env=sandbox" for request in requests[1:])
    assert len(requests) == (301 if terminal == "running" else 3)


def test_generated_ports_are_ephemeral_loopback_and_overrides_are_checked(
    demo, config, monkeypatch
):
    config.compose_file.parent.mkdir(parents=True)
    config.compose_file.write_text(
        yaml.safe_dump(
            {"services": {"api": {"ports": ["4000:4000", "127.0.0.1:9000:9001/tcp"]}, "worker": {}}}
        )
    )
    monkeypatch.setattr(
        demo.golden,
        "compose_config_json",
        lambda config: yaml.safe_load(config.compose_file.read_text()),
    )
    demo.loopback_ports(config)
    services = yaml.safe_load(config.compose_file.read_text())["services"]
    assert services["api"]["ports"] == [
        {"target": 4000, "published": "0", "host_ip": "127.0.0.1"},
        {"target": 9001, "published": "0", "host_ip": "127.0.0.1"},
    ]
    assert services["worker"]["ports"] == []
    for unsafe in (
        {"network_mode": "host"},
        {"ports": [{"target": 4000}]},
        {"ports": [{"host_ip": "0.0.0.0"}]},
    ):
        monkeypatch.setattr(
            demo.golden,
            "compose_config_json",
            lambda config, unsafe=unsafe: {"services": {"override": unsafe}},
        )
        with pytest.raises(demo.golden.CandidateError, match="loopback-only"):
            demo.verify_loopback(config)


@pytest.mark.parametrize("terminal", ["SUCCESS", "FAILURE", "CANCELED", "STARTED", "PythonError"])
def test_fixture_uses_live_origin_and_requires_success(demo, config, endpoint, terminal):
    requests, responses = endpoint
    responses.append(
        (
            200,
            {
                "data": {
                    "repositoriesOrError": {
                        "nodes": [
                            {"name": "not-assets", "pipelines": []},
                            {
                                "name": "live-repo",
                                "location": {"name": "live-location"},
                                "pipelines": [{"name": "__ASSET_JOB"}],
                            },
                        ]
                    }
                }
            },
        )
    )
    launch = (
        {"__typename": "PythonError", "message": "launch failed", "stack": ["private stack"]}
        if terminal == "PythonError"
        else {"__typename": "LaunchRunSuccess", "run": {"runId": "fixture-17"}}
    )
    responses.append((200, {"data": {"launchRun": launch}}))
    responses.extend(
        [(200, {"data": {"runOrError": {"__typename": "Run", "status": terminal}}})] * 180
    )
    if terminal == "SUCCESS":
        assert demo.fixture_run(config) == "fixture-17"
    else:
        message = (
            "not launched"
            if terminal == "PythonError"
            else "bounded polling"
            if terminal == "STARTED"
            else "materialization failed"
        )
        with pytest.raises(demo.golden.CandidateError, match=message) as error:
            demo.fixture_run(config)
        if terminal == "PythonError":
            assert "private stack" in str(error.value)
    launch_request = json.loads(requests[1][3])
    assert launch_request["variables"]["params"] == {
        "selector": {
            "repositoryLocationName": "live-location",
            "repositoryName": "live-repo",
            "pipelineName": "mission_control_full",
        },
        "runConfigData": {},
        "executionMetadata": {"tags": [{"key": "phlo/ref", "value": "main"}]},
    }
    assert "... on PythonError{message stack}" in launch_request["query"]
    assert len(requests) == (
        182 if terminal == "STARTED" else 2 if terminal == "PythonError" else 3
    )


@pytest.mark.parametrize("keep", [False, True])
def test_bootstrap_failure_reports_cleanup_or_private_diagnostics(
    demo, tmp_path, monkeypatch, keep
):
    binary = tmp_path / "native"
    binary.write_bytes(b"verified binary")
    root = tmp_path / "runtime"
    root.mkdir()
    monkeypatch.setattr(demo.tempfile, "mkdtemp", lambda **kwargs: str(root))
    monkeypatch.setattr(demo.golden, "run", lambda *args, **kwargs: None)

    def fail_prepare(*args):
        raise RuntimeError("private GraphQL error body")

    monkeypatch.setattr(demo, "prepare", fail_prepare)
    cleaned = []
    monkeypatch.setattr(
        demo.golden, "cleanup", lambda config, owned_paths: cleaned.extend(owned_paths) or []
    )
    report = tmp_path / "report.json"
    argv = [
        "--run-disposable",
        "--native-binary",
        str(binary),
        "--native-sha256",
        hashlib.sha256(binary.read_bytes()).hexdigest(),
        "--report",
        str(report),
    ]
    with pytest.raises(RuntimeError, match="private GraphQL"):
        demo.main(argv + (["--keep-project"] if keep else []))
    evidence = json.loads(report.read_text())
    assert evidence["result"] == "failed"
    assert evidence["stage"] == "bootstrap"
    assert evidence["project_retained"] is keep
    assert "private GraphQL" not in report.read_text()
    if keep:
        diagnostics = Path(evidence["private_diagnostics"])
        assert diagnostics.read_text() == "private GraphQL error body"
        assert diagnostics.stat().st_mode & 0o777 == 0o600
        assert not cleaned
    else:
        assert cleaned == [root]
        assert evidence["cleanup"] == "removed owned services, volumes and private files"
        assert not (root / "failure.log").exists()


def test_demo_requires_explicit_write_opt_in(tmp_path):
    script = Path(__file__).resolve().parents[2] / "scripts/observatory_demo.py"
    report = tmp_path / "report.json"
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--native-binary",
            str(tmp_path / "absent"),
            "--native-sha256",
            "0" * 64,
            "--report",
            str(report),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "--run-disposable is required" in result.stderr
    assert not report.exists()


def test_demo_rejects_unverified_binary_before_runtime_creation(tmp_path):
    script = Path(__file__).resolve().parents[2] / "scripts/observatory_demo.py"
    binary = tmp_path / "engine"
    binary.write_bytes(b"not the verified native engine")
    report = tmp_path / "report.json"
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--run-disposable",
            "--native-binary",
            str(binary),
            "--native-sha256",
            "0" * 64,
            "--report",
            str(report),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "Native binary is missing or its SHA256 differs" in result.stderr
    assert not report.exists()
