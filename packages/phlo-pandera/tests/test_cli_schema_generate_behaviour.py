"""Behaviour tests for `phlo schema generate`: inference from a local DLT sample,
table selection, and the dry-run, write, overwrite and update output modes."""

from __future__ import annotations

import ast
import itertools
import sys
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import ModuleType, SimpleNamespace

import dlt
import pytest
from click.testing import CliRunner

from phlo_pandera import cli_schema_codegen

MODULE = "phlo_schema_generate_sources"


@pytest.fixture
def sources(tmp_path, monkeypatch):
    """Register an importable module of source callables and authorise writes."""
    module = ModuleType(MODULE)
    monkeypatch.setitem(sys.modules, MODULE, module)
    monkeypatch.chdir(tmp_path)
    authorizations: list[str] = []
    monkeypatch.setattr(
        cli_schema_codegen,
        "enforce_surface_mutation_authorization",
        lambda surface, adapter: authorizations.append(surface),
    )
    module.authorizations = authorizations
    return module


def _ingestion(table_name: str | None = None, unique_key: str | None = None):
    """Attach the table config @phlo_ingestion would attach to a source function."""

    def wrap(fn):
        if table_name is not None:
            fn._phlo_table_config = SimpleNamespace(
                table_name=table_name, unique_key=unique_key, group_name="tests"
            )
        return fn

    return wrap


def _generate(*args: str):
    return CliRunner().invoke(cli_schema_codegen.generate, list(args))


def _import_generated(code: str) -> ModuleType:
    """Import generated source as a module so Pandera can resolve its annotations."""
    module = ModuleType("phlo_generated_schema")
    sys.modules[module.__name__] = module
    try:
        exec(compile(code, "<generated>", "exec"), module.__dict__)
        for value in vars(module).values():
            if isinstance(value, type) and hasattr(value, "to_schema"):
                value.to_schema()
    finally:
        del sys.modules[module.__name__]
    return module


def _schema_class(code: str, class_name: str):
    return getattr(_import_generated(code), class_name)


def _fields(code: str, class_name: str) -> dict[str, tuple[str, bool]]:
    schema_class = _schema_class(code, class_name)
    columns = schema_class.to_schema().columns
    return {
        name: (schema_class.__annotations__[name], column.nullable)
        for name, column in columns.items()
    }


def test_dry_run_prints_an_importable_schema_with_inferred_types(sources) -> None:
    @_ingestion(table_name="user_events", unique_key="id")
    def user_events(partition_date: str):
        return [
            {
                "id": 1,
                "kind": "push",
                "score": 1.5,
                "active": True,
                "amount": Decimal("1.25"),
                "day": date(2026, 1, 1),
                "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
            },
            {"id": 2, "kind": None, "score": 2.0, "active": False},
        ]

    sources.user_events = user_events

    result = _generate("--from", f"{MODULE}:user_events", "--domain", "github", "--dry-run")

    assert result.exit_code == 0, result.output
    assert ast.get_docstring(ast.parse(result.output)).startswith("Pandera schemas for github")
    assert _fields(result.output, "RawUserEvents") == {
        "id": ("int", False),
        "kind": ("str | None", True),
        "score": ("float | None", True),
        "active": ("bool | None", True),
        "amount": ("Decimal | None", True),
        "day": ("date | None", True),
        "created_at": ("datetime | None", True),
    }
    assert _schema_class(result.output, "RawUserEvents").to_schema().columns["id"].unique
    assert sources.authorizations == []
    assert not Path("workflows").exists()


def test_partition_date_and_class_name_options_reach_the_source_and_output(sources) -> None:
    seen: list[str] = []

    @_ingestion(table_name="orders")
    def orders(partition_date: str):
        seen.append(partition_date)
        return [{"order_id": 1}]

    sources.orders = orders

    result = _generate(
        "--from",
        f"{MODULE}:orders",
        "--domain",
        "shop",
        "--partition-date",
        "2026-02-03",
        "--class",
        "OrdersSchema",
        "--dry-run",
    )

    assert result.exit_code == 0, result.output
    assert seen == ["2026-02-03"]
    assert _fields(result.output, "OrdersSchema") == {"order_id": ("int | None", True)}


