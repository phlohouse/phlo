"""Versioned draft metric definitions over observed assets, without semantic publication."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import Field, StringConstraints, model_validator

from phlo_api.api.authentication import get_request_principal
from phlo_api.api.operation_controls import audit_operation, project_root
from phlo_api.api.v1 import _target
from phlo_api.api.v1_assets import AssetDetail, v1_asset_detail
from phlo_api.errors import ConflictError, NotFoundError, UnprocessableInputError
from phlo_api.observatory_api.observatory_durable_state import load_collection, mutate_collection
from phlo_api.v1_contract import Environment, WireModel
from phlo_api.security_manifest import HTTP_ROUTE_MANIFEST, enforce_http_operation

router = APIRouter(tags=["v1 metrics"])
_COLLECTION = "v1_metric_models"
Identifier = Annotated[str, StringConstraints(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")]
ModelId = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_-]{0,62}$")]
ColumnKind = Literal["string", "number", "boolean", "time"]
Aggregation = Literal["count", "count_distinct", "sum", "avg", "min", "max"]


class MetricDimension(WireModel):
    name: Identifier
    column: Identifier
    kind: ColumnKind
    description: str = Field(default="", max_length=500)


class MetricMeasure(WireModel):
    name: Identifier
    column: Identifier
    aggregation: Aggregation
    description: str = Field(default="", max_length=500)


class MetricDefinition(WireModel):
    model_id: ModelId
    description: str = Field(min_length=1, max_length=4000)
    owner: str = Field(min_length=1, max_length=160)
    asset_id: str = Field(min_length=1, max_length=500)
    dimensions: list[MetricDimension] = Field(default_factory=list, max_length=20)
    measures: list[MetricMeasure] = Field(min_length=1, max_length=20)
    time_dimension: Identifier | None = None

    @model_validator(mode="after")
    def validate_names(self) -> MetricDefinition:
        names = [item.name for item in [*self.dimensions, *self.measures]]
        if len(set(names)) != len(names):
            raise ValueError("Measure and dimension names must be unique.")
        if self.time_dimension is not None and not any(
            item.name == self.time_dimension and item.kind == "time" for item in self.dimensions
        ):
            raise ValueError("The time dimension must name a declared time dimension.")
        if not self.owner.strip() or not self.description.strip():
            raise ValueError("Owner and description must be non-empty.")
        return self


class MetricWrite(MetricDefinition):
    expected_version: int = Field(ge=0)


class MetricRecord(MetricDefinition):
    env: Environment
    nessie_ref: str
    relation: str
    version: int = Field(ge=1)
    status: Literal["draft", "archived"] = "draft"
    created_at: str
    updated_at: str


class MetricInventoryItem(MetricRecord):
    """Expose an authorized draft even when its source needs repair."""

    source_status: Literal["available", "unavailable"]
    source_reason: str | None


class MetricInventory(WireModel):
    env: Environment
    items: list[MetricInventoryItem]
    publication: Literal["draft_definitions_only"] = "draft_definitions_only"


class MetricLifecycleWrite(WireModel):
    expected_version: int = Field(ge=1)


class MetricExport(WireModel):
    format: Literal["phlo.metric-draft"] = "phlo.metric-draft"
    schema_version: Literal[1] = 1
    env: Environment
    nessie_ref: str
    version: int
    status: Literal["draft", "archived"]
    definition: MetricDefinition


class MetricPreviewRequest(WireModel):
    group_by: list[Identifier] = Field(default_factory=list, max_length=10)


class MetricPreview(WireModel):
    env: Environment
    model_id: str
    version: int
    sql: str
    source: Literal["draft_metric_definition"] = "draft_metric_definition"


def _path() -> Path:
    return project_root() / ".phlo" / "metric-models.json"


def _records(env: Environment, ref: str) -> list[MetricRecord]:
    records = [
        MetricRecord.model_validate(item)
        for item in load_collection(project_root(), _COLLECTION, _path())
    ]
    return [record for record in records if record.env == env and record.nessie_ref == ref]


def _record(request: Request, env: Environment, model_id: str) -> MetricRecord:
    target = _target(request, env)
    record = next(
        (record for record in _records(env, target.nessie_ref) if record.model_id == model_id), None
    )
    if record is None:
        raise NotFoundError("Metric model was not found in this environment and ref.")
    return record


def column_kind(value: str | None) -> ColumnKind | None:
    """Classify only explicit scalar source types; unknown types cannot be declared."""
    token = (value or "").lower().split("(", 1)[0].strip()
    if token in {
        "int",
        "integer",
        "bigint",
        "smallint",
        "tinyint",
        "double",
        "float",
        "real",
        "decimal",
        "numeric",
        "long",
    }:
        return "number"
    if token in {
        "date",
        "timestamp",
        "timestamptz",
        "timestamp with time zone",
        "timestamp without time zone",
    }:
        return "time"
    if token in {"bool", "boolean"}:
        return "boolean"
    if token in {"string", "varchar", "char", "text"}:
        return "string"
    return None


async def _source(request: Request, env: Environment, definition: MetricDefinition) -> AssetDetail:
    await enforce_http_operation(
        request, HTTP_ROUTE_MANIFEST["v1_asset_detail"], {"asset_id": definition.asset_id}
    )
    source = await v1_asset_detail(request, definition.asset_id, env)
    relation = source.relation or ""
    if len(relation.split(".")) != 2 or any(not part for part in relation.split(".")):
        raise UnprocessableInputError(
            "The source asset must have an environment-scoped schema.table relation."
        )
    columns = {column.name: column_kind(column.type) for column in source.columns}
    for dimension in definition.dimensions:
        if columns.get(dimension.column) != dimension.kind:
            raise UnprocessableInputError(
                "Every dimension must match an observed scalar column type."
            )
    for measure in definition.measures:
        if measure.column not in columns or columns[measure.column] is None:
            raise UnprocessableInputError("Every measure must reference an observed scalar column.")
        if measure.aggregation in {"sum", "avg"} and columns[measure.column] != "number":
            raise UnprocessableInputError("Sum and average require numeric source columns.")
    return source


async def _validate_record_source(request: Request, env: Environment, record: MetricRecord) -> None:
    source = await _source(request, env, record)
    if source.relation != record.relation:
        raise ConflictError(
            "The source relation changed. Save a current draft before reading this model."
        )


def metric_sql(record: MetricRecord, group_by: list[str]) -> str:
    """Compile only validated identifiers and an explicit aggregation allowlist."""
    dimensions = {dimension.name: dimension for dimension in record.dimensions}
    if len(set(group_by)) != len(group_by) or any(name not in dimensions for name in group_by):
        raise UnprocessableInputError("Group by must contain unique declared dimension names.")

    def quote(value: str) -> str:
        return '"' + value.replace('"', '""') + '"'

    selected = [f"{quote(dimensions[name].column)} AS {quote(name)}" for name in group_by]
    for measure in record.measures:
        column = quote(measure.column)
        expression = (
            f"COUNT(DISTINCT {column})"
            if measure.aggregation == "count_distinct"
            else f"{measure.aggregation.upper()}({column})"
        )
        selected.append(f"{expression} AS {quote(measure.name)}")
    sql = (
        "SELECT\n  "
        + ",\n  ".join(selected)
        + "\nFROM "
        + ".".join(quote(part) for part in record.relation.split("."))
    )
    if group_by:
        positions = ", ".join(str(index + 1) for index in range(len(group_by)))
        sql += f"\nGROUP BY {positions}\nORDER BY {positions}"
    return sql + "\nLIMIT 100"


@router.get("/metric-models", response_model=MetricInventory)
async def v1_metric_models(
    request: Request, env: Environment = Query(), include_archived: bool = Query(False)
) -> MetricInventory:
    target = _target(request, env, allowed_query=frozenset({"env", "include_archived"}))
    scoped = Request({**request.scope, "query_string": urlencode({"env": env}).encode()})
    records = _records(env, target.nessie_ref)
    items = []
    for record in records:
        if record.status == "archived" and not include_archived:
            continue
        try:
            items.append(await _inventory_item(scoped, env, record))
        except HTTPException as exc:
            if exc.status_code == 403:
                continue
            raise
    return MetricInventory(env=env, items=items)


async def _inventory_item(
    request: Request, env: Environment, record: MetricRecord
) -> MetricInventoryItem:
    reason = None
    try:
        await _validate_record_source(request, env, record)
    except HTTPException as exc:
        if exc.status_code != 404:
            raise
        reason = "Source asset is unavailable. Repair the draft before previewing."
    except (NotFoundError, UnprocessableInputError, ConflictError):
        reason = "Source asset or schema changed. Repair the draft before previewing."
    return MetricInventoryItem(
        **record.model_dump(),
        source_status="unavailable" if reason else "available",
        source_reason=reason,
    )


@router.get("/metric-models/{model_id}", response_model=MetricInventoryItem)
async def v1_metric_model(
    request: Request, model_id: ModelId, env: Environment = Query()
) -> MetricInventoryItem:
    record = _record(request, env, model_id)
    return await _inventory_item(request, env, record)


async def _write(request: Request, env: Environment, payload: MetricWrite) -> MetricRecord:
    target = _target(request, env)
    if payload.expected_version:
        current = _record(request, env, payload.model_id)
        if current.version != payload.expected_version:
            raise ConflictError("The metric model changed. Reload before saving.")
        await enforce_http_operation(
            request, HTTP_ROUTE_MANIFEST["v1_asset_detail"], {"asset_id": current.asset_id}
        )
        if current.status == "archived":
            raise ConflictError("Restore the archived metric model before editing.")
    source = await _source(request, env, payload)
    relation = source.relation
    assert relation is not None
    now = datetime.now(UTC).isoformat()
    selected: MetricRecord | None = None

    def change(items: list[dict]) -> list[dict]:
        nonlocal selected
        records = [MetricRecord.model_validate(item) for item in items]
        current = next(
            (
                item
                for item in records
                if item.env == env
                and item.nessie_ref == target.nessie_ref
                and item.model_id == payload.model_id
            ),
            None,
        )
        if payload.expected_version != (current.version if current else 0):
            raise ConflictError("The metric model changed. Reload before saving.")
        if current is not None and current.status == "archived":
            raise ConflictError("Restore the archived metric model before editing.")
        if current is None and len(records) >= 1000:
            raise ConflictError(
                "The 1000-record retained metric model limit was reached. "
                "Archiving only frees active slots."
            )
        if current is None and sum(item.status == "draft" for item in records) >= 500:
            raise ConflictError("The 500-active-draft metric model limit was reached.")
        selected = MetricRecord(
            **payload.model_dump(exclude={"expected_version"}),
            env=env,
            nessie_ref=target.nessie_ref,
            relation=relation,
            version=(current.version if current else 0) + 1,
            created_at=current.created_at if current else now,
            updated_at=now,
        )
        return [item.model_dump() for item in records if item is not current] + [
            selected.model_dump()
        ]

    mutate_collection(project_root(), _COLLECTION, _path(), change)
    assert selected is not None
    principal = get_request_principal(request)
    audit_operation(
        operation="metric_model.create" if payload.expected_version == 0 else "metric_model.update",
        target=f"{env}:{target.nessie_ref}:{payload.model_id}",
        dry_run=False,
        auth={"subject": principal.subject if principal else "unknown", "scopes": []},
        payload={"expected_version": payload.expected_version, "asset_id": payload.asset_id},
        result={"version": selected.version, "status": "draft"},
    )
    return selected


@router.post("/metric-models", response_model=MetricRecord, status_code=201)
async def v1_metric_model_create(
    request: Request, payload: MetricWrite, env: Environment = Query()
) -> MetricRecord:
    if payload.expected_version != 0:
        raise UnprocessableInputError("New metric models require expected_version zero.")
    return await _write(request, env, payload)


@router.put("/metric-models/{model_id}", response_model=MetricRecord)
async def v1_metric_model_update(
    request: Request, model_id: ModelId, payload: MetricWrite, env: Environment = Query()
) -> MetricRecord:
    if model_id != payload.model_id or payload.expected_version < 1:
        raise UnprocessableInputError("Updates require the matching model ID and current version.")
    return await _write(request, env, payload)


async def _set_status(
    request: Request,
    env: Environment,
    model_id: str,
    payload: MetricLifecycleWrite,
    status: Literal["draft", "archived"],
) -> MetricRecord:
    original = _record(request, env, model_id)
    if original.version != payload.expected_version:
        raise ConflictError("The metric model changed. Reload before changing its status.")
    await enforce_http_operation(
        request, HTTP_ROUTE_MANIFEST["v1_asset_detail"], {"asset_id": original.asset_id}
    )
    selected: MetricRecord | None = None

    def change(items: list[dict]) -> list[dict]:
        nonlocal selected
        records = [MetricRecord.model_validate(item) for item in items]
        current = next(
            item
            for item in records
            if (item.env, item.nessie_ref, item.model_id)
            == (original.env, original.nessie_ref, original.model_id)
        )
        if current.version != payload.expected_version or current.status == status:
            raise ConflictError("The metric model changed. Reload before changing its status.")
        if status == "draft" and sum(item.status == "draft" for item in records) >= 500:
            raise ConflictError("The 500-active-draft metric model limit was reached.")
        selected = current.model_copy(
            update={
                "status": status,
                "version": current.version + 1,
                "updated_at": datetime.now(UTC).isoformat(),
            }
        )
        return [
            item.model_dump() if item is not current else selected.model_dump() for item in records
        ]

    mutate_collection(project_root(), _COLLECTION, _path(), change)
    assert selected is not None
    principal = get_request_principal(request)
    audit_operation(
        operation="metric_model.archive" if status == "archived" else "metric_model.restore",
        target=f"{env}:{selected.nessie_ref}:{model_id}",
        dry_run=False,
        auth={"subject": principal.subject if principal else "unknown", "scopes": []},
        payload=payload.model_dump(),
        result={"version": selected.version, "status": selected.status},
    )
    return selected


@router.post("/metric-models/{model_id}/archive", response_model=MetricRecord)
async def v1_metric_model_archive(
    request: Request, model_id: ModelId, payload: MetricLifecycleWrite, env: Environment = Query()
) -> MetricRecord:
    return await _set_status(request, env, model_id, payload, "archived")


@router.post("/metric-models/{model_id}/restore", response_model=MetricRecord)
async def v1_metric_model_restore(
    request: Request, model_id: ModelId, payload: MetricLifecycleWrite, env: Environment = Query()
) -> MetricRecord:
    return await _set_status(request, env, model_id, payload, "draft")


@router.get("/metric-models/{model_id}/export", response_model=MetricExport)
async def v1_metric_model_export(
    request: Request, model_id: ModelId, env: Environment = Query()
) -> MetricExport:
    record = _record(request, env, model_id)
    await enforce_http_operation(
        request, HTTP_ROUTE_MANIFEST["v1_asset_detail"], {"asset_id": record.asset_id}
    )
    return MetricExport(
        env=env,
        nessie_ref=record.nessie_ref,
        version=record.version,
        status=record.status,
        definition=MetricDefinition.model_validate(
            {
                key: value
                for key, value in record.model_dump().items()
                if key in MetricDefinition.model_fields
            }
        ),
    )


@router.post("/metric-models/{model_id}/preview-sql", response_model=MetricPreview)
async def v1_metric_model_preview(
    request: Request, model_id: ModelId, payload: MetricPreviewRequest, env: Environment = Query()
) -> MetricPreview:
    record = _record(request, env, model_id)
    if record.status == "archived":
        raise ConflictError("Restore the archived metric model before previewing.")
    source = await _source(request, env, record)
    if source.relation != record.relation:
        raise ConflictError(
            "The source relation changed. Reload and save the draft before previewing."
        )
    return MetricPreview(
        env=env, model_id=model_id, version=record.version, sql=metric_sql(record, payload.group_by)
    )
