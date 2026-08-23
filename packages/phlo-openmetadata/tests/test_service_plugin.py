"""OpenMetadata generated service contracts.

Pins every bundled image (server, MySQL setup, Elasticsearch) to a
version plus digest and rejects build sections, so generated definitions
stay reproducible and never drift to floating tags.
"""

from importlib import resources

import yaml

from phlo_openmetadata.plugin import OpenMetadataServicePlugin


def test_openmetadata_uses_pinned_upstream_server_image() -> None:
    definition = OpenMetadataServicePlugin().service_definition

    assert definition["image"] == (
        "docker.io/openmetadata/server:1.13.1@"
        "sha256:eaa318584c52d4a492a2c56c95818b5564c6ea28b2e9695ac532c856b2c61bc9"
    )
    assert "build" not in definition
    assert "OPENMETADATA_VERSION" not in definition["env_vars"]


def test_openmetadata_mysql_uses_pinned_upstream_database_image() -> None:
    raw = resources.files("phlo_openmetadata").joinpath("openmetadata-mysql-setup.yaml").read_text()
    definition = yaml.safe_load(raw)
    assert definition["image"] == (
        "docker.io/openmetadata/db:1.13.1@"
        "sha256:6659446dba183f1e9364602839dd999c06a83f7d2e905d1c3fb22a74f3e27288"
    )
    assert "build" not in definition


def test_openmetadata_setup_uses_the_pinned_upstream_server_image() -> None:
    raw = resources.files("phlo_openmetadata").joinpath("openmetadata-setup.yaml").read_text()
    definition = yaml.safe_load(raw)

    assert definition["image"] == (
        "docker.io/openmetadata/server:1.13.1@"
        "sha256:eaa318584c52d4a492a2c56c95818b5564c6ea28b2e9695ac532c856b2c61bc9"
    )
    assert "build" not in definition


def test_openmetadata_elasticsearch_uses_pinned_upstream_image() -> None:
    raw = (
        resources.files("phlo_openmetadata")
        .joinpath("openmetadata-elasticsearch-setup.yaml")
        .read_text()
    )
    definition = yaml.safe_load(raw)

    assert definition["image"] == (
        "docker.elastic.co/elasticsearch/elasticsearch:9.3.0@"
        "sha256:4f6bdcb742e892539c6ac49b0dd3e4e182e90218546e8c6a22db378c344acb60"
    )
    assert "build" not in definition
    assert not definition.get("files")
