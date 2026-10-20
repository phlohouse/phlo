"""Public provisioning command preserves isolation, pins and private output."""

import json
import ssl

import certifi
import yaml
from click.testing import CliRunner

from phlo_trino.cli import trino_group


def test_preview_provision_is_opt_in_private_and_ref_pinned(tmp_path, monkeypatch):
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    monkeypatch.delenv("PHLO_V1_ENVIRONMENTS", raising=False)
    ca = ssl.create_default_context(cafile=certifi.where()).get_ca_certs(binary_form=True)[0]
    (tmp_path / "ca.pem").write_text(ssl.DER_cert_to_PEM_cert(ca), encoding="ascii")
    (tmp_path / "keystore").write_bytes(b"operator-keystore-config-only")
    (tmp_path / "keystore").chmod(0o600)
    (tmp_path / "password").write_text("private-disposable-password", encoding="utf-8")
    (tmp_path / "password").chmod(0o600)
    (tmp_path / "catalog.properties").write_text(
        "connector.name=iceberg\niceberg.catalog.type=rest\n"
        "iceberg.rest-catalog.uri=https://catalog.example/iceberg\n"
        "iceberg.rest-catalog.prefix=release_42\niceberg.security=ALLOW_ALL\n",
        encoding="utf-8",
    )
    (tmp_path / "catalog.properties").chmod(0o600)
    (tmp_path / "phlo.yaml").write_text(
        yaml.safe_dump(
            {
                "environments": {
                    "analytics": {
                        "dagster_location": "analytics",
                        "nessie_ref": "release_42",
                        "preview": {
                            "catalog": "preview_analytics",
                            "catalog_file": "catalog.properties",
                            "password_file": "password",
                        },
                    },
                    "ingestion": {"dagster_location": "ingestion", "nessie_ref": "main"},
                },
            }
        ),
        encoding="utf-8",
    )
    inputs = tmp_path / "preview.json"
    inputs.write_text(
        json.dumps(
            {
                "ca_file": "ca.pem",
                "keystore_file": "keystore",
                "keystore_password_file": "password",
                "network": "approved-network",
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "generated"
    arguments = ["preview", "provision", "--config", str(inputs), "--output", str(output)]
    runner = CliRunner()
    generated = runner.invoke(trino_group, arguments)
    assert generated.exit_code == 0, generated.output
    assert "private-disposable-password" not in generated.output
    assert output.stat().st_mode & 0o077 == 0
    assert all(path.stat().st_mode & 0o077 == 0 for path in output.rglob("*"))
    assert [path.name for path in (output / "trino/catalog").iterdir()] == [
        "preview_analytics.properties"
    ]
    catalog = (output / "trino/catalog/preview_analytics.properties").read_text()
    assert "iceberg.rest-catalog.prefix=release_42\n" in catalog
    assert "iceberg.security=READ_ONLY\n" in catalog
    compose = yaml.safe_load((output / "compose.yaml").read_text())
    assert set(compose["services"]) == {"trino-preview", "trino-preview-init"}
    assert compose["services"]["trino-preview"]["ports"] == ["127.0.0.1:10025:8443"]
    api = json.loads((output / "api-settings.json").read_text())
    assert not any("ASSERT" in key or "SINGLE" in key or "LIMITS" in key for key in api)
    assert json.loads(api["PHLO_V1_PREVIEW_CATALOGS"]) == {
        "analytics": {"catalog": "preview_analytics", "nessie_ref": "release_42"}
    }
    before = (output / "trino/config.properties").read_bytes()
    assert runner.invoke(trino_group, arguments).exit_code != 0
    assert (output / "trino/config.properties").read_bytes() == before
    (tmp_path / "catalog.properties").write_text(
        catalog.replace("release_42", "main"), encoding="utf-8"
    )
    rejected = runner.invoke(trino_group, arguments[:-1] + [str(tmp_path / "bad")])
    assert rejected.exit_code != 0
    assert not (tmp_path / "bad").exists()
