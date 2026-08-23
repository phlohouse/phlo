"""Tests for infrastructure configuration.

Exercises the phlo.yaml schema and its loaders: typed ServiceConfig
validation, default fallbacks for missing or empty files (invalid YAML
raises), WAP policy loading that fails closed on typos and insecure
remote endpoints, regulated-mode key precedence with deprecation
aliasing, and path-traversal rejection of PHLO_PROJECT_PATH.
"""

import tempfile
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from phlo.config_schema import InfrastructureConfig, ServiceConfig
from phlo.infrastructure import (
    clear_config_cache,
    get_authentication_config,
    get_authentication_provider_config,
    get_capability_defaults_from_config,
    get_container_name,
    get_project_name_from_config,
    get_regulated_config,
    get_service_config,
    load_infrastructure_config,
    load_project_config,
    load_wap_config,
)
from phlo.infrastructure.config import get_api_authorization_config
from phlo.security.mode import is_regulated, is_regulated_mode_enabled


@pytest.fixture(autouse=True)
def _clear_infra_config_cache():
    """Ensure load_infrastructure_config cache does not leak between tests."""
    clear_config_cache()
    yield
    clear_config_cache()


def _write_phlo_yaml(config_path: Path, config: dict) -> None:
    """Write a phlo.yaml fixture payload."""
    with config_path.open("w") as f:
        yaml.safe_dump(config, f, sort_keys=False)


def test_service_config_defaults():
    """Test ServiceConfig with defaults."""
    service = ServiceConfig(service_name="test-service")

    assert service.service_name == "test-service"
    assert service.container_name is None
    assert service.host == "localhost"
    assert service.internal_host is None
    assert service.get_internal_host() == "test-service"


def test_service_config_with_values():
    """Test ServiceConfig with explicit values."""
    service = ServiceConfig(
        service_name="dagster-webserver",
        container_name="custom-dagster",
        host="192.168.1.100",
        internal_host="dagster",
    )

    assert service.service_name == "dagster-webserver"
    assert service.container_name == "custom-dagster"
    assert service.host == "192.168.1.100"
    assert service.get_internal_host() == "dagster"


def test_service_config_container_name_validation():
    """Test container name validation."""
    with pytest.raises(ValueError, match="container_name cannot be empty"):
        ServiceConfig(service_name="test", container_name="")

    with pytest.raises(ValueError, match="must contain only alphanumeric"):
        ServiceConfig(service_name="test", container_name="invalid@name")

    with pytest.raises(ValueError, match="cannot start with hyphen"):
        ServiceConfig(service_name="test", container_name="-invalid")


def test_service_config_get_container_name():
    """Test get_container_name with pattern."""
    service = ServiceConfig(service_name="dagster-webserver")
    pattern = "{project}-{service}-1"

    assert service.get_container_name("myproject", pattern) == "myproject-dagster-webserver-1"


def test_service_config_get_container_name_with_override():
    """Test get_container_name with explicit override."""
    service = ServiceConfig(service_name="dagster-webserver", container_name="my-custom-container")
    pattern = "{project}-{service}-1"

    assert service.get_container_name("myproject", pattern) == "my-custom-container"


def test_infrastructure_config_defaults():
    """Test InfrastructureConfig with defaults."""
    config = InfrastructureConfig()

    assert config.container_naming_pattern == "{project}-{service}-1"
    assert len(config.services) == 0
    assert config.network.driver == "bridge"


def test_load_project_config_reads_phlo_yaml_root_keys(tmp_path: Path) -> None:
    config_path = tmp_path / "phlo.yaml"
    _write_phlo_yaml(
        config_path,
        {
            "name": "demo",
            "capabilities": {"defaults": {"table_store": "iceberg"}},
        },
    )

    loaded = load_project_config(tmp_path)

    assert loaded["name"] == "demo"
    assert loaded["capabilities"]["defaults"]["table_store"] == "iceberg"


def test_get_capability_defaults_from_config_reads_defaults_block(tmp_path: Path) -> None:
    config_path = tmp_path / "phlo.yaml"
    _write_phlo_yaml(
        config_path,
        {
            "capabilities": {
                "defaults": {
                    "table_store": "iceberg",
                    "query_engine": "trino",
                }
            }
        },
    )

    defaults = get_capability_defaults_from_config(tmp_path)

    assert defaults == {"table_store": "iceberg", "query_engine": "trino"}


