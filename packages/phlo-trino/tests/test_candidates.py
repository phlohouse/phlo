"""Candidate catalogs are newly created and file-attested, never silently adopted."""

from types import SimpleNamespace
import pytest
from phlo_trino import candidates

ID = "a" * 32
REF = "sandbox-upload-" + ID


@pytest.fixture
def provider(monkeypatch, tmp_path):
    directory = tmp_path / ".phlo/trino/catalog"
    directory.mkdir(parents=True)
    (directory / "iceberg.properties").write_text(
        "connector.name=iceberg\niceberg.catalog.type=rest\niceberg.rest-catalog.uri=http://nessie/iceberg\niceberg.rest-catalog.warehouse=warehouse\n"
    )
    monkeypatch.setattr(
        candidates,
        "get_settings",
        lambda root: SimpleNamespace(
            trino_catalog="iceberg", trino_host="primary", trino_port=8080
        ),
    )
    calls = []

    class Cursor:
        def execute(self, sql, params=None):
            calls.append((sql, params))
            if sql.startswith("CREATE"):
                assert "IF NOT EXISTS" not in sql
                (directory / f"iceberg_candidate_{ID}.properties").write_text(
                    (directory / "iceberg.properties").read_text()
                    + f"iceberg.rest-catalog.prefix={REF}\n"
                )

        def fetchall(self):
            return [["iceberg_candidate_" + ID]]

    class Client:
        def cursor(self):
            return Cursor()

        def close(self):
            pass

    monkeypatch.setattr(candidates, "_connect", lambda *args: Client())
    return tmp_path, directory, calls


def test_fresh_catalog_attests_config_and_rejects_adoption_or_changed_prefix(provider):
    root, directory, calls = provider
    binding = candidates.create_catalog(root, ID, REF)
    assert '"iceberg.rest-catalog.prefix"' in calls[0][0] and REF in calls[0][0]
    candidates.verify_catalog(
        root, ID, REF, binding["catalog"], binding["catalog_properties_digest"]
    )
    with pytest.raises(ValueError, match="adopted"):
        candidates.create_catalog(root, ID, REF)
    path = directory / f"{binding['catalog']}.properties"
    path.write_text(path.read_text().replace(REF, "main"))
    with pytest.raises(ValueError, match="binding changed"):
        candidates.verify_catalog(
            root, ID, REF, binding["catalog"], binding["catalog_properties_digest"]
        )


def test_catalog_owner_and_preview_relation_validation_precede_sql(provider):
    root, _, calls = provider
    for ref in ("main", "sandbox-upload-other", "prod-transform-" + "b" * 32):
        with pytest.raises(ValueError):
            candidates.create_catalog(root, ID, ref)
    for relation in ("iceberg.bronze.raw", "bronze.raw;DROP", "raw"):
        with pytest.raises(ValueError):
            candidates.preview(root, "candidate", relation, 25)
    assert calls == []
