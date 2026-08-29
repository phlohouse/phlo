"""Tests for the phlo-dlt ingestion scaffold: field spec parsing and
validation, generated workflow structure, dependency requirements."""

from __future__ import annotations

import importlib.util
import re
import tomllib
from pathlib import Path

import phlo_dlt.scaffold as scaffold_module
import pytest
from phlo_dlt.scaffold import (
    _resolve_schema_base_import,
    create_ingestion_workflow,
    parse_field_specs,
)


def _requirement_name(requirement: str) -> str:
    """Return the package name from a simple PEP 508 dependency string."""
    return re.split(r"\s*(?:[<>=!~]=?|@|\[|;)", requirement, maxsplit=1)[0].strip().lower()


def test_parse_field_specs_validates_and_dedupes() -> None:
    """Normalizes field specs."""
    specs = parse_field_specs(["User ID:str!", "created_at:datetime"])
    assert [s.name for s in specs] == ["user_id", "created_at"]
    assert specs[0].nullable is False


def test_parse_field_specs_rejects_duplicate_normalized_names() -> None:
    """Reject ambiguous duplicate field declarations."""
    with pytest.raises(ValueError, match="Duplicate field"):
        parse_field_specs(["User ID:str!", "user_id:int"])


def test_scaffold_schema_base_comes_from_quality_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keeps generated schema imports behind the quality-provider capability."""
    import phlo.plugins.discovery as discovery

    class FakeQualityProvider:
        def get_schema_base_import(self) -> tuple[str, str]:
            return ("example_quality.schemas", "ExampleSchema")

    monkeypatch.setattr(discovery, "discover_plugins", lambda: None, raising=False)

    class _Registry:
        def get(self, plugin_type: str, name: str):
            assert plugin_type == "quality_provider"
            return FakeQualityProvider() if name == "pandera" else None

    monkeypatch.setattr(
        discovery,
        "get_global_registry",
        lambda: _Registry(),
        raising=False,
    )

    assert _resolve_schema_base_import() == ("example_quality.schemas", "ExampleSchema")


def test_scaffold_rejects_nullable_unique_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rejects merge keys that cannot reliably deduplicate downstream."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "workflows" / "schemas").mkdir(parents=True)
    (tmp_path / "workflows" / "ingestion").mkdir(parents=True)

    with pytest.raises(ValueError, match="Unique key 'id' cannot be nullable"):
        create_ingestion_workflow(
            domain="commerce",
            table_name="orders",
            unique_key="id",
            fields=["id:int?"],
        )


def test_scaffold_creates_schema_directory_for_fresh_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    create_ingestion_workflow(
        domain="qa",
        table_name="samples",
        unique_key="id",
        fields=["id:int", "name:str"],
    )

    assert (tmp_path / "workflows" / "schemas" / "qa.py").exists()


def test_scaffold_schema_rendering_comes_from_quality_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Lets quality providers own generated schema source rendering."""

    class FakeQualityProvider:
        def get_schema_base_import(self) -> tuple[str, str]:
            return ("example_quality.schemas", "ExampleSchema")

        def render_schema_field(
            self,
            *,
            name: str,
            type_name: str,
            nullable: bool,
            description: str | None = None,
        ) -> str:
            suffix = f"  # {description}" if description else ""
            return f"    {name}: {type_name} = field(nullable={nullable}){suffix}"

        def render_schema_module(
            self,
            *,
            domain: str,
            schema_class: str,
            type_imports: str,
            schema_fields: str,
        ) -> str:
            return (
                f'"""Schema for {domain}."""\n\n'
                "from example_quality import field\n\n\n"
                f"class {schema_class}:\n"
                f"{schema_fields}\n"
            )

    monkeypatch.setattr(scaffold_module, "_load_quality_provider", lambda: FakeQualityProvider())
    monkeypatch.chdir(tmp_path)
    (tmp_path / "workflows" / "schemas").mkdir(parents=True)
    (tmp_path / "workflows" / "ingestion").mkdir(parents=True)

    create_ingestion_workflow(
        domain="commerce",
        table_name="products",
        unique_key="id",
        fields=["id:int", "title:str"],
    )

    schema_text = (tmp_path / "workflows" / "schemas" / "commerce.py").read_text()
    assert "from example_quality import field" in schema_text
    assert "import pandera" not in schema_text
    assert "id: int = field(nullable=False)  # Unique key" in schema_text