def test_get_api_authorization_config_ignores_non_mapping_service_auth(tmp_path: Path) -> None:
    config_path = tmp_path / "phlo.yaml"
    _write_phlo_yaml(
        config_path,
        {
            "services": {"phlo-api": {"authorization": True}},
            "api": {"authorization": {"mode": "required"}},
        },
    )

    auth_config = get_api_authorization_config(tmp_path)

    assert auth_config is not None
    assert auth_config.mode == "required"
    assert auth_config.backend is None


def test_get_regulated_config_reads_root_boolean(tmp_path: Path) -> None:
    config_path = tmp_path / "phlo.yaml"
    _write_phlo_yaml(config_path, {"regulated": True})

    assert get_regulated_config(tmp_path) is True


def test_get_regulated_config_rejects_non_boolean(tmp_path: Path) -> None:
    config_path = tmp_path / "phlo.yaml"
    _write_phlo_yaml(config_path, {"regulated": "true"})

    with pytest.raises(ValueError, match="regulated.*must be a boolean"):
        get_regulated_config(tmp_path)


def test_get_authentication_config_reads_root_mapping(tmp_path: Path) -> None:
    config_path = tmp_path / "phlo.yaml"
    _write_phlo_yaml(
        config_path,
        {
            "authentication": {
                "provider": "proxy",
                "proxy": {"trusted_proxies": ["127.0.0.1/32"]},
            }
        },
    )

    assert get_authentication_config(tmp_path) == {
        "provider": "proxy",
        "proxy": {"trusted_proxies": ["127.0.0.1/32"]},
    }


def test_get_authentication_config_rejects_non_mapping(tmp_path: Path) -> None:
    config_path = tmp_path / "phlo.yaml"
    _write_phlo_yaml(config_path, {"authentication": True})

    with pytest.raises(ValueError, match="authentication must be a mapping"):
        get_authentication_config(tmp_path)


def test_get_authentication_provider_config_reads_provider(tmp_path: Path) -> None:
    config_path = tmp_path / "phlo.yaml"
    _write_phlo_yaml(config_path, {"authentication": {"provider": "proxy"}})

    assert get_authentication_provider_config(tmp_path) == "proxy"


def test_get_authentication_provider_config_rejects_non_string(tmp_path: Path) -> None:
    config_path = tmp_path / "phlo.yaml"
    _write_phlo_yaml(config_path, {"authentication": {"provider": True}})

    with pytest.raises(ValueError, match="authentication.provider must be a string"):
        get_authentication_provider_config(tmp_path)


def test_is_regulated_reads_phlo_yaml_when_env_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = tmp_path / "phlo.yaml"
    _write_phlo_yaml(config_path, {"regulated": True})

    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    monkeypatch.delenv("PHLO_REGULATED", raising=False)
    clear_config_cache()

    assert is_regulated() is True


def test_infrastructure_config_pattern_validation():
    """Test pattern validation."""
    with pytest.raises(ValueError, match="must contain at least"):
        InfrastructureConfig(container_naming_pattern="invalid-pattern")


def test_infrastructure_config_get_service():
    """Test get_service method."""
    config = InfrastructureConfig(
        services={"dagster": ServiceConfig(service_name="dagster-webserver")}
    )

    service = config.get_service("dagster")
    assert service is not None
    assert service.service_name == "dagster-webserver"

    assert config.get_service("nonexistent") is None


def test_infrastructure_config_get_container_name():
    """Test get_container_name method."""
    config = InfrastructureConfig(
        container_naming_pattern="{project}-{service}-1",
        services={"dagster": ServiceConfig(service_name="dagster-webserver")},
    )

    name = config.get_container_name("dagster", "myproject")
    assert name == "myproject-dagster-webserver-1"

    assert config.get_container_name("nonexistent", "myproject") is None


def test_load_infrastructure_config_with_file():
    """Test loading config from phlo.yaml."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = Path(tmpdir) / "phlo.yaml"

        phlo_config = {
            "name": "test-project",
            "infrastructure": {
                "container_naming_pattern": "{project}_{service}",
                "services": {
                    "dagster_webserver": {
                        "service_name": "dagster-webserver",
                        "host": "localhost",
                        "internal_host": "dagster",
                    }
                },
            },
        }

        with config_path.open("w") as f:
            yaml.dump(phlo_config, f)

        clear_config_cache()
        config = load_infrastructure_config(Path(tmpdir))

        assert config.container_naming_pattern == "{project}_{service}"
        assert len(config.services) == 1
        assert "dagster_webserver" in config.services


def test_get_container_name_helper():
    """Test get_container_name helper function."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = Path(tmpdir) / "phlo.yaml"

        phlo_config = {
            "name": "test-project",
            "infrastructure": {
                "services": {
                    "dagster_webserver": {
                        "service_name": "dagster-webserver",
                        "internal_host": "dagster",
                    }
                }
            },
        }

        with config_path.open("w") as f:
            yaml.dump(phlo_config, f)

        clear_config_cache()
        name = get_container_name("dagster_webserver", "myproject", Path(tmpdir))

        assert name == "myproject-dagster-webserver-1"