def test_partition_date_defaults_to_today(sources) -> None:
    seen: list[str] = []

    def rows(partition_date: str):
        seen.append(partition_date)
        return [{"id": 1}]

    sources.rows = rows

    result = _generate("--from", f"{MODULE}:rows", "--domain", "shop", "--dry-run")

    assert result.exit_code == 0, result.output
    assert seen == [date.today().isoformat()]


def test_plain_iterables_use_the_table_option_or_data_as_the_table_name(sources) -> None:
    def rows(partition_date: str):
        return [{"id": 1}]

    sources.rows = rows

    default = _generate("--from", f"{MODULE}:rows", "--domain", "shop", "--dry-run")
    named = _generate(
        "--from", f"{MODULE}:rows", "--domain", "shop", "--table", "line_items", "--dry-run"
    )

    assert default.exit_code == 0, default.output
    assert _fields(default.output, "RawData") == {"id": ("int | None", True)}
    assert named.exit_code == 0, named.output
    assert _fields(named.output, "RawLineItems") == {"id": ("int | None", True)}


def test_max_records_bounds_how_much_of_a_generator_is_read(sources) -> None:
    pulled = itertools.count()

    def endless(partition_date: str):
        while True:
            yield {"id": next(pulled)}

    sources.endless = endless

    result = _generate(
        "--from", f"{MODULE}:endless", "--domain", "shop", "--max-records", "3", "--dry-run"
    )

    assert result.exit_code == 0, result.output
    assert next(pulled) == 3


def test_dlt_resources_are_limited_to_max_records(sources) -> None:
    pulled = itertools.count()

    def resource(partition_date: str):
        @dlt.resource(name="events")
        def events():
            while True:
                yield {"event_id": next(pulled)}

        return events()

    sources.resource = resource

    result = _generate(
        "--from", f"{MODULE}:resource", "--domain", "shop", "--max-records", "2", "--dry-run"
    )

    assert result.exit_code == 0, result.output
    assert _fields(result.output, "RawEvents") == {"event_id": ("int | None", True)}
    assert next(pulled) <= 3


def _two_table_source(partition_date: str):
    @dlt.source(name="shop")
    def shop():
        @dlt.resource(name="customers")
        def customers():
            yield from ({"customer_id": i} for i in range(10))

        @dlt.resource(name="invoices")
        def invoices():
            yield {"invoice_id": 1, "total": 9.5}

        return customers, invoices

    return shop()


def test_dlt_sources_with_several_tables_require_a_table_choice(sources) -> None:
    sources.shop = _two_table_source

    result = _generate("--from", f"{MODULE}:shop", "--domain", "shop", "--dry-run")

    assert result.exit_code != 0
    assert "Multiple DLT tables inferred" in result.output
    assert "Candidates: customers, invoices" in result.output


def test_table_option_selects_one_table_of_a_dlt_source(sources) -> None:
    sources.shop = _two_table_source

    result = _generate(
        "--from", f"{MODULE}:shop", "--domain", "shop", "--table", "invoices", "--dry-run"
    )

    assert result.exit_code == 0, result.output
    assert _fields(result.output, "RawInvoices") == {
        "invoice_id": ("int | None", True),
        "total": ("float | None", True),
    }


def test_unknown_table_lists_available_tables(sources) -> None:
    sources.shop = _two_table_source

    result = _generate(
        "--from", f"{MODULE}:shop", "--domain", "shop", "--table", "refunds", "--dry-run"
    )

    assert result.exit_code != 0
    assert "Table not found in inferred schema: refunds" in result.output
    assert "Available: customers, invoices" in result.output


@pytest.fixture
def orders_source(sources):
    @_ingestion(table_name="orders", unique_key="order_id")
    def orders(partition_date: str):
        return [{"order_id": 1, "status": "new"}]

    sources.orders = orders
    return sources


