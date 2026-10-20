"""Acceptance-driver contracts, not live installed-artifact qualification.

These tests need the normal workspace dependencies. The separate Windows
golden-path portability harness intentionally needs only pytest and stdlib.
"""

import json
import os
import sys
from contextlib import nullcontext
from dataclasses import replace
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


def test_private_acceptance_files_are_exclusive_and_private(tmp_path, observatory):
    path = tmp_path / "private" / "backup"
    observatory.private_file(path, "original")
    with pytest.raises(FileExistsError):
        observatory.private_file(path, "replacement")
    assert path.read_text() == "original"
    if os.name == "posix":
        assert path.stat().st_mode & 0o777 == 0o600
        assert path.parent.stat().st_mode & 0o777 == 0o700


def test_initialize_runs_real_cli_and_persists_onboarding_and_upgrade_contract(
    config, observatory, monkeypatch
):
    """Prove workspace CLI compatibility, not released-artifact qualification."""
    config = replace(
        config,
        operator_env=Path(sys.executable).parent.parent,
        project_dir=config.project_dir.parent / config.project_name,
    )
    monkeypatch.setattr(observatory.golden, "pin_candidate_images", lambda config: None)

    observatory.initialize(config)

    project = yaml.safe_load((config.project_dir / "phlo.yaml").read_text())
    assert project["name"] == config.project_name
    assert any((config.project_dir / "workflows").rglob("*.py"))
    assert project["authentication"]["provider"] == "static"
    assert project["authentication"]["static"]["enabled"] is True
    assert project["authentication"]["static"]["dev_mode"] is True
    assert project["services"]["observatory"]["enabled"] is True
    assert project["api"]["authorization"]["mode"] == "required"
    assert project["api"]["authorization"]["backend"] == "default"
    assert project["default_environment"] in project["environments"]
    baseline = json.loads((config.project_dir / ".phlo/upgrade-baseline.json").read_text())
    assert ".phlo/docker-compose.yml" in baseline["files"]


@pytest.mark.parametrize(
    ("title", "text"),
    [
        ("Couldn't load this page", "Sign in to access Phlo."),
        ("Overview", "Couldn't load this page"),
        ("Overview", "Sign in to access Phlo."),
    ],
)
def test_browser_rejects_packaged_error_and_sign_in_pages(
    config, observatory, monkeypatch, tmp_path, title, text
):
    """A rendered heading is not proof of a usable route or actual sign-in."""
    monkeypatch.setattr(observatory.golden, "service_url", lambda *args: "http://synthetic.invalid")
    monkeypatch.setattr(
        observatory,
        "_browser",
        lambda session, *args: {"result": {"title": title, "text": text}},
    )
    with pytest.raises(observatory.golden.CandidateError, match="route failed"):
        observatory.browser_journey(
            config, {"env": "analytics", "nessie_ref": "main"}, tmp_path / "screenshots"
        )


