"""Render an isolated preview deployment without starting or changing services."""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import shutil
import ssl
from importlib.resources import files
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from phlo.config.env import resolve_project_root
from phlo.config.project import get_environment_targets


class PreviewProvisionConfig(BaseModel):
    """Operator inputs. Paths are relative to the input file, not the shell."""

    model_config = ConfigDict(extra="forbid")
    ca_file: Path
    keystore_file: Path
    keystore_password_file: Path
    network: str = Field(pattern=r"^[A-Za-z0-9_.-]+$")
    port: int = Field(default=10025, ge=1024, le=65535)


def _private_text(path: Path) -> str:
    if path.stat().st_mode & 0o077:
        raise ValueError("credential inputs must have mode 0600 or stricter")
    value = path.read_text(encoding="utf-8").rstrip("\n")
    if len(value) < 16 or any(character in value for character in "\r\n\x00"):
        raise ValueError("credentials must be single-line values of at least 16 characters")
    return value


def _catalog(path: Path, ref: str) -> str:
    if path.stat().st_mode & 0o077:
        raise ValueError("catalog credentials must have mode 0600 or stricter")
    properties: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, separator, value = line.partition("=")
        key = key.strip()
        if not separator or key in properties or "\\" in line or "\x00" in line:
            raise ValueError("catalog inputs require unique, unescaped key=value properties")
        properties[key] = value.strip()
    if (
        properties.get("connector.name") != "iceberg"
        or properties.get("iceberg.catalog.type") != "rest"
    ):
        raise ValueError("preview requires an Iceberg REST catalog")
    if properties.get("iceberg.rest-catalog.prefix") != ref:
        raise ValueError("catalog prefix must exactly match the configured Nessie ref")
    properties["iceberg.security"] = "READ_ONLY"
    return "".join(f"{key}={value}\n" for key, value in properties.items())


def provision_preview(config_file: Path, output: Path, project_root: Path | None = None) -> None:
    """Create a new private deployment directory, never overwrite an installation."""
    config = PreviewProvisionConfig.model_validate_json(config_file.read_text(encoding="utf-8"))
    base = config_file.resolve().parent
    root = resolve_project_root(project_root)
    targets = get_environment_targets(root)
    previews = {
        env: target.preview for env, target in targets.items() if target.preview is not None
    }
    if not previews or len({item.catalog for item in previews.values()}) != len(previews):
        raise ValueError("configure at least one preview with distinct catalogs in phlo.yaml")
    mapping = {
        env: {"catalog": item.catalog, "nessie_ref": targets[env].nessie_ref}
        for env, item in previews.items()
    }
    ca = (base / config.ca_file).read_bytes()
    ssl.create_default_context(cadata=ca.decode("ascii"))
    keystore_path = base / config.keystore_file
    if keystore_path.stat().st_mode & 0o077:
        raise ValueError("keystore must have mode 0600 or stricter")
    keystore = keystore_path.read_bytes()
    key_password = _private_text(base / config.keystore_password_file)
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", key_password):
        raise ValueError(
            "keystore password must be an alphanumeric token with optional ._- characters"
        )
    passwords = {
        env: _private_text(root / target.password_file) for env, target in previews.items()
    }
    if len(set(passwords.values())) != len(passwords):
        raise ValueError("each preview identity requires a distinct password")
    catalogs = {
        target.catalog: _catalog(root / target.catalog_file, targets[env].nessie_ref)
        for env, target in previews.items()
    }
    output.mkdir(mode=0o700)  # Fail if the destination already exists, including a symlink.
    try:
        _write_bundle(output, config, ca, keystore, key_password, passwords, catalogs, mapping)
    except BaseException:
        shutil.rmtree(output)
        raise


