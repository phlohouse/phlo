"""Durable draft collections of canonical Datasets, without publication or access effects."""

from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
import json
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import Field, StringConstraints, ValidationError, model_validator

from phlo.dataset_projection import DatasetAuthority, build_dataset_authority
from phlo.dataset_state import DatasetStoreResolutionError
from phlo.plugins.observatory_settings import StorageUnavailableError
from phlo_api.api.authentication import get_request_principal
from phlo_api.api.operation_controls import (
    audit_operation,
    project_root,
    validate_payload_retention,
)
from phlo_api.api.v1 import _target
from phlo_api.api.v1_assets import _assets
from phlo_api.api.v1_datasets import DatasetProjection
from phlo_api.errors import (
    BackendUnavailableError,
    ConflictError,
    NotFoundError,
    UnprocessableInputError,
)
from phlo_api.observatory_api import observatory
from phlo_api.observatory_api.observatory_durable_state import load_collection, mutate_collection
from phlo_api.v1_contract import Environment, WireModel
from phlo_api.security_manifest import HTTP_ROUTE_MANIFEST, enforce_http_operation

router = APIRouter(tags=["v1 data products"])
_COLLECTION = "v1_data_products"
ProductId = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_-]{0,62}$")]
ShortText = Annotated[str, StringConstraints(min_length=1, max_length=160, strip_whitespace=True)]


class ProductDefinition(WireModel):
    """Descriptive collection metadata; no member policy or state is changed."""

    product_id: ProductId
    name: ShortText
    purpose: str = Field(min_length=1, max_length=4000)
    owner: ShortText
    classifications: list[ShortText] = Field(min_length=1, max_length=10)
    member_dataset_ids: list[str] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def unique_members(self) -> ProductDefinition:
        if len(set(self.member_dataset_ids)) != len(self.member_dataset_ids):
            raise ValueError("Member Dataset IDs must be unique.")
        if not self.purpose.strip() or any(
            not member.strip() for member in self.member_dataset_ids
        ):
            raise ValueError("Purpose and member Dataset IDs must be non-empty.")
        return self


class ProductDeclaration(ProductDefinition):
    expected_version: int = Field(ge=0)


class ProductRecord(WireModel):
    product_id: ProductId
    env: Environment
    name: ShortText
    purpose: str = Field(min_length=1, max_length=4000)
    owner: ShortText
    classifications: list[ShortText] = Field(min_length=1, max_length=10)
    member_dataset_ids: list[str] = Field(min_length=1, max_length=50)
    version: int = Field(ge=1)
    status: Literal["draft", "archived"] = "draft"
    created_at: str
    updated_at: str


class ProductMember(WireModel):
    dataset_id: str
    canonical: DatasetProjection | None
    reason: str | None


class ProductTombstone(WireModel):
    product_id: ProductId
    env: Environment
    version: int = Field(ge=1)
    status: Literal["reclaimed"] = "reclaimed"
    definition_sha256: str
    created_at: str
    reclaimed_at: str
    reclaimed_by: str
    reason: str


class ProductView(ProductRecord):
    members: list[ProductMember]
    sharing_status: Literal["unavailable"] = "unavailable"
    sharing_reason: str = (
        "Client shares, access requests and signatures are not implemented for draft products."
    )


class ProductInventory(WireModel):
    env: Environment
    items: list[ProductView]


class ProductLifecycleWrite(WireModel):
    expected_version: int = Field(ge=1)


class ProductReclaimWrite(ProductLifecycleWrite):
    confirmed: Literal[True]
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]


class ProductExport(WireModel):
    format: Literal["phlo.product-draft"] = "phlo.product-draft"
    schema_version: Literal[1] = 1
    env: Environment
    version: int
    status: Literal["draft", "archived"]
    definition: ProductDefinition


def _legacy_path() -> Path:
    return project_root() / ".phlo" / "data-products.json"


def _authority() -> DatasetAuthority:
    if observatory._load_capability_registry() is None:
        raise BackendUnavailableError("Project declaration registry is unavailable.")
    try:
        return build_dataset_authority(str(project_root()))
    except (DatasetStoreResolutionError, StorageUnavailableError) as exc:
        raise BackendUnavailableError("Core Dataset authority is unavailable.") from exc


def _view(record: ProductRecord, authority: DatasetAuthority, visible: set[str]) -> ProductView:
    members = [
        ProductMember(
            dataset_id=member,
            canonical=(
                DatasetProjection.model_validate(authority.projection(member))
                if member in visible
                else None
            ),
            reason=(
                "Dataset asset is unavailable or not authorized in this environment."
                if member not in visible
                else None
                if member in authority.surface.tables
                else "Governance declarations are incomplete."
            ),
        )
        for member in record.member_dataset_ids
    ]
    return ProductView(**record.model_dump(), members=members)


async def _visible_relations(request: Request, env: Environment) -> set[str]:
    visible: set[str] = set()
    for asset in await _assets(request, env):
        if not asset.relation:
            continue
        try:
            await enforce_http_operation(
                request=request,
                spec=HTTP_ROUTE_MANIFEST["v1_asset_detail"],
                path_params={"asset_id": asset.id},
            )
        except HTTPException as exc:
            if exc.status_code == 403:
                continue
            raise
        visible.add(asset.relation)
    return visible


