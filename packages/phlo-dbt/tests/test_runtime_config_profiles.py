"""Tests for engine-aware dbt profile generation and preservation."""

from __future__ import annotations

from phlo_dbt.runtime_config import DbtRuntimeConfig, write_dbt_profile

# ---------------------------------------------------------------------------
# Engine-aware payload + foreign-engine preservation (#772 / #773)
# ---------------------------------------------------------------------------


def test_clickhouse_payload_omits_trino_keys() -> None:
    config = DbtRuntimeConfig(engine_type="clickhouse", password="secret")
    output = config.to_profile_payload()[config.profile_name]["outputs"][config.target_name]

    assert output["type"] == "clickhouse"
    assert output["password"] == "secret"
    for trino_key in ("method", "catalog", "http_scheme"):
        assert trino_key not in output


def test_trino_payload_shape_unchanged() -> None:
    config = DbtRuntimeConfig()
    output = config.to_profile_payload()[config.profile_name]["outputs"][config.target_name]
    assert output["type"] == "trino"
    assert {"method", "catalog", "http_scheme", "user", "host", "port"} <= set(output)


def test_write_dbt_profile_preserves_foreign_engine(tmp_path) -> None:
    profiles_dir = tmp_path / "profiles"
    existing = (
        "myproj:\n"
        "  target: prod\n"
        "  outputs:\n"
        "    prod:\n"
        "      type: clickhouse\n"
        "      user: svc\n"
        "      password: pw\n"
        "      host: ch\n"
        "      port: 8123\n"
        "      schema: marts\n"
    )
    profiles_dir.mkdir()
    (profiles_dir / "profiles.yml").write_text(existing, encoding="utf-8")

    config = DbtRuntimeConfig()  # trino defaults
    result = write_dbt_profile(config, profiles_dir)

    assert result == profiles_dir / "profiles.yml"
    assert "type: clickhouse" in result.read_text(encoding="utf-8")


def test_write_dbt_profile_overwrites_same_engine(tmp_path) -> None:
    profiles_dir = tmp_path / "profiles"
    profiles_dir.mkdir()
    (profiles_dir / "profiles.yml").write_text(
        "dev:\n  target: dev\n  outputs:\n    dev:\n      type: trino\n",
        encoding="utf-8",
    )

    write_dbt_profile(DbtRuntimeConfig(), profiles_dir)
    content = (profiles_dir / "profiles.yml").read_text(encoding="utf-8")
    assert "type: trino" in content
