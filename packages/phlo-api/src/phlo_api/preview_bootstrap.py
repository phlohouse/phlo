"""Make the credentials for the preview Trino that phlo adds to a generated stack.

``phlo services init`` adds a read-only ``trino-preview`` service whenever the
API and Trino are installed, and points the API at it. On each start the API
runs this module (``python -m phlo_api.preview_bootstrap``) to make, in a
private folder it owns:

* ``api/password-<env>``: one random password per environment, kept across
  restarts and readable only by the API user;
* ``api/ca.pem`` and ``server/ca.pem``: a local CA the API trusts;
* ``server/server.pem``: the server key and certificate for ``trino-preview``,
  signed by that CA (the CA key is thrown away once it has signed);
* ``server/password.db``: PBKDF2 hashes of the passwords, never the passwords;
* ``server/access-control.json`` and ``server/targets``: each environment's
  identity may only read its own catalog, pinned to the environment's ref.

The preview container copies ``server/`` and derives the catalogs. Nothing is
made unless ``PHLO_V1_PREVIEW_STATE_DIR`` and the catalog map are set.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from phlo_api.settings import get_process_settings

_HOST = "trino-preview"
_ENV_ID = re.compile(r"[a-z][a-z0-9_-]{0,62}")
_CATALOG = re.compile(r"[a-z_][a-z0-9_]*")
_REF = re.compile(r"[A-Za-z0-9._-]+")
# Renew the server certificate once it has less than this left.
_RENEW_SECONDS = 30 * 24 * 3600


def _mapping(raw: str) -> dict[str, dict[str, str]]:
    mapping = json.loads(raw)
    if not isinstance(mapping, dict) or not mapping:
        raise ValueError("the preview catalog map is empty")
    for env, target in mapping.items():
        if (
            not isinstance(env, str)
            or not _ENV_ID.fullmatch(env)
            or not isinstance(target, dict)
            or set(target) != {"catalog", "nessie_ref"}
            or not _CATALOG.fullmatch(str(target["catalog"]))
            or not _REF.fullmatch(str(target["nessie_ref"]))
        ):
            raise ValueError(f"the preview catalog map entry for {env!r} is invalid")
    if len({target["catalog"] for target in mapping.values()}) != len(mapping):
        raise ValueError("each environment needs its own preview catalog")
    return mapping


def _write(path: Path, value: str | bytes) -> None:
    """Replace a file atomically, private to the API user from the first byte."""
    data = value.encode() if isinstance(value, str) else value
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _private_dir(path: Path) -> Path:
    if path.is_symlink():
        raise ValueError(f"{path} must not be a symlink")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.chmod(0o700)
    return path


def _passwords(api: Path, envs: list[str]) -> dict[str, str]:
    passwords: dict[str, str] = {}
    for env in envs:
        path = api / f"password-{env}"
        value = path.read_text(encoding="utf-8").strip() if path.is_file() else ""
        if len(value) < 32:
            value = secrets.token_urlsafe(32)
            _write(path, value + "\n")
        passwords[env] = value
    for stale in api.glob("password-*"):
        if stale.name.removeprefix("password-") not in passwords:
            stale.unlink()
    return passwords


def _password_db(passwords: dict[str, str]) -> str:
    lines = []
    for env, password in passwords.items():
        salt = secrets.token_bytes(16)
        # Trino 483 verifies PBKDF2WithHmacSHA1 with the stored hash's key length.
        digest = hashlib.pbkdf2_hmac("sha1", password.encode(), salt, 600_000, dklen=32)
        lines.append(f"phlo_api_preview_{env}:600000:{salt.hex()}:{digest.hex()}\n")
    return "".join(lines)


def _openssl(*args: str, cwd: Path) -> None:
    subprocess.run(["openssl", *args], cwd=cwd, check=True, capture_output=True)


def _certificate_is_current(server: Path, api: Path) -> bool:
    certificate, ca = server / "server.pem", api / "ca.pem"
    if not certificate.is_file() or not ca.is_file():
        return False
    checks = (
        ["x509", "-in", str(certificate), "-noout", "-checkend", str(_RENEW_SECONDS)],
        ["x509", "-in", str(certificate), "-noout", "-checkhost", _HOST],
        ["verify", "-CAfile", str(ca), str(certificate)],
    )
    for check in checks:
        result = subprocess.run(["openssl", *check], capture_output=True, text=True)
        if result.returncode != 0 or "does NOT match" in result.stdout:
            return False
    return True


def _make_certificate(server: Path, api: Path) -> None:
    with tempfile.TemporaryDirectory() as folder:
        work = Path(folder)
        (work / "server.ext").write_text(
            f"subjectAltName=DNS:{_HOST},DNS:localhost\n"
            "basicConstraints=critical,CA:FALSE\n"
            "keyUsage=critical,digitalSignature,keyEncipherment\n"
            "extendedKeyUsage=serverAuth\n",
            encoding="utf-8",
        )
        _openssl(
            "req",
            "-x509",
            "-newkey",
            "rsa:3072",
            "-nodes",
            "-sha256",
            "-days",
            "3650",
            "-subj",
            "/CN=phlo local query preview CA",
            "-addext",
            "basicConstraints=critical,CA:TRUE",
            "-addext",
            "keyUsage=critical,keyCertSign,cRLSign",
            "-keyout",
            "ca.key",
            "-out",
            "ca.pem",
            cwd=work,
        )
        _openssl(
            "req",
            "-newkey",
            "rsa:3072",
            "-nodes",
            "-sha256",
            "-subj",
            f"/CN={_HOST}",
            "-keyout",
            "server.key",
            "-out",
            "server.csr",
            cwd=work,
        )
        _openssl(
            "x509",
            "-req",
            "-in",
            "server.csr",
            "-CA",
            "ca.pem",
            "-CAkey",
            "ca.key",
            "-CAcreateserial",
            "-sha256",
            "-days",
            "397",
            "-extfile",
            "server.ext",
            "-out",
            "server.crt",
            cwd=work,
        )
        ca = (work / "ca.pem").read_bytes()
        key = (work / "server.key").read_bytes()
        chain = (work / "server.crt").read_bytes() + ca
    _write(server / "server.pem", key + chain)
    _write(server / "ca.pem", ca)
    _write(api / "ca.pem", ca)


def _access_control(mapping: dict[str, dict[str, str]]) -> str:
    access = {
        "catalogs": [
            {
                "user": f"phlo_api_preview_{env}",
                "catalog": re.escape(target["catalog"]),
                "allow": "read-only",
            }
            for env, target in mapping.items()
        ]
        + [{"catalog": ".*", "allow": "none"}],
        "system_session_properties": [{"allow": False}],
        "catalog_session_properties": [{"allow": False}],
        "queries": [{"allow": ["execute"]}],
    }
    return json.dumps(access, indent=2)


def bootstrap(state_dir: Path, raw_mapping: str) -> None:
    """Make or refresh every file the preview server and the API need."""
    mapping = _mapping(raw_mapping)
    root = _private_dir(state_dir)
    api = _private_dir(root / "api")
    server = _private_dir(root / "server")
    passwords = _passwords(api, list(mapping))
    if not _certificate_is_current(server, api):
        _make_certificate(server, api)
    _write(server / "password.db", _password_db(passwords))
    _write(server / "access-control.json", _access_control(mapping))
    _write(
        server / "targets",
        "".join(f"{target['catalog']} {target['nessie_ref']}\n" for target in mapping.values()),
    )


def main() -> int:
    settings = get_process_settings()
    if not settings.phlo_v1_preview_state_dir or not settings.phlo_v1_preview_catalogs:
        return 0
    if shutil.which("openssl") is None:
        print("Query preview setup needs openssl in the API image.", file=sys.stderr)
        return 1
    try:
        bootstrap(Path(settings.phlo_v1_preview_state_dir), settings.phlo_v1_preview_catalogs)
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"Query preview setup failed: {exc}", file=sys.stderr)
        return 1
    # stderr: one-off commands run through this entrypoint print JSON on stdout.
    print("Query preview credentials are ready.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
