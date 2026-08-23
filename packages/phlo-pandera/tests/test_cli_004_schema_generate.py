"""Integration tests for `phlo schema generate`: dry-run rendering of
inferred Pandera schemas from ingestion assets and existing source tables."""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner
import pytest

from phlo.cli.main import cli

pytestmark = pytest.mark.integration


def _write(path: Path, content: str) -> None:
    """Write text to a file, creating parent directories as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def _add_cwd_to_syspath() -> None:
    """Ensure the isolated test workspace is importable from `sys.path`."""
    import sys

    cwd = str(Path.cwd())
    if cwd not in sys.path:
        sys.path.insert(0, cwd)

    for name in list(sys.modules):
        if name == "workflows" or name.startswith("workflows."):
            sys.modules.pop(name, None)


def test_schema_generate_dry_run_from_ingestion_asset() -> None:
    """Verify dry-run schema generation renders an inferred schema class."""
    runner = CliRunner()
    with runner.isolated_filesystem():
        _add_cwd_to_syspath()
        _write(Path("workflows/__init__.py"), "")
        _write(Path("workflows/ingestion/__init__.py"), "")
        _write(Path("workflows/ingestion/github/__init__.py"), "")

        _write(
            Path("workflows/ingestion/github/user_events.py"),
            """
from __future__ import annotations

from pandera.pandas import Field

from phlo_dlt import phlo_ingestion
from phlo_pandera.schemas import PhloSchema


class Minimal(PhloSchema):
    id: int = Field(unique=True)


@phlo_ingestion(table_name="user_events", unique_key="id", validation_schema=Minimal, group="github")
def user_events(partition_date: str):
    return [
        {"id": 1, "type": "PushEvent", "created_at": "2025-01-01T00:00:00Z"},
        {"id": 2, "type": None, "created_at": None},
    ]
""".lstrip(),
        )

        result = runner.invoke(
            cli,
            [
                "schema",
                "generate",
                "--from",
                "workflows.ingestion.github.user_events:user_events",
                "--domain",
                "github",
                "--dry-run",
            ],
        )

        assert result.exit_code == 0, result.output
        assert "class RawUserEvents(PhloSchema):" in result.output
        assert "id: int = Field(unique=True, nullable=False)" in result.output
        assert "created_at:" in result.output


def test_schema_generate_write_new_file_default_location() -> None:
    """Verify schema generation writes a new schema file at the default path."""
    runner = CliRunner()
    with runner.isolated_filesystem():
        _add_cwd_to_syspath()
        _write(Path("workflows/__init__.py"), "")
        _write(Path("workflows/ingestion/__init__.py"), "")
        _write(Path("workflows/ingestion/github/__init__.py"), "")

        _write(
            Path("workflows/ingestion/github/user_events.py"),
            """
from __future__ import annotations

from pandera.pandas import Field

from phlo_dlt import phlo_ingestion
from phlo_pandera.schemas import PhloSchema


class Minimal(PhloSchema):
    id: int = Field(unique=True)


@phlo_ingestion(table_name="user_events", unique_key="id", validation_schema=Minimal, group="github")
def user_events(partition_date: str):
    return [{"id": 1, "type": "PushEvent"}]
""".lstrip(),
        )

        out_file = Path("workflows/schemas/github.py")
        assert not out_file.exists()

        result = runner.invoke(
            cli,
            [
                "schema",
                "generate",
                "--from",
                "workflows.ingestion.github.user_events:user_events",
                "--domain",
                "github",
            ],
        )

        assert result.exit_code == 0, result.output
        assert out_file.exists()
        content = out_file.read_text()
        assert "class RawUserEvents(PhloSchema):" in content


def test_schema_generate_refuses_overwrite_by_default() -> None:
    """Verify generation fails when output file exists and `--update` is not set."""
    runner = CliRunner()
    with runner.isolated_filesystem():
        _add_cwd_to_syspath()
        _write(Path("workflows/__init__.py"), "")
        _write(Path("workflows/ingestion/__init__.py"), "")
        _write(Path("workflows/ingestion/github/__init__.py"), "")

        _write(
            Path("workflows/ingestion/github/user_events.py"),
            """
from __future__ import annotations

from pandera.pandas import Field

from phlo_dlt import phlo_ingestion
from phlo_pandera.schemas import PhloSchema


class Minimal(PhloSchema):
    id: int = Field(unique=True)


@phlo_ingestion(table_name="user_events", unique_key="id", validation_schema=Minimal, group="github")
def user_events(partition_date: str):
    return [{"id": 1, "type": "PushEvent"}]
""".lstrip(),
        )

        out_file = Path("workflows/schemas/github.py")
        _write(out_file, "existing = True\n")

        result = runner.invoke(
            cli,
            [
                "schema",
                "generate",
                "--from",
                "workflows.ingestion.github.user_events:user_events",
                "--domain",
                "github",
            ],
        )

        assert result.exit_code != 0
        assert "Refusing to overwrite existing file" in result.output


def test_schema_generate_update_in_place() -> None:
    """Verify generation updates an existing schema file when `--update` is used."""
    runner = CliRunner()
    with runner.isolated_filesystem():
        _add_cwd_to_syspath()
        _write(Path("workflows/__init__.py"), "")
        _write(Path("workflows/ingestion/__init__.py"), "")
        _write(Path("workflows/ingestion/github/__init__.py"), "")

        _write(
            Path("workflows/ingestion/github/user_events.py"),
            """
from __future__ import annotations

from pandera.pandas import Field

from phlo_dlt import phlo_ingestion
from phlo_pandera.schemas import PhloSchema


class Minimal(PhloSchema):
    id: int = Field(unique=True)


@phlo_ingestion(table_name="user_events", unique_key="id", validation_schema=Minimal, group="github")
def user_events(partition_date: str):
    return [{"id": 1, "type": "PushEvent"}]
""".lstrip(),
        )

        out_file = Path("workflows/schemas/github.py")
        _write(
            out_file,
            """
\"\"\"Existing github schemas.\"\"\"

from __future__ import annotations

from pandera.pandas import Field

from phlo_pandera.schemas import PhloSchema


class RawUserEvents(PhloSchema):
    id: int = Field(unique=True)
""".lstrip(),
        )

        result = runner.invoke(
            cli,
            [
                "schema",
                "generate",
                "--from",
                "workflows.ingestion.github.user_events:user_events",
                "--domain",
                "github",
                "--update",
            ],
        )

        assert result.exit_code == 0, result.output
        content = out_file.read_text()
        assert "class RawUserEvents(PhloSchema):" in content
        assert "nullable=False" in content