def _write_bundle(
    output: Path,
    config: PreviewProvisionConfig,
    ca: bytes,
    keystore: bytes,
    key_password: str,
    passwords: dict[str, str],
    catalogs: dict[str, str],
    mapping: dict[str, dict[str, str]],
) -> None:
    package = files("phlo_trino")

    def write(name: str, value: str | bytes) -> None:
        path = output / name
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with path.open("xb") as stream:
            path.chmod(0o600)
            stream.write(value.encode() if isinstance(value, str) else value)

    for name in ("jvm.config", "node.properties"):
        write(f"trino/{name}", (package / name).read_text(encoding="utf-8"))
    # The nested read-only bind mount needs an existing mountpoint.
    (output / "trino/secrets").mkdir(mode=0o700)
    template = (package / "preview/config.properties.template").read_text(encoding="utf-8")
    write(
        "trino/config.properties",
        template.replace("${ENV:TRINO_HTTPS_KEYSTORE_PASSWORD}", key_password).replace(
            "${ENV:TRINO_INTERNAL_SHARED_SECRET}", secrets.token_urlsafe(48)
        ),
    )
    for name in (
        "access-control.properties",
        "password-authenticator.properties",
        "resource-groups.properties",
    ):
        write(f"trino/{name}", (package / "preview" / name).read_text(encoding="utf-8"))
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
    write("trino/preview/access-control.json", json.dumps(access, indent=2))
    # All authenticated users on this dedicated server share the bounded group.
    groups = json.loads((package / "preview/resource-groups.json").read_text(encoding="utf-8"))
    groups["selectors"] = [{"group": "global.preview.api"}]
    write("trino/preview/resource-groups.json", json.dumps(groups, indent=2))
    for catalog, content in catalogs.items():
        write(f"trino/catalog/{catalog}.properties", content)
    entries = []
    for env, password in passwords.items():
        salt = secrets.token_bytes(16)
        # Trino 483 verifies PBKDF2WithHmacSHA1, with the stored hash's key length.
        digest = hashlib.pbkdf2_hmac("sha1", password.encode(), salt, 600_000, dklen=32)
        entries.append(f"phlo_api_preview_{env}:600000:{salt.hex()}:{digest.hex()}\n")
        write(f"secrets/password-{env}", password)
    write("secrets/preview-password.db", "".join(entries))
    write("secrets/preview-keystore.jks", keystore)
    write("secrets/ca.pem", ca)
    first = next(iter(passwords))
    write(
        "secrets/healthcheck.curl",
        f'cacert = "/etc/trino/secrets/ca.pem"\nuser = {json.dumps(f"phlo_api_preview_{first}:{passwords[first]}")}\n',
    )
    image = yaml.safe_load((package / "service.yaml").read_text(encoding="utf-8"))["image"]
    compose = {
        "services": {
            "trino-preview": {
                "image": image,
                "restart": "unless-stopped",
                "ports": [f"127.0.0.1:{config.port}:8443"],
                "mem_limit": "2g",
                "volumes": ["./trino:/etc/trino:ro", "./secrets:/etc/trino/secrets:ro"],
                "networks": ["preview"],
                "healthcheck": {
                    "test": [
                        "CMD-SHELL",
                        '[ "$(curl --config /etc/trino/secrets/healthcheck.curl --fail --silent --show-error https://trino-preview:8443/v1/info/state)" = \'"ACTIVE"\' ] && curl --config /etc/trino/secrets/healthcheck.curl --fail --silent --show-error https://trino-preview:8443/v1/query > /dev/null',
                    ],
                    "interval": "10s",
                    "timeout": "5s",
                    "retries": 10,
                    "start_period": "90s",
                },
            }
        },
        "networks": {"preview": {"external": True, "name": config.network}},
    }
    write("compose.yaml", yaml.safe_dump(compose, sort_keys=False))
    api = {
        "PHLO_V1_PREVIEW_TRINO_URL": "https://trino-preview:8443",
        "PHLO_V1_PREVIEW_TRINO_CA_FILE": str((output / "secrets/ca.pem").resolve()),
        "PHLO_V1_PREVIEW_TRINO_PASSWORD_FILES": json.dumps(
            {env: str((output / f"secrets/password-{env}").resolve()) for env in passwords}
        ),
        "PHLO_V1_PREVIEW_CATALOGS": json.dumps(mapping),
    }
    write("api-settings.json", json.dumps(api, indent=2))
    write(
        "README.txt",
        "Generated configuration only, not deployment evidence.\nReview paths/mounts in api-settings.json for the API runtime.\nTLS certificate must cover trino-preview; API must join the selected network.\nRun a single API process in one replica; active sessions do not survive restart.\nDo not set safety assertions until deployment enforcement has been verified.\nSecrets are private local files; import/mount through your approved secret store.\nContainer UID must be able to read these files without broadening permissions.\n",
    )