def _stored_records(items: list[dict[str, Any]]) -> list[ProductRecord | ProductTombstone]:
    return [
        ProductTombstone.model_validate(item)
        if item.get("status") == "reclaimed"
        else ProductRecord.model_validate(item)
        for item in items
    ]


def _records(env: Environment) -> list[ProductRecord | ProductTombstone]:
    return [
        record
        for record in _stored_records(
            load_collection(project_root(), _COLLECTION, _legacy_path(), require_known_state=True)
        )
        if record.env == env
    ]


@router.get("/data-products", response_model=ProductInventory)
async def v1_data_products(
    request: Request, env: Environment = Query(), include_archived: bool = Query(False)
) -> ProductInventory:
    """List declared collections; every product remains an unpublished draft."""
    _target(request, env, allowed_query=frozenset({"env", "include_archived"}))
    scoped = Request({**request.scope, "query_string": urlencode({"env": env}).encode()})
    authority = _authority()
    visible = await _visible_relations(scoped, env)
    return ProductInventory(
        env=env,
        items=[
            _view(record, authority, visible)
            for record in _records(env)
            if isinstance(record, ProductRecord) and (record.status == "draft" or include_archived)
        ],
    )


def _record(request: Request, env: Environment, product_id: str) -> ProductRecord:
    _target(request, env)
    record = next((item for item in _records(env) if item.product_id == product_id), None)
    if record is None:
        raise NotFoundError("Data product was not found in this environment.")
    if isinstance(record, ProductTombstone):
        raise HTTPException(
            410, "The product draft definition was reclaimed. Its identity is permanently reserved."
        )
    return record


@router.get("/data-products/{product_id}", response_model=ProductView)
async def v1_data_product(
    request: Request, product_id: ProductId, env: Environment = Query()
) -> ProductView:
    record = _record(request, env, product_id)
    return _view(record, _authority(), await _visible_relations(request, env))


