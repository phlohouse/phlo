"""
Schema code generation helpers and the ``generate`` CLI command.

Extracted from ``cli_schema`` to keep module size manageable.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any, Callable

import click

from phlo.cli.authorization_wrappers import enforce_surface_mutation_authorization
from phlo.logging import get_logger
from phlo_pandera.authorization import get_pandera_adapter

_DEFAULT_SCHEMA_OUT_DIR = Path("workflows/schemas")
logger = get_logger(__name__)


def _import_object(ref: str) -> Any:
    """Import the object at a "module:attr" reference; ClickException on bad ref or missing attr."""
    import importlib

    if ":" not in ref:
        raise click.ClickException(
            "Invalid reference. Expected 'module:attr' "
            "(e.g. workflows.ingestion.github.user_events:user_events)."
        )

    module_name, attr = ref.split(":", 1)
    module = importlib.import_module(module_name)
    try:
        return getattr(module, attr)
    except AttributeError as exc:
        logger.warning(
            "schema_codegen_import_attr_missing",
            module_name=module_name,
            attribute=attr,
        )
        raise click.ClickException(f"Object not found: {ref}") from exc


def _extract_source_callable(obj: Any) -> tuple[Callable[..., Any], dict[str, Any]]:
    """Split a @phlo_ingestion object into its source builder callable and metadata.

    Raises click.ClickException when the object is not a supported callable.

    """
    import inspect

    if callable(obj):
        meta: dict[str, Any] = {}
        table_config = getattr(obj, "_phlo_table_config", None)
        if table_config is not None:
            meta["table_name"] = getattr(table_config, "table_name", None)
            meta["unique_key"] = getattr(table_config, "unique_key", None)
            meta["group"] = getattr(table_config, "group_name", None)

        sig = inspect.signature(obj)
        if "partition_date" not in sig.parameters:
            raise click.ClickException(
                "Unsupported asset source function signature: expected (partition_date: str)."
            )

        return obj, meta

    raise click.ClickException("Unsupported input. Provide a callable created via @phlo_ingestion.")


def _to_pascal_case(name: str) -> str:
    """Convert an underscore- or hyphen-delimited string to PascalCase."""
    parts = [p for p in name.replace("-", "_").split("_") if p]
    return "".join(p[:1].upper() + p[1:] for p in parts)


def _snake_case(name: str) -> str:
    """Convert a PascalCase or camelCase string to snake_case."""
    s1 = re.sub("(.)([A-Z][a-z]+)", r"\1_\2", name)
    return re.sub("([a-z0-9])([A-Z])", r"\1_\2", s1).lower()


def _map_dlt_type(dlt_type: str) -> tuple[str, str | None]:
    """Map a DLT data type to (annotation, import statement); the import is None when unneeded."""
    normalized = dlt_type.lower()
    if normalized in {"text", "varchar", "char", "uuid"}:
        return "str", None
    if normalized in {"bigint", "int", "integer", "smallint", "tinyint"}:
        return "int", None
    if normalized in {"double", "float", "real"}:
        return "float", None
    if normalized in {"decimal"}:
        return "Decimal", "from decimal import Decimal"
    if normalized in {"bool", "boolean"}:
        return "bool", None
    if normalized in {"date"}:
        return "date", "from datetime import date"
    if "timestamp" in normalized or normalized in {"datetime"}:
        return "datetime", "from datetime import datetime"
    if normalized in {"json"}:
        return "dict[str, Any]", "from typing import Any"
    if normalized in {"binary", "bytes"}:
        return "bytes", None
    return "str", None


def _render_schema_module(
    *,
    domain: str,
    class_name: str,
    columns: list[dict[str, Any]],
    unique_key: str | None,
) -> str:
    """Render a complete Pandera schema module (docstring, imports, class body) as source."""
    imports: set[str] = {"from __future__ import annotations", "from pandera.pandas import Field"}
    imports.add("from phlo_pandera.schemas import PhloSchema")

    fields_lines: list[str] = []
    for col in columns:
        col_name = col["name"]
        annotation = col["annotation"]
        nullable = col["nullable"]

        field_args: list[str] = []
        if unique_key and col_name == unique_key:
            field_args.append("unique=True")
            field_args.append("nullable=False")
        elif nullable:
            field_args.append("nullable=True")

        field = ""
        if field_args:
            field = f" = Field({', '.join(field_args)})"

        fields_lines.append(f"    {col_name}: {annotation}{field}")

    module_doc = (
        f'"""Pandera schemas for {domain} domain.\n\nGenerated via `phlo schema generate`.\n"""'
    )
    body = "\n".join(fields_lines) if fields_lines else "    pass"

    import_lines = "\n".join(sorted(imports))
    return f"{module_doc}\n\n{import_lines}\n\n\nclass {class_name}(PhloSchema):\n{body}\n"


def _ensure_parent_dir(path: Path) -> None:
    """Create the parent directory of ``path`` when it does not exist."""
    path.parent.mkdir(parents=True, exist_ok=True)


def _update_or_insert_class(content: str, class_name: str, class_block: str) -> str:
    """Replace the named class in module content, or append it when absent."""
    tree = ast.parse(content)
    for node in tree.body:
        if (
            isinstance(node, ast.ClassDef)
            and node.name == class_name
            and node.lineno
            and node.end_lineno
        ):
            lines = content.splitlines(keepends=True)
            start = node.lineno - 1
            end = node.end_lineno
            return "".join(lines[:start]) + class_block + "".join(lines[end:])

    if not content.endswith("\n"):
        content += "\n"
    if not content.endswith("\n\n"):
        content += "\n"
    return content + class_block


def _class_block_only(module_code: str, class_name: str) -> str:
    """Extract one class definition as source; ValueError when it is absent."""
    tree = ast.parse(module_code)
    lines = module_code.splitlines(keepends=True)
    for node in tree.body:
        if (
            isinstance(node, ast.ClassDef)
            and node.name == class_name
            and node.lineno
            and node.end_lineno
        ):
            return "".join(lines[node.lineno - 1 : node.end_lineno])
    raise ValueError(f"class not found in generated module: {class_name}")


def _ensure_imports_in_module(content: str, import_lines: list[str]) -> str:
    """Insert missing import lines after any module docstring, keeping __future__ first."""
    if not import_lines:
        return content

    try:
        tree = ast.parse(content)
    except SyntaxError:
        logger.warning("schema_codegen_existing_module_invalid_syntax")
        # Don't try to be clever if the file is already invalid.
        return "\n".join(import_lines) + "\n" + content

    insert_after = 0
    first_node = tree.body[0] if tree.body else None
    if isinstance(first_node, ast.Expr) and isinstance(first_node.value, ast.Constant):
        if isinstance(first_node.value.value, str) and first_node.end_lineno is not None:
            insert_after = first_node.end_lineno

    lines = content.splitlines()
    existing = set(lines)
    to_add = [line for line in import_lines if line not in existing]
    if not to_add:
        return content

    lines[insert_after:insert_after] = to_add + [""]
    return "\n".join(lines) + ("\n" if not content.endswith("\n") else "")


@click.command("generate")
@click.option(
    "--from",
    "from_ref",
    required=True,
    help="Python reference to a callable or @phlo_ingestion asset (module:attr).",
)
@click.option("--dry-run", is_flag=True, help="Print generated schema without writing files.")
@click.option(
    "--domain",
    required=True,
    help="Domain name (used for default output path and module docstring).",
)
@click.option("--table", "table_name", default=None, help="DLT table/resource name to generate.")
@click.option(
    "--class",
    "class_name",
    default=None,
    help="Schema class name (default: Raw<TableName>).",
)
@click.option(
    "--partition-date",
    default=None,
    help="Partition date passed to ingestion source builder (default: today).",
)
@click.option(
    "--max-records",
    type=int,
    default=200,
    show_default=True,
    help="Limit records extracted per DLT resource during schema inference.",
)
@click.option(
    "--out",
    "out_path",
    default=None,
    help="Output schema file path (default: workflows/schemas/<domain>.py).",
)
@click.option(
    "--update",
    is_flag=True,
    help="Update/insert the class into an existing schema module (non-destructive).",
)
@click.option(
    "--overwrite",
    is_flag=True,
    help="Overwrite the entire output module if it exists (destructive).",
)
def generate(
    from_ref: str,
    dry_run: bool,
    domain: str,
    table_name: str | None,
    class_name: str | None,
    partition_date: str | None,
    max_records: int,
    out_path: str | None,
    update: bool,
    overwrite: bool,
):
    """Generate Pandera schemas from a bounded DLT inference sample.

    Runs a small DLT sample locally (filesystem destination) to infer the
    schema, then emits a PhloSchema-based DataFrameModel into your lakehouse
    code (default: workflows/schemas/). With --dry-run the schema is printed
    instead of written.

    """
    import datetime as _dt
    import itertools
    import tempfile

    import dlt
    from rich.console import Console

    console = Console()

    # ``generate`` may execute user-provided source and create temporary DLT
    # state before writing the requested schema module. Check durable writes
    # first so a regulated denial leaves both source and output untouched.
    if not dry_run:
        enforce_surface_mutation_authorization("schema.generate", get_pandera_adapter)

    obj = _import_object(from_ref)
    source_fn, meta = _extract_source_callable(obj)

    partition_date_value = partition_date or _dt.date.today().isoformat()
    dlt_obj = source_fn(partition_date_value)

    try:
        from dlt.extract.resource import DltResource
        from dlt.extract.source import DltSource
    except Exception as exc:  # pragma: no cover
        logger.exception(
            "schema_codegen_dlt_import_failed",
            from_ref=from_ref,
            error=str(exc),
        )
        raise click.ClickException("Failed to import DLT types.") from exc

    if isinstance(dlt_obj, DltSource):
        for r in dlt_obj.resources.values():
            try:
                r.add_limit(max_records)
            except Exception:
                continue
    elif isinstance(dlt_obj, DltResource):
        dlt_obj.add_limit(max_records)
    elif hasattr(dlt_obj, "__iter__"):
        dlt_obj = itertools.islice(dlt_obj, max_records)

    with tempfile.TemporaryDirectory(prefix="phlo-schema-generate-") as tmpdir:
        pipeline = dlt.pipeline(
            pipeline_name=f"phlo_schema_generate_{_dt.datetime.now().strftime('%Y%m%d_%H%M%S')}",
            destination=dlt.destinations.filesystem(bucket_url=Path(tmpdir).as_uri()),
            dataset_name=_snake_case(domain),
            pipelines_dir=tmpdir,
        )

        run_kwargs: dict[str, Any] = {"loader_file_format": "parquet"}
        if not isinstance(dlt_obj, (DltSource, DltResource)):
            run_kwargs["table_name"] = meta.get("table_name") or table_name or "data"

        pipeline.run(dlt_obj, **run_kwargs)

        schema = pipeline.default_schema
        candidate_tables = [
            t
            for t in schema.tables.keys()
            if not t.startswith("_dlt_") and t not in {"_dlt_pipeline_state"}
        ]

        selected_table = table_name
        if selected_table is None:
            if len(candidate_tables) == 1:
                selected_table = candidate_tables[0]
            else:
                raise click.ClickException(
                    "Multiple DLT tables inferred. Re-run with --table <name>. "
                    f"Candidates: {', '.join(sorted(candidate_tables))}"
                )

        if selected_table not in schema.tables:
            raise click.ClickException(
                f"Table not found in inferred schema: {selected_table}. "
                f"Available: {', '.join(sorted(candidate_tables))}"
            )

        table = schema.tables[selected_table]
        dlt_columns: dict[str, Any] = table.get("columns") or {}

    unique_key = meta.get("unique_key")
    inferred_columns: list[dict[str, Any]] = []
    imports: set[str] = set()
    for col_name, col in sorted(dlt_columns.items()):
        if col_name.startswith("_dlt_") or col_name.startswith("_phlo_"):
            continue

        ann, imp = _map_dlt_type(str(col.get("data_type", "text")))
        if imp:
            imports.add(imp)

        nullable = bool(col.get("nullable", True))
        if unique_key and col_name == unique_key:
            nullable = False
        annotation = ann if not nullable else f"{ann} | None"

        inferred_columns.append(
            {
                "name": col_name,
                "annotation": annotation,
                "nullable": nullable,
                "dlt_type": col.get("data_type"),
            }
        )

    base_name = meta.get("table_name") or selected_table
    schema_class = class_name or f"Raw{_to_pascal_case(base_name)}"

    module_code = _render_schema_module(
        domain=domain,
        class_name=schema_class,
        columns=inferred_columns,
        unique_key=unique_key,
    )

    # Add extra imports if needed by annotations.
    if imports:
        lines = module_code.splitlines()
        insert_at = 0
        for idx, line in enumerate(lines):
            if line.startswith("from __future__ import annotations"):
                insert_at = idx + 1
                break
        extra = sorted(imports)
        lines[insert_at:insert_at] = extra
        module_code = "\n".join(lines) + "\n"

    output_path = (
        Path(out_path) if out_path else (_DEFAULT_SCHEMA_OUT_DIR / f"{_snake_case(domain)}.py")
    )

    if dry_run:
        click.echo(module_code)
        return

    if overwrite and update:
        raise click.ClickException("Use only one of --update or --overwrite.")

    _ensure_parent_dir(output_path)
    if not output_path.exists():
        output_path.write_text(module_code)
        console.print(f"[green]Wrote[/green] {output_path}")
        return

    if overwrite:
        output_path.write_text(module_code)
        console.print(f"[green]Overwrote[/green] {output_path}")
        return

    if not update:
        raise click.ClickException(
            f"Refusing to overwrite existing file: {output_path}. Use --update or --overwrite."
        )

    existing = output_path.read_text()
    class_block = _class_block_only(module_code, schema_class)

    required_imports = [
        "from __future__ import annotations",
        "from pandera.pandas import Field",
        "from phlo_pandera.schemas import PhloSchema",
    ]
    existing = _ensure_imports_in_module(existing, required_imports)

    updated = _update_or_insert_class(existing, schema_class, class_block)
    output_path.write_text(updated)
    console.print(f"[green]Updated[/green] {output_path}")