def test_writes_a_new_module_at_the_default_path_after_authorising(orders_source) -> None:
    result = _generate("--from", f"{MODULE}:orders", "--domain", "OnlineShop")

    out_file = Path("workflows/schemas/online_shop.py")
    assert result.exit_code == 0, result.output
    assert orders_source.authorizations == ["schema.generate"]
    assert _fields(out_file.read_text(), "RawOrders") == {
        "order_id": ("int", False),
        "status": ("str | None", True),
    }


def test_out_option_sets_the_output_path(orders_source) -> None:
    result = _generate(
        "--from", f"{MODULE}:orders", "--domain", "shop", "--out", "custom/schemas.py"
    )

    assert result.exit_code == 0, result.output
    assert "RawOrders" in _class_names(Path("custom/schemas.py"))


def _class_names(path: Path) -> list[str]:
    return [
        node.name for node in ast.parse(path.read_text()).body if isinstance(node, ast.ClassDef)
    ]


def test_refuses_to_replace_an_existing_module_without_a_mode(orders_source) -> None:
    out_file = Path("workflows/schemas/shop.py")
    out_file.parent.mkdir(parents=True)
    out_file.write_text("existing = True\n")

    result = _generate("--from", f"{MODULE}:orders", "--domain", "shop")

    assert result.exit_code != 0
    assert "Refusing to overwrite existing file" in result.output
    assert out_file.read_text() == "existing = True\n"


def test_overwrite_replaces_an_existing_module(orders_source) -> None:
    out_file = Path("workflows/schemas/shop.py")
    out_file.parent.mkdir(parents=True)
    out_file.write_text("class Legacy:\n    pass\n")

    result = _generate("--from", f"{MODULE}:orders", "--domain", "shop", "--overwrite")

    assert result.exit_code == 0, result.output
    assert _class_names(out_file) == ["RawOrders"]


def test_update_and_overwrite_are_mutually_exclusive(orders_source) -> None:
    result = _generate("--from", f"{MODULE}:orders", "--domain", "shop", "--update", "--overwrite")

    assert result.exit_code != 0
    assert "Use only one of --update or --overwrite" in result.output
    assert not Path("workflows/schemas/shop.py").exists()


def test_update_replaces_the_class_and_keeps_the_rest_of_the_module(orders_source) -> None:
    out_file = Path("workflows/schemas/shop.py")
    out_file.parent.mkdir(parents=True)
    out_file.write_text(
        '"""Shop schemas."""\n\n'
        "from phlo_pandera.schemas import PhloSchema\n\n\n"
        "class RawCustomers(PhloSchema):\n    customer_id: int\n\n\n"
        "class RawOrders(PhloSchema):\n    order_id: str\n"
    )

    result = _generate("--from", f"{MODULE}:orders", "--domain", "shop", "--update")

    assert result.exit_code == 0, result.output
    content = out_file.read_text()
    assert _class_names(out_file) == ["RawCustomers", "RawOrders"]
    assert ast.get_docstring(ast.parse(content)) == "Shop schemas."
    assert _fields(content, "RawOrders") == {
        "order_id": ("int", False),
        "status": ("str | None", True),
    }


def test_update_appends_a_missing_class(orders_source) -> None:
    out_file = Path("workflows/schemas/shop.py")
    out_file.parent.mkdir(parents=True)
    out_file.write_text("existing = True")

    result = _generate("--from", f"{MODULE}:orders", "--domain", "shop", "--update")

    assert result.exit_code == 0, result.output
    module = _import_generated(out_file.read_text())
    assert module.existing is True
    assert set(module.RawOrders.to_schema().columns) == {"order_id", "status"}


def test_rejects_sources_without_a_partition_date_parameter(sources) -> None:
    sources.bad = lambda: [{"id": 1}]

    result = _generate("--from", f"{MODULE}:bad", "--domain", "shop", "--dry-run")

    assert result.exit_code != 0
    assert "expected (partition_date: str)" in result.output