def test_load_infrastructure_config_missing_file_returns_defaults(tmp_path: Path):
    """Missing phlo.yaml should return default infrastructure config."""
    config = load_infrastructure_config(tmp_path)

    assert config.container_naming_pattern == "{project}-{service}-1"
    assert config.services == {}
    assert config.network.driver == "bridge"


def test_load_wap_config_reads_the_project_policy(tmp_path: Path) -> None:
    """WAP launch selection is an explicit project-level phlo.yaml setting."""
    (tmp_path / "phlo.yaml").write_text(
        """\
wap:
  enabled: true
  job_name: project_asset_job
  repository_location_name: user_code
  repository_name: user_repository
  dagster_url: https://dagster.internal/graphql
""",
        encoding="utf-8",
    )

    config = load_wap_config(tmp_path)

    assert config.enabled is True
    assert config.job_name == "project_asset_job"
    assert config.repository_location_name == "user_code"
    assert config.repository_name == "user_repository"
    assert config.dagster_url == "https://dagster.internal/graphql"
    assert config.requires_access_token is True


def test_load_wap_config_uses_generated_stack_selectors_by_default(tmp_path: Path) -> None:
    """Generated WAP stacks need selectors even when phlo.yaml only enables WAP."""
    (tmp_path / "phlo.yaml").write_text("wap:\n  enabled: true\n", encoding="utf-8")

    config = load_wap_config(tmp_path)

    assert config.repository_location_name == "phlo_dagster.framework.definitions"
    assert config.repository_name == "__repository__"


def test_load_wap_config_fails_closed_for_typos_and_insecure_remote_endpoints(
    tmp_path: Path,
) -> None:
    """A typo must not silently disable the WAP policy or downgrade transport security."""
    (tmp_path / "phlo.yaml").write_text(
        """\
wap:
  enabledd: true
  dagster_url: http://dagster.internal/graphql
""",
        encoding="utf-8",
    )

    with pytest.raises(ValidationError):
        load_wap_config(tmp_path)


def test_load_wap_config_uses_service_resolution_when_no_remote_url_is_set(tmp_path: Path) -> None:
    (tmp_path / "phlo.yaml").write_text("wap:\n  enabled: true\n", encoding="utf-8")

    config = load_wap_config(tmp_path)

    assert config.enabled is True
    assert config.dagster_url is None
    assert config.requires_access_token is False


def test_load_infrastructure_config_empty_file_returns_defaults(tmp_path: Path):
    """Empty phlo.yaml should return default infrastructure config."""
    config_path = tmp_path / "phlo.yaml"
    config_path.write_text("")

    config = load_infrastructure_config(tmp_path)

    assert config.container_naming_pattern == "{project}-{service}-1"
    assert config.services == {}
    assert config.network.driver == "bridge"


def test_load_infrastructure_config_missing_infrastructure_section_returns_defaults(tmp_path: Path):
    """Config without infrastructure section should return defaults."""
    config_path = tmp_path / "phlo.yaml"
    _write_phlo_yaml(config_path, {"name": "test-project"})

    config = load_infrastructure_config(tmp_path)

    assert config.container_naming_pattern == "{project}-{service}-1"
    assert config.services == {}
    assert config.network.driver == "bridge"


def test_load_infrastructure_config_invalid_yaml_raises(tmp_path: Path):
    """Invalid YAML should be surfaced to the caller."""
    config_path = tmp_path / "phlo.yaml"
    config_path.write_text("infrastructure:\n  services:\n    dagster: [")

    with pytest.raises(yaml.YAMLError):
        load_infrastructure_config(tmp_path)


def test_load_infrastructure_config_invalid_schema_raises(tmp_path: Path):
    """Invalid infrastructure schema should raise ValidationError."""
    config_path = tmp_path / "phlo.yaml"
    _write_phlo_yaml(
        config_path,
        {
            "infrastructure": {
                "container_naming_pattern": "invalid-pattern",
            }
        },
    )

    with pytest.raises(ValidationError):
        load_infrastructure_config(tmp_path)