def test_preview_layer_preserves_provider_private_volumes(config, observatory, monkeypatch):
    """Layer transformation only; this does not verify image UID access."""
    overrides = config.project_dir / ".phlo/overrides"
    overrides.mkdir(parents=True)
    layer_path = overrides / "compose.yaml"
    layer_path.write_text(
        "volumes:\n  retained-data: {}\nservices:\n  phlo-api:\n"
        "    environment:\n      RETAIN_ME: 'yes'\n"
        "    depends_on:\n      postgres:\n        condition: service_healthy\n"
        "    volumes:\n      - retained-data:/retained\n"
    )
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
    monkeypatch.setattr(
        observatory.golden, "run", lambda *args, **kwargs: SimpleNamespace(stdout="[100,101]")
    )
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
    monkeypatch.setattr(observatory, "compose", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        observatory.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0)
    )
    observatory.provision_preview(config, {"env": "analytics"})

    result = yaml.safe_load(layer_path.read_text())
    assert result["volumes"] == {
        "retained-data": {},
        "preview-config": {},
        "preview-api-secrets": {},
    }
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
        "retained-data:/retained",
        "preview-api-secrets:/opt/phlo-preview/api-secrets:ro",
    ]
    api = result["services"]["phlo-api"]
    assert api["environment"]["RETAIN_ME"] == "yes"
    assert api["depends_on"]["postgres"] == {"condition": "service_healthy"}
    assert api["depends_on"]["phlo-api-preview-init"] == {
        "condition": "service_completed_successfully"
    }
    private_init = result["services"]["phlo-api-preview-init"]
    assert private_init["network_mode"] == "none"
    assert private_init["read_only"] is True
    assert private_init["volumes"] == [
        f"{output}/api-secrets:/source:ro",
        "preview-api-secrets:/target",
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


@pytest.mark.parametrize(
    ("capability", "state"),
    [
        ("query_preview", "configured"),
        ("query_preview", "ready"),
        ("query_preview", "disabled"),
        ("query_preview", "unavailable"),
        ("query_preview", "empty"),
        ("query_preview", "unprobed"),
        ("orchestration", "configured"),
        ("incidents", "unavailable"),
        ("query_usage", "disabled"),
    ],
)
def test_readiness_separates_mandatory_health_from_preview_configuration(
    capability, state, observatory, monkeypatch
):
    capabilities = {
        name: [{"state": "ready"}] for name in ("orchestration", "incidents", "query_usage")
    }
    capabilities["query_preview"] = [{"state": "configured", "reason_code": "backend_reachable"}]
    capabilities[capability] = (
        []
        if state == "empty"
        else [
            {
                "state": "configured" if state == "unprobed" else state,
                "reason_code": "enforcement_unverified"
                if state == "unprobed"
                else "backend_reachable",
            }
        ]
    )
    report = {"capabilities": capabilities}
    monkeypatch.setattr(observatory, "request", lambda *_: report)
    if (capability, state) == ("query_preview", "configured"):
        assert (
            observatory.verify_readiness("http://synthetic.invalid", {"env": "analytics"}) == report
        )
    else:
        with pytest.raises(observatory.golden.CandidateError, match=capability):
            observatory.verify_readiness("http://synthetic.invalid", {"env": "analytics"})


@pytest.mark.parametrize("outage", [True, False])
@pytest.mark.parametrize("authorization_unavailable", [True, False])
def test_database_outage_requires_db_failure_not_configured_preview(
    config, observatory, monkeypatch, outage, authorization_unavailable
):
    capabilities = {
        name: [
            {
                "state": "unavailable" if outage else "ready",
                "reason_code": "backend_unavailable" if outage else "ready",
            }
        ]
        for name in ("incidents", "query_usage")
    }
    capabilities.update(
        {"orchestration": [{"state": "ready"}], "query_preview": [{"state": "configured"}]}
    )
    monkeypatch.setattr(observatory.golden, "service_url", lambda *args: "http://synthetic.invalid")
    monkeypatch.setattr(
        observatory,
        "compose",
        lambda *args, **kwargs: SimpleNamespace(
            stdout=json.dumps({"capabilities": capabilities})
            if "exec" in args
            else "api-container\n"
        ),
    )
    monkeypatch.setattr(observatory, "wait_ready", lambda *args: None)
    monkeypatch.setattr(observatory, "verify_data", lambda *args: None)
    monkeypatch.setattr(
        observatory,
        "request",
        lambda url, **kwargs: (
            {"result": {"rows": observatory.ROWS}, "evidence_available": True}
            if "/queries/" in url
            else {"error": {"code": "authorization_unavailable"}}
            if authorization_unavailable
            else {"capabilities": capabilities}
        ),
    )
    if outage:
        assert observatory.recover(config, {"env": "analytics"}, "q19")["feature_outage_detected"]
    else:
        with pytest.raises(observatory.golden.CandidateError, match="outage was not detected"):
            observatory.recover(config, {"env": "analytics"}, "q19")


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


def test_oidc_setup_has_distinct_targets_private_credentials_and_no_dev_identity(
    config, observatory, monkeypatch
):
    """Fixture provisioning contract only, not a login or artifact qualification."""
    import release_observatory_oidc as oidc
    from click.testing import CliRunner

    from phlo.cli.commands.services.init import init_cmd
    from phlo.config_schema import ProjectConfig

    config.project_dir.mkdir(parents=True)
    monkeypatch.chdir(config.project_dir)
    initialized = CliRunner().invoke(init_cmd, ["--observatory-dev"])
    assert initialized.exit_code == 0, initialized.output
    overrides = config.project_dir / ".phlo/overrides/compose.yaml"
    overrides.write_text("services: {}\n")
    monkeypatch.setattr(
        observatory.golden,
        "compose_config_json",
        lambda _: {
            "networks": {"default": {"name": "owned-test-network"}},
            "services": {"phlo-api": {"image": "example.invalid/api@sha256:synthetic"}},
        },
    )
    monkeypatch.setattr(
        observatory.golden, "service_url", lambda *args: "https://synthetic.invalid"
    )
    monkeypatch.setattr(observatory, "request", lambda *args, **kwargs: {"hash": "a" * 64})

    def installed(_config, source, *args):
        monkeypatch.setattr(sys, "argv", ["-c", *args])
        exec(source, {})
        return {}

    monkeypatch.setattr(observatory, "installed", installed)
    root, issuer, _base, private_ref = oidc.prepare(
        config, {"env": "sandbox", "nessie_ref": "main"}, "example.invalid/proxy@sha256:synthetic"
    )
    project = ProjectConfig.model_validate(
        yaml.safe_load((config.project_dir / "phlo.yaml").read_text())
    )
    assert project.authentication.provider == "jwt"
    assert project.environments["denied"].nessie_ref == private_ref != "main"
    assert project.environments["denied"].dagster_location == private_ref
    assert project.environments["denied"].compose_project is None
    assert project.services.overrides["phlo-api"].environment["PHLO_AUTH_DEV_MODE"] == "false"
    fixture = json.loads((root / "fixture.json").read_text())
    proxy = fixture["services"]["login-proxy"]
    assert proxy["environment"]["OAUTH2_PROXY_OIDC_ISSUER_URL"] == issuer
    assert proxy["environment"]["OAUTH2_PROXY_COOKIE_SECURE"] == "true"
    assert "OAUTH2_PROXY_SSL_INSECURE_SKIP_VERIFY" not in proxy["environment"]
    assert proxy["environment"]["OAUTH2_PROXY_CLIENT_SECRET"] not in json.dumps(
        [definition.get("command", []) for definition in fixture["services"].values()]
    )
    if os.name == "posix":
        assert (root / "fixture.json").stat().st_mode & 0o077 == 0
        assert (root / "login.json").stat().st_mode & 0o077 == 0


@pytest.mark.parametrize("fault", [None, "identity", "denial", "receipt"])
def test_oidc_acceptance_requires_verified_role_denial_and_honest_receipt(
    config, observatory, monkeypatch, tmp_path, fault
):
    """Controlled driver boundary; real browser/TLS proof is a separate run."""
    import release_observatory_oidc as oidc

    root = tmp_path / "owned-oidc"
    root.mkdir()
    (root / "public").mkdir()
    (root / "login.json").write_text(json.dumps({"password": "synthetic-private-password"}))
    receipt = config.project_dir / ".phlo/observatory-ready.json"
    receipt.parent.mkdir(parents=True)
    receipt.write_text(
        json.dumps(
            {
                "state": "ready" if fault == "receipt" else "verification_required",
                "browser_login_verified": False,
            }
        )
    )
    monkeypatch.setattr(
        oidc,
        "prepare",
        lambda *args: (root, "https://issuer.invalid", "https://ui.invalid", "actual-private"),
    )
    monkeypatch.setattr(oidc.Path, "home", lambda: tmp_path / "home")
    monkeypatch.setattr(oidc.ssl, "create_default_context", lambda **kwargs: None)
    monkeypatch.setattr(oidc.urllib.request, "urlopen", lambda *args, **kwargs: nullcontext())
    monkeypatch.setattr(
        oidc.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(stdout="", stderr="", returncode=0),
    )
    monkeypatch.setattr(oidc.golden, "service_url", lambda *args: "https://dagster.invalid")
    monkeypatch.setattr(
        oidc.golden,
        "graphql",
        lambda *args: {
            "data": {"repositoriesOrError": {"nodes": [{"location": {"name": "actual-private"}}]}}
        },
    )
    monkeypatch.setattr(observatory, "compose", lambda *args, **kwargs: None)

    def browser(_session, *args):
        if args[0] == "eval":
            if "/api/v1/me" in args[1]:
                return {
                    "result": {
                        "status": 200,
                        "body": {
                            "subject": oidc.VIEWER,
                            "roles": ["admin" if fault == "identity" else "viewer"],
                        },
                    }
                }
            return {
                "result": 401 if "/oauth2/auth" in args[1] else 200 if fault == "denial" else 403
            }
        return {}

    monkeypatch.setattr(observatory, "_browser", browser)
    if fault:
        with pytest.raises(observatory.golden.CandidateError, match="viewer|receipt|browser login"):
            oidc.run(config, {"env": "sandbox"}, tmp_path, "synthetic-proxy")
    else:
        result = oidc.run(config, {"env": "sandbox"}, tmp_path, "synthetic-proxy")
        assert result["api_jwt_verified"] and result["browser_password_login"]
        assert result["canonical_role"] == "viewer"
        assert result["environment_denied"] and result["admin_denied"]
        assert result["logout_denied"] and result["api_no_token_denied"]
