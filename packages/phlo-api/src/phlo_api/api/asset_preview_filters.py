"""Compile bounded typed preview predicates without accepting SQL fragments."""

from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    TypeAdapter,
)


class ComparisonFilter(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    column: str = Field(min_length=1, max_length=256)
    operator: Literal["eq", "ne", "lt", "lte", "gt", "gte"]
    value: StrictStr | StrictInt | StrictFloat | StrictBool


class NullFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")
    column: str = Field(min_length=1, max_length=256)
    operator: Literal["is_null", "is_not_null"]


PreviewFilter = ComparisonFilter | NullFilter
preview_filters = TypeAdapter(Annotated[list[PreviewFilter], Field(max_length=20)])


def preview_where(filters: list[PreviewFilter], columns: set[str]) -> str:
    predicates = []
    operators = {"eq": "=", "ne": "<>", "lt": "<", "lte": "<=", "gt": ">", "gte": ">="}
    for predicate in filters:
        if predicate.column not in columns or "\x00" in predicate.column:
            raise ValueError("Filter column is not in the selected table schema.")
        column = '"' + predicate.column.replace('"', '""') + '"'
        if isinstance(predicate, NullFilter):
            expression = "IS NULL" if predicate.operator == "is_null" else "IS NOT NULL"
        else:
            value = predicate.value
            if isinstance(value, str):
                if len(value) > 2048 or "\x00" in value:
                    raise ValueError("Filter value exceeds the supported text budget.")
                literal = "'" + value.replace("'", "''") + "'"
            elif isinstance(value, bool):
                literal = "TRUE" if value else "FALSE"
            else:
                literal = str(value)
            expression = f"{operators[predicate.operator]} {literal}"
        predicates.append(f"{column} {expression}")
    return " WHERE " + " AND ".join(predicates) if predicates else ""