def test_get_project_name_from_config_handles_missing_and_invalid_config(tmp_path: Path):
    """Project name helper should gracefully handle missing/invalid config."""
    assert get_project_name_from_config(tmp_path) is None

    config_path = tmp_path / "phlo.yaml"
    _write_phlo_yaml(config_path, {"name": "test-project"})
    clear_config_cache()
    assert get_project_name_from_config(tmp_path) == "test-project"

    config_path.write_text("name: [")
    clear_config_cache()
    assert get_project_name_from_config(tmp_path) is None


def test_service_config_and_container_name_helpers_use_service_override(tmp_path: Path):
    """Helper functions should honor configured service overrides."""
    config_path = tmp_path / "phlo.yaml"
    _write_phlo_yaml(
        config_path,
        {
            "infrastructure": {
                "services": {
                    "dagster": {
                        "service_name": "dagster-webserver",
                        "container_name": "custom-dagster-web",
                    }
                }
            }
        },
    )

    service = get_service_config("dagster", tmp_path)

    assert service is not None
    assert service.container_name == "custom-dagster-web"
    assert get_container_name("dagster", "test-project", tmp_path) == "custom-dagster-web"
    assert get_service_config("missing", tmp_path) is None
    assert get_container_name("missing", "test-project", tmp_path) is None


def test_clear_config_cache_refreshes_updated_file_contents(tmp_path: Path):
    """clear_config_cache should force a reload after file changes."""
    config_path = tmp_path / "phlo.yaml"

    _write_phlo_yaml(
        config_path,
        {
            "infrastructure": {
                "container_naming_pattern": "{project}-{service}-1",
                "services": {"dagster": {"service_name": "dagster-webserver"}},
            }
        },
    )
    first_config = load_infrastructure_config(tmp_path)
    assert first_config.container_naming_pattern == "{project}-{service}-1"

    _write_phlo_yaml(
        config_path,
        {
            "infrastructure": {
                "container_naming_pattern": "{project}_{service}",
                "services": {
                    "dagster": {
                        "service_name": "dagster-webserver",
                        "container_name": "updated-container",
                    }
                },
            }
        },
    )

    stale_config = load_infrastructure_config(tmp_path)
    assert stale_config is first_config
    assert stale_config.container_naming_pattern == "{project}-{service}-1"

    clear_config_cache()
    refreshed_config = load_infrastructure_config(tmp_path)

    assert refreshed_config is not first_config
    assert refreshed_config.container_naming_pattern == "{project}_{service}"
    assert get_container_name("dagster", "test-project", tmp_path) == "updated-container"


def test_phlo_project_path_rejects_path_traversal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PHLO_PROJECT_PATH with '..' sequences should be rejected (issue #339)."""
    bad_path = tmp_path / ".." / "etc"
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(bad_path))

    with pytest.raises(ValueError, match="path traversal"):
        from phlo.infrastructure.config import _default_project_root

        _default_project_root()


def test_phlo_project_path_allows_explicit_absolute_path_outside_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PHLO_PROJECT_PATH should honor explicit absolute paths for child processes and CI."""
    project_root = tmp_path / "external-project"
    project_root.mkdir()
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(project_root))

    from phlo.infrastructure.config import _default_project_root

    assert _default_project_root() == project_root.resolve()


def test_get_regulated_config_falls_back_to_regulated_mode_key(tmp_path: Path) -> None:
    """get_regulated_config should fallback to regulated_mode key with deprecation warning."""
    config_path = tmp_path / "phlo.yaml"
    _write_phlo_yaml(config_path, {"regulated_mode": True})

    result = get_regulated_config(tmp_path)

    assert result is True


def test_get_regulated_config_prefers_regulated_over_regulated_mode(tmp_path: Path) -> None:
    """get_regulated_config should prefer regulated key over regulated_mode."""
    config_path = tmp_path / "phlo.yaml"
    _write_phlo_yaml(config_path, {"regulated": False, "regulated_mode": True})

    result = get_regulated_config(tmp_path)

    assert result is False


def test_is_regulated_mode_enabled_alias_emits_deprecation_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Calling is_regulated_mode_enabled should emit DeprecationWarning."""
    config_path = tmp_path / "phlo.yaml"
    _write_phlo_yaml(config_path, {"regulated": True})

    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    monkeypatch.delenv("PHLO_REGULATED", raising=False)
    clear_config_cache()

    with pytest.warns(DeprecationWarning, match="is_regulated_mode_enabled.*deprecated"):
        result = is_regulated_mode_enabled()

    assert result is True