async def _write_product(
    request: Request, env: Environment, payload: ProductDeclaration
) -> ProductView:
    _target(request, env)
    if payload.expected_version and _record(request, env, payload.product_id).status == "archived":
        raise ConflictError("Restore the archived product declaration before editing.")
    authority = _authority()
    relations = await _visible_relations(request, env)
    if any(member not in relations for member in payload.member_dataset_ids):
        raise UnprocessableInputError(
            "Each member must identify an actual asset relation in the selected environment."
        )
    principal = get_request_principal(request)
    if principal is None:
        raise HTTPException(401, "Authentication is required.")
    now = datetime.now(UTC).isoformat()
    selected: ProductRecord | None = None

    def change(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        nonlocal selected
        records = _stored_records(items)
        current = next(
            (item for item in records if item.env == env and item.product_id == payload.product_id),
            None,
        )
        if isinstance(current, ProductTombstone):
            raise ConflictError("The reclaimed product identity is permanently reserved.")
        if current is None and sum(item.status != "reclaimed" for item in records) >= 1000:
            raise UnprocessableInputError(
                "The project has reached its 1000-record retained product limit. "
                "Archiving only frees active slots."
            )
        if current is None and sum(item.status == "draft" for item in records) >= 500:
            raise UnprocessableInputError(
                "The project has reached its 500-active-product declaration limit."
            )
        version = current.version if current else 0
        if payload.expected_version != version:
            raise ConflictError("The product declaration changed. Reload before saving.")
        if current is not None and current.status == "archived":
            raise ConflictError("Restore the archived product declaration before editing.")
        selected = ProductRecord(
            **payload.model_dump(exclude={"expected_version"}),
            env=env,
            version=version + 1,
            created_at=current.created_at if current else now,
            updated_at=now,
        )
        return [
            item.model_dump()
            for item in records
            if not (item.env == env and item.product_id == payload.product_id)
        ] + [selected.model_dump()]

    mutate_collection(project_root(), _COLLECTION, _legacy_path(), change, require_known_state=True)
    assert selected is not None
    raw_scopes = principal.claims.get("scopes", [])
    scopes = (
        raw_scopes.split()
        if isinstance(raw_scopes, str)
        else list(raw_scopes)
        if isinstance(raw_scopes, (list, tuple))
        else []
    )
    audit_operation(
        operation="data_product.declare",
        target=f"{env}/{selected.product_id}",
        dry_run=False,
        auth={"subject": principal.subject, "scopes": scopes},
        payload=payload.model_dump(),
        result={"version": selected.version, "status": selected.status},
    )
    return _view(selected, authority, relations)


@router.post("/data-products", response_model=ProductView, status_code=201)
async def v1_data_product_create(
    request: Request, payload: ProductDeclaration, env: Environment = Query()
) -> ProductView:
    if payload.expected_version != 0:
        raise UnprocessableInputError("New product declarations require expected_version zero.")
    return await _write_product(request, env, payload)


@router.put("/data-products/{product_id}", response_model=ProductView)
async def v1_data_product_update(
    request: Request, product_id: ProductId, payload: ProductDeclaration, env: Environment = Query()
) -> ProductView:
    if product_id != payload.product_id or payload.expected_version < 1:
        raise UnprocessableInputError("Updates require a matching product ID and current version.")
    return await _write_product(request, env, payload)


async def _set_status(
    request: Request,
    env: Environment,
    product_id: str,
    payload: ProductLifecycleWrite,
    status: Literal["draft", "archived"],
) -> ProductView:
    _record(request, env, product_id)
    authority = _authority()
    visible = await _visible_relations(request, env)
    principal = get_request_principal(request)
    if principal is None:
        raise HTTPException(401, "Authentication is required.")
    selected: ProductRecord | None = None

    def change(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        nonlocal selected
        records = _stored_records(items)
        current = next(item for item in records if (item.env, item.product_id) == (env, product_id))
        if (
            isinstance(current, ProductTombstone)
            or current.version != payload.expected_version
            or current.status == status
        ):
            raise ConflictError(
                "The product declaration changed. Reload before changing its status."
            )
        if status == "draft" and sum(item.status == "draft" for item in records) >= 500:
            raise ConflictError("The project has reached its 500-active-product declaration limit.")
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

    mutate_collection(project_root(), _COLLECTION, _legacy_path(), change, require_known_state=True)
    assert selected is not None
    audit_operation(
        operation="data_product.archive" if status == "archived" else "data_product.restore",
        target=f"{env}/{product_id}",
        dry_run=False,
        auth={"subject": principal.subject, "scopes": []},
        payload=payload.model_dump(),
        result={"version": selected.version, "status": selected.status},
    )
    return _view(selected, authority, visible)


@router.post("/data-products/{product_id}/archive", response_model=ProductView)
async def v1_data_product_archive(
    request: Request,
    product_id: ProductId,
    payload: ProductLifecycleWrite,
    env: Environment = Query(),
) -> ProductView:
    return await _set_status(request, env, product_id, payload, "archived")


@router.post("/data-products/{product_id}/restore", response_model=ProductView)
async def v1_data_product_restore(
    request: Request,
    product_id: ProductId,
    payload: ProductLifecycleWrite,
    env: Environment = Query(),
) -> ProductView:
    return await _set_status(request, env, product_id, payload, "draft")


@router.post("/data-products/{product_id}/reclaim", response_model=ProductTombstone)
async def v1_data_product_reclaim(
    request: Request,
    product_id: ProductId,
    payload: ProductReclaimWrite,
    env: Environment = Query(),
) -> ProductTombstone:
    """Remove only an archived local definition, never canonical Dataset state or policy."""
    _target(request, env)
    try:
        original = _record(request, env, product_id)
    except ValidationError as exc:
        raise ConflictError("Unknown record fields prevent safe reclamation.") from exc
    if original.version != payload.expected_version or original.status != "archived":
        raise ConflictError("Reclamation requires the current version of an archived product.")
    principal = get_request_principal(request)
    if principal is None:
        raise HTTPException(401, "Authentication is required.")
    selected: ProductTombstone | None = None

    def change(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        nonlocal selected
        try:
            records = _stored_records(items)
        except ValidationError as exc:
            raise ConflictError("Unknown record fields prevent safe reclamation.") from exc
        current = next(item for item in records if (item.env, item.product_id) == (env, product_id))
        if (
            not isinstance(current, ProductRecord)
            or current.version != payload.expected_version
            or current.status != "archived"
        ):
            raise ConflictError("The product declaration changed. Reload before reclaiming.")
        validate_payload_retention(current.updated_at)
        selected = ProductTombstone(
            product_id=product_id,
            env=env,
            version=current.version + 1,
            created_at=current.created_at,
            definition_sha256=sha256(
                json.dumps(current.model_dump(), sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
            reclaimed_at=datetime.now(UTC).isoformat(),
            reclaimed_by=principal.subject,
            reason=payload.reason,
        )
        return [selected.model_dump() if item is current else item.model_dump() for item in records]

    mutate_collection(project_root(), _COLLECTION, _legacy_path(), change, require_known_state=True)
    assert selected is not None
    audit_operation(
        operation="data_product.reclaim",
        target=f"{env}/{product_id}",
        dry_run=False,
        auth={"subject": principal.subject, "scopes": []},
        payload=payload.model_dump(),
        result=selected.model_dump(),
    )
    return selected


@router.get("/data-products/{product_id}/export", response_model=ProductExport)
async def v1_data_product_export(
    request: Request, product_id: ProductId, env: Environment = Query()
) -> ProductExport:
    """Export only declared metadata, never canonical Dataset details or access grants."""
    record = _record(request, env, product_id)
    return ProductExport(
        env=env,
        version=record.version,
        status=record.status,
        definition=ProductDefinition.model_validate(
            {
                key: value
                for key, value in record.model_dump().items()
                if key in ProductDefinition.model_fields
            }
        ),
    )
