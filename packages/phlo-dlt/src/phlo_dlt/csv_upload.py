"""Author and execute immutable reviewed CSV artifacts on one candidate reference."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from pathlib import Path
from typing import Any

from phlo.operations.csv_upload import csv_output_columns, csv_review_values, parse_csv


def canonical_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def author_csv_upload(project_root: Path, values: dict[str, Any]) -> dict[str, Any]:
    """Write only the immutable upload contract and its generated ingestion module."""
    manifest = values["manifest"]
    upload_id = manifest["upload_id"]
    table = manifest["table"]
    if not re.fullmatch(r"[a-f0-9]{32}", upload_id) or not re.fullmatch(
        r"[a-z][a-z0-9_]{0,62}", table
    ):
        raise ValueError("Invalid reviewed upload identity or destination.")
    encoded = canonical_bytes(manifest)
    digest = hashlib.sha256(encoded).hexdigest()
    contract = Path("contracts/uploads") / f"{upload_id}.json"
    workflow = Path("workflows/uploads") / f"{upload_id}.py"
    source = (
        '"""Reviewed CSV candidate ingestion; no schedule or automatic promotion."""\n'
        "from pathlib import Path\n"
        "from phlo_dlt.csv_upload import register_csv_upload\n\n"
        f"register_csv_upload(Path(__file__).resolve().parents[2], {str(contract)!r}, {digest!r})\n"
    ).encode()
    for relative, content in ((contract, encoded), (workflow, source)):
        destination = project_root / relative
        if not destination.resolve().is_relative_to(project_root.resolve()):
            raise ValueError("Generated upload paths must remain in the project.")
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            if destination.read_bytes() != content:
                raise FileExistsError(f"Generated upload artifact differs: {relative}")
        else:
            with destination.open("xb") as handle:
                handle.write(content)
    return {
        "workflow_type": "ingestion",
        "provider": "dlt",
        "domain": "uploads",
        "table": table,
        "files": [str(contract), str(workflow)],
        "next_steps": [],
        "metadata": {
            "contract_digest": digest,
            "source_digest": hashlib.sha256(source).hexdigest(),
        },
    }


def reviewed_rows(root: Path, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    """Verify the original bytes and apply exactly the reviewed deterministic recipe."""
    path = root / ".phlo/uploads" / manifest["upload_id"] / "source.csv"
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("Retained upload must remain inside the project.")
    data = path.read_bytes()
    if len(data) > 10 * 1024 * 1024 or hashlib.sha256(data).hexdigest() != manifest["checksum"]:
        raise ValueError("Retained upload checksum changed.")
    header, records, errors = parse_csv(data)
    if errors or len(records) != manifest["row_count"]:
        raise ValueError("Retained upload structure differs from the reviewed artifact.")
    choices = manifest["columns"]
    if {item["column"] for item in choices} != set(header):
        raise ValueError("Reviewed columns differ from the retained CSV.")
    result = []
    for index, values in enumerate(records):
        original = dict(zip(header, values, strict=True))
        row = {}
        for item in choices:
            row.update(csv_review_values(original[item["column"]], item))
        for item in choices:
            if item["type"] == "date" and row[item["name"]] is not None:
                row[item["name"]] = date.fromisoformat(row[item["name"]])
        row[manifest["record_key"]] = f"{manifest['checksum']}:{index}"
        result.append(row)
    return result


def register_csv_upload(root: Path, contract: str, expected_digest: str) -> None:
    """Register a real DLT asset whose immutable contract fixes its candidate ref."""
    import dlt
    import pandera.pandas as pa
    from phlo_dlt.decorator import phlo_ingestion

    content = (root / contract).read_bytes()
    if hashlib.sha256(content).hexdigest() != expected_digest:
        raise ValueError("Reviewed CSV contract checksum changed.")
    manifest = json.loads(content)
    types = {"string": str, "integer": int, "number": float, "boolean": bool, "date": date}
    annotations: dict[str, Any] = {manifest["record_key"]: str}
    fields: dict[str, Any] = {"__annotations__": annotations}
    output_columns = csv_output_columns(manifest["columns"])
    for choice in output_columns:
        annotations[choice["name"]] = types[choice["type"]]
        fields[choice["name"]] = pa.Field(nullable=choice["nullable"])
    schema = type("ReviewedCsvSchema", (pa.DataFrameModel,), fields)

    @phlo_ingestion(
        table_name=manifest["table"],
        namespace="bronze",
        unique_key=manifest["record_key"],
        group="uploads",
        validation_schema=schema,
        layer="bronze",
        partitioned=False,
        cron=None,
        strict_validation=True,
        merge_strategy="merge",
        add_metadata_columns=True,
        required_write_ref=manifest["candidate_ref"],
        required_actor=manifest["actor"],
        owner=manifest["actor"],
        max_retries=0,
    )
    def reviewed_csv(partition_date: str) -> Any:
        if hashlib.sha256((root / contract).read_bytes()).hexdigest() != expected_digest:
            raise ValueError("Reviewed CSV contract checksum changed.")
        dlt_types = {
            "string": "text",
            "integer": "bigint",
            "number": "double",
            "boolean": "bool",
            "date": "date",
        }
        columns: dict[str, Any] = {
            choice["name"]: {"data_type": dlt_types[choice["type"]], "nullable": choice["nullable"]}
            for choice in output_columns
        }
        columns[manifest["record_key"]] = {"data_type": "text", "nullable": False}
        return dlt.resource(reviewed_rows(root, manifest), name=manifest["table"], columns=columns)
