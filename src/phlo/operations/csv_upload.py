"""Deterministic bounded CSV parsing shared by review and ingestion providers."""

from __future__ import annotations

import csv
import io
import math
import re
from collections.abc import Mapping, Sequence
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any

_NUMBER = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$")


def parse_csv(
    data: bytes, *, max_rows: int = 50_000, max_columns: int = 100, max_cells: int = 500_000
) -> tuple[list[str], list[list[str]], list[str]]:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return [], [], ["Use a UTF-8 CSV file. The original bytes have been retained."]
    if "\x00" in text:
        return [], [], ["CSV files cannot contain NUL characters."]
    try:
        reader = csv.reader(io.StringIO(text, newline=""), strict=True)
        header = next(reader, [])
        if not header or len(header) > max_columns:
            return [], [], [f"CSV requires between 1 and {max_columns} columns."]
        if any(not name.strip() or len(name) > 256 for name in header) or len(set(header)) != len(
            header
        ):
            return (
                [],
                [],
                ["CSV column names must be non-empty, unique and at most 256 characters."],
            )
        rows = []
        for number, row in enumerate(reader, start=2):
            if len(rows) >= max_rows or (len(rows) + 1) * len(header) > max_cells:
                return (
                    header,
                    [],
                    [f"CSV exceeds the {max_rows:,}-row or {max_cells:,}-cell limit."],
                )
            if len(row) != len(header):
                return (
                    header,
                    [],
                    [f"CSV record {number} has {len(row)} values; expected {len(header)}."],
                )
            if any(len(value) > 65_536 for value in row):
                return (
                    header,
                    [],
                    [f"CSV record {number} contains a value longer than 65,536 characters."],
                )
            rows.append(row)
        return header, rows, []
    except csv.Error:
        return [], [], ["CSV structure is invalid. Check quotes and field lengths."]


def csv_value(value: str, choice: Mapping[str, Any]) -> Any:
    if not value:
        if choice["nullable"]:
            return None
        raise ValueError("A value is required.")
    if choice["type"] == "string":
        return value
    if choice["type"] == "integer":
        if not re.fullmatch(r"[+-]?\d+", value):
            raise ValueError("Expected an integer.")
        number = int(value)
        if abs(number) > 9_007_199_254_740_991:
            raise ValueError("Integer exceeds the exact preview range; keep it as text.")
        return number
    if choice["type"] == "number":
        if not _NUMBER.fullmatch(value) or not math.isfinite(float(value)):
            raise ValueError("Expected a finite number; keep flags such as ND or <LOQ as text.")
        return float(value)
    if choice["type"] == "boolean":
        if value.casefold() not in {"true", "false"}:
            raise ValueError("Expected true or false.")
        return value.casefold() == "true"
    formats = {
        "iso": ["%Y-%m-%d"],
        "day_first": ["%Y-%m-%d", "%d/%m/%Y"],
        "month_first": ["%Y-%m-%d", "%m/%d/%Y"],
    }
    for pattern in formats[choice["date_format"]]:
        try:
            return datetime.strptime(value, pattern).replace(tzinfo=UTC).date().isoformat()
        except ValueError:
            pass
    raise ValueError(f"Date does not match {choice['date_format'].replace('_', ' ')} format.")


def csv_output_columns(choices: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Describe original reviewed fields and explicitly requested derived qualifiers."""
    output = []
    for choice in choices:
        output.append(
            {
                "name": choice["name"],
                "type": choice["type"],
                "nullable": choice["nullable"],
                "source_column": choice["column"],
                "derived": False,
            }
        )
        qualifiers = choice.get("qualifiers")
        if qualifiers:
            output.extend(
                [
                    {
                        "name": qualifiers["value_name"],
                        "type": "number",
                        "nullable": True,
                        "source_column": choice["column"],
                        "derived": True,
                    },
                    {
                        "name": qualifiers["flag_name"],
                        "type": "string",
                        "nullable": True,
                        "source_column": choice["column"],
                        "derived": True,
                    },
                ]
            )
    return output


def csv_review_values(value: str, choice: Mapping[str, Any]) -> dict[str, Any]:
    """Keep original text while deriving only recognized finite numeric values/flags."""
    result = {choice["name"]: csv_value(value, choice)}
    qualifiers = choice.get("qualifiers")
    if not qualifiers:
        return result
    if choice["type"] != "string":
        raise ValueError("Qualifier splitting must preserve the original column as text.")
    number = None
    flag = None
    if value:
        if _NUMBER.fullmatch(value) and math.isfinite(float(value)):
            number = float(value)
        elif value.strip().upper() in {"ND", "<LOQ"}:
            flag = value.strip().upper()
        else:
            raise ValueError(
                "Expected a finite number, ND or <LOQ; keep as text only for other values."
            )
    result.update({qualifiers["value_name"]: number, qualifiers["flag_name"]: flag})
    return result


def _date_options(value: str) -> dict[str, str]:
    if not re.fullmatch(r"(?:\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4})", value):
        return {}
    result = {}
    for name in ("iso", "day_first", "month_first"):
        with suppress(ValueError):
            result[name] = csv_value(
                value, {"type": "date", "nullable": False, "date_format": name}
            )
    return result


def inspect_csv_columns(header: list[str], rows: list[list[str]]) -> list[dict[str, Any]]:
    """Count bounded full-file observations without automatically choosing a conversion."""
    output = []
    for index, column in enumerate(header):
        counts = {"iso": 0, "day_first": 0, "month_first": 0, "ambiguous": 0, "invalid": 0}
        numbers = {"numbers": 0, "nd": 0, "below_loq": 0, "other": 0, "blank": 0}
        examples = {}
        for row in rows:
            value = row[index]
            if not value:
                numbers["blank"] += 1
                continue
            options = _date_options(value)
            if options:
                if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                    category = "iso"
                elif "day_first" in options and "month_first" in options:
                    category = "ambiguous"
                else:
                    category = "day_first" if "day_first" in options else "month_first"
                counts[category] += 1
                examples.setdefault(
                    category,
                    {
                        "original": value,
                        "iso": options.get("iso"),
                        "day_first": options.get("day_first"),
                        "month_first": options.get("month_first"),
                    },
                )
            else:
                counts["invalid"] += 1
            flag = value.strip().upper()
            if _NUMBER.fullmatch(value) and math.isfinite(float(value)):
                numbers["numbers"] += 1
            elif flag == "ND":
                numbers["nd"] += 1
            elif flag == "<LOQ":
                numbers["below_loq"] += 1
            else:
                numbers["other"] += 1
        valid_dates = sum(counts[name] for name in ("iso", "day_first", "month_first", "ambiguous"))
        dates = (
            {**counts, "examples": list(examples.values())}
            if valid_dates and valid_dates >= counts["invalid"]
            else None
        )
        qualifiers = (
            numbers if numbers["numbers"] and (numbers["nd"] or numbers["below_loq"]) else None
        )
        if dates or qualifiers:
            output.append({"column": column, "dates": dates, "qualifiers": qualifiers})
    return output