def test_phlo_dlt_does_not_depend_on_pandera_packages() -> None:
    """Quality providers own Pandera dependencies; phlo-dlt only consumes capability metadata."""
    pyproject = tomllib.loads(Path("packages/phlo-dlt/pyproject.toml").read_text())
    dependencies = pyproject["project"]["dependencies"]

    package_names = {_requirement_name(dependency) for dependency in dependencies}
    assert "pandera" not in package_names
    assert "phlo-pandera" not in package_names


def test_scaffold_generates_no_todos_and_is_syntax_valid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Generate scaffold files and assert they contain no placeholders and parse."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "workflows" / "schemas").mkdir(parents=True)
    (tmp_path / "workflows" / "ingestion").mkdir(parents=True)

    created = create_ingestion_workflow(
        domain="Weather",
        table_name="observations",
        unique_key="id",
        cron="0 */1 * * *",
        api_base_url="https://api.example.com",
        fields=["temperature:float", "created_at:datetime?"],
    )

    for rel_path in created:
        contents = (tmp_path / rel_path).read_text()
        assert "TODO" not in contents
        compile(contents, rel_path, "exec")

    asset_path = next(
        (tmp_path / rel_path for rel_path in created if "workflows/ingestion/" in rel_path),
        None,
    )
    assert asset_path is not None
    asset_contents = asset_path.read_text()
    assert "return rest_api(" in asset_contents
    assert "client={" in asset_contents
    assert "resources=[" in asset_contents


def test_scaffold_can_generate_partitioned_sql_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Generates the asset and editable SQL template for partitioned SQL ingestion."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "workflows" / "schemas").mkdir(parents=True)
    (tmp_path / "workflows" / "ingestion").mkdir(parents=True)

    created = create_ingestion_workflow(
        domain="warehouse",
        table_name="orders",
        unique_key="order_id",
        fields=["order_id:int", "updated_at:datetime"],
        source_kind="partitioned-sql",
    )

    assert created[2] == "tests/test_warehouse_orders.py"
    assert created[-1] == "workflows/sql/warehouse/orders.sql"
    asset_text = (tmp_path / "workflows" / "ingestion" / "warehouse" / "orders.py").read_text()
    sql_text = (tmp_path / "workflows" / "sql" / "warehouse" / "orders.sql").read_text()

    compile(asset_text, "orders.py", "exec")
    assert "partitioned_sql_resource(" in asset_text
    assert "PartitionedSqlConfig(" in asset_text
    assert 'Path(__file__).resolve().parents[2] / "sql" / "warehouse" / "orders.sql"' in asset_text
    assert "WHERE updated_at >= :partition_start" in sql_text
    assert "AND updated_at < :partition_end" in sql_text


