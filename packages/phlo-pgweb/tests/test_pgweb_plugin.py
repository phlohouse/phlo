"""Tests for the pgweb service plugin.

Verifies the service definition uses a pinned upstream image with no local
build or bundled files, and that plugin metadata advertises the postgres tag.
"""

from phlo_pgweb.plugin import PgwebServicePlugin


def test_pgweb_service_definition():
    """Validate pgweb service metadata in compose definition."""
    plugin = PgwebServicePlugin()
    defn = plugin.service_definition

    assert defn["name"] == "pgweb"
    assert defn["image"] == (
        "sosedoff/pgweb:0.17.0@"
        "sha256:a5256d416e2e8b92d69a4459058e3eca33a9f075d8325491644411d0bc3bd70b"
    )
    assert "build" not in defn
    assert not defn.get("files")


def test_pgweb_plugin_metadata():
    """Validate pgweb plugin metadata tags and name."""
    plugin = PgwebServicePlugin()
    meta = plugin.metadata

    assert meta.name == "pgweb"
    assert "postgres" in meta.tags
