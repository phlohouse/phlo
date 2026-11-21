"""The API makes the generated preview Trino's certificate and passwords on start."""

from __future__ import annotations

import hashlib
import json
import shutil
import ssl
import subprocess
from pathlib import Path

import pytest

from phlo_api.preview_bootstrap import bootstrap

pytestmark = pytest.mark.skipif(shutil.which("openssl") is None, reason="needs openssl")

MAPPING = json.dumps(
    {
        "sandbox": {"catalog": "preview_sandbox", "nessie_ref": "main"},
        "pre-prod": {"catalog": "preview_pre_prod", "nessie_ref": "release_1"},
    }
)


def _mode(path: Path) -> int:
    return path.stat().st_mode & 0o777


def test_makes_private_credentials_the_server_can_verify(tmp_path: Path) -> None:
    """Verify passwords, hashes, certificate and access rules agree and stay private."""
    bootstrap(tmp_path, MAPPING)
    api, server = tmp_path / "api", tmp_path / "server"
    assert _mode(tmp_path) == _mode(api) == _mode(server) == 0o700
    for path in [*api.iterdir(), *server.iterdir()]:
        assert _mode(path) == 0o600, path

    password = (api / "password-sandbox").read_text(encoding="utf-8").strip()
    entries = dict(line.split(":", 1) for line in (server / "password.db").read_text().splitlines())
    iterations, salt, digest = entries["phlo_api_preview_sandbox"].split(":")
    expected = hashlib.pbkdf2_hmac(
        "sha1", password.encode(), bytes.fromhex(salt), int(iterations), 32
    )
    assert expected.hex() == digest
    assert password not in (server / "password.db").read_text()

    # The certificate chains to the CA the API trusts and names the service host.
    subprocess.run(
        ["openssl", "verify", "-CAfile", str(api / "ca.pem"), str(server / "server.pem")],
        check=True,
        capture_output=True,
    )
    hosts = subprocess.run(
        ["openssl", "x509", "-in", str(server / "server.pem"), "-noout", "-ext", "subjectAltName"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert "DNS:trino-preview" in hosts
    ssl.create_default_context(cafile=str(api / "ca.pem"))
    assert not list(tmp_path.rglob("*.key"))

    rules = json.loads((server / "access-control.json").read_text())
    assert rules["catalogs"][0] == {
        "user": "phlo_api_preview_sandbox",
        "catalog": "preview_sandbox",
        "allow": "read-only",
    }
    assert rules["catalogs"][-1] == {"catalog": ".*", "allow": "none"}
    assert (server / "targets").read_text() == "preview_sandbox main\npreview_pre_prod release_1\n"


def test_restart_keeps_passwords_and_certificate(tmp_path: Path) -> None:
    """Verify a restart reuses what exists and drops removed environments."""
    bootstrap(tmp_path, MAPPING)
    password = (tmp_path / "api/password-sandbox").read_text()
    certificate = (tmp_path / "server/server.pem").read_bytes()
    only_sandbox = json.dumps({"sandbox": json.loads(MAPPING)["sandbox"]})
    bootstrap(tmp_path, only_sandbox)
    assert (tmp_path / "api/password-sandbox").read_text() == password
    assert (tmp_path / "server/server.pem").read_bytes() == certificate
    assert not (tmp_path / "api/password-pre-prod").exists()


@pytest.mark.parametrize(
    "mapping",
    [
        "{}",
        json.dumps({"Prod": {"catalog": "p", "nessie_ref": "main"}}),
        json.dumps({"prod": {"catalog": "p;drop", "nessie_ref": "main"}}),
        json.dumps({"prod": {"catalog": "p", "nessie_ref": "main ref"}}),
        json.dumps(
            {
                "a": {"catalog": "same", "nessie_ref": "main"},
                "b": {"catalog": "same", "nessie_ref": "dev"},
            }
        ),
    ],
)
def test_refuses_an_unsafe_catalog_map(tmp_path: Path, mapping: str) -> None:
    """Verify names that would reach Trino config unescaped are refused before anything is made."""
    with pytest.raises(ValueError):
        bootstrap(tmp_path, mapping)
    assert not (tmp_path / "server").exists()


def test_entrypoint_status_stays_off_stdout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """One-off commands through the API entrypoint (migrations --json) own stdout."""
    import phlo_api.preview_bootstrap as module

    settings = type(
        "Settings",
        (),
        {"phlo_v1_preview_state_dir": str(tmp_path), "phlo_v1_preview_catalogs": "{}"},
    )()
    monkeypatch.setattr(module, "get_process_settings", lambda: settings)
    monkeypatch.setattr(module, "bootstrap", lambda *_args: None)

    assert module.main() == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "credentials are ready" in captured.err