def test_generated_partitioned_sql_callable_constructs_partition_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The generated public callable builds a window for its partition date."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.syspath_prepend(str(tmp_path))

    create_ingestion_workflow(
        domain="warehouse",
        table_name="orders",
        unique_key="order_id",
        fields=["order_id:int", "updated_at:datetime"],
        source_kind="partitioned-sql",
    )

    asset_path = tmp_path / "workflows" / "ingestion" / "warehouse" / "orders.py"
    spec = importlib.util.spec_from_file_location("scaffolded_warehouse_orders", asset_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    resource = module.orders("2026-06-04")

    assert resource is not None


def test_scaffold_uses_unique_key_field_type_when_declared(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Normalizes and types API primary keys such as Fake Store product ids."""
    monkeypatch.setattr(
        scaffold_module,
        "_resolve_schema_base_import",
        lambda: ("phlo_pandera.schemas", "PhloSchema"),
    )
    monkeypatch.chdir(tmp_path)
    (tmp_path / "workflows" / "schemas").mkdir(parents=True)
    (tmp_path / "workflows" / "ingestion").mkdir(parents=True)

    created = create_ingestion_workflow(
        domain="commerce",
        table_name="products",
        unique_key="ProductId",
        api_base_url="https://fakestoreapi.com",
        fields=["ProductId:int", "title:str", "price:float"],
    )

    schema_path = tmp_path / "workflows" / "schemas" / "commerce.py"
    asset_path = tmp_path / "workflows" / "ingestion" / "commerce" / "products.py"
    test_path_str = next((p for p in created if p.startswith("tests/")), None)
    assert test_path_str is not None, "Expected test file was not created"
    test_path = tmp_path / test_path_str

    assert "from phlo_pandera.schemas import PhloSchema" in schema_path.read_text()
    assert "class RawProducts(PhloSchema):" in schema_path.read_text()
    assert 'product_id: Series[int] = pa.Field(description="Unique key", nullable=False)' in (
        schema_path.read_text()
    )
    assert 'unique_key="product_id"' in asset_path.read_text()
    assert 'unique_key="ProductId"' not in asset_path.read_text()
    assert '"product_id": 1' in test_path.read_text()
    assert '"title": "test-001"' in test_path.read_text()
    assert '"price": 1.0' in test_path.read_text()


def test_scaffold_appends_schema_class_for_existing_domain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Allow multiple ingestion tables in one domain schema module."""
    monkeypatch.setattr(
        scaffold_module,
        "_resolve_schema_base_import",
        lambda: ("phlo_pandera.schemas", "PhloSchema"),
    )
    monkeypatch.chdir(tmp_path)
    (tmp_path / "workflows" / "schemas").mkdir(parents=True)
    (tmp_path / "workflows" / "ingestion").mkdir(parents=True)

    create_ingestion_workflow(
        domain="commerce",
        table_name="orders",
        unique_key="id",
        fields=["id:int"],
    )
    create_ingestion_workflow(
        domain="commerce",
        table_name="customers",
        unique_key="id",
        fields=["id:int"],
    )

    schema_text = (tmp_path / "workflows" / "schemas" / "commerce.py").read_text()
    assert "class RawOrders(PhloSchema):" in schema_text
    assert "class RawCustomers(PhloSchema):" in schema_text


def test_scaffold_appends_required_imports_to_existing_template_schema(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Generated workflows remain importable when init templates already created a schema."""
    monkeypatch.setattr(
        scaffold_module,
        "_resolve_schema_base_import",
        lambda: ("phlo_pandera.schemas", "PhloSchema"),
    )
    monkeypatch.chdir(tmp_path)
    schema_dir = tmp_path / "workflows" / "schemas"
    schema_dir.mkdir(parents=True)
    (tmp_path / "workflows" / "ingestion").mkdir(parents=True)
    schema_file = schema_dir / "api.py"
    schema_file.write_text(
        "from __future__ import annotations\n\n"
        "import pandera.pandas as pa\n\n\n"
        "class EventsSchema(pa.DataFrameModel):\n"
        "    id: int\n"
    )

    create_ingestion_workflow(
        domain="api",
        table_name="purchases",
        unique_key="id",
        api_base_url="https://example.test/api",
        fields=["id:int", "amount:float", "customer_id:str"],
    )

    schema_text = schema_file.read_text()
    assert "from pandera.typing import Series" in schema_text
    assert "from phlo_pandera.schemas import PhloSchema" in schema_text
    assert "class RawPurchases(PhloSchema):" in schema_text
    assert '"""Raw api RawPurchases records."""' in schema_text


def test_scaffold_uses_normalized_table_identifier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keep Phlo table identifiers valid while preserving REST endpoint path."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "workflows" / "schemas").mkdir(parents=True)
    (tmp_path / "workflows" / "ingestion").mkdir(parents=True)

    created = create_ingestion_workflow(
        domain="demo",
        table_name="bad-name",
        unique_key="id",
        api_base_url="https://example.com",
        fields=["id:int"],
    )

    asset_path = tmp_path / next(
        path for path in created if path.startswith("workflows/ingestion/")
    )
    asset_text = asset_path.read_text()
    assert 'table_name="bad_name"' in asset_text
    assert '"path": "bad-name"' in asset_text
