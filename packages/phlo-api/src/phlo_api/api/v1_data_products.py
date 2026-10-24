"""Durable draft collections of canonical Datasets, without publication or access effects."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import Field, StringConstraints, model_validator

from phlo.dataset_projection import DatasetAuthority, build_dataset_authority
from phlo.dataset_state import DatasetStoreResolutionError
from phlo.plugins.observatory_settings import StorageUnavailableError
from phlo_api.api.authentication import get_request_principal
from phlo_api.api.operation_controls import audit_operation, project_root
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


class ProductDeclaration(WireModel):
    """Descriptive collection metadata; no member policy or state is changed."""

    product_id: ProductId
    name: ShortText
    purpose: str = Field(min_length=1, max_length=4000)
    owner: ShortText
    classifications: list[ShortText] = Field(min_length=1, max_length=10)
    member_dataset_ids: list[str] = Field(min_length=1, max_length=50)
    expected_version: int = Field(ge=0)

    @model_validator(mode="after")
    def unique_members(self) -> ProductDeclaration:
        if len(set(self.member_dataset_ids)) != len(self.member_dataset_ids):
            raise ValueError("Member Dataset IDs must be unique.")
        if not self.purpose.strip() or any(
            not member.strip() for member in self.member_dataset_ids
        ):
            raise ValueError("Purpose and member Dataset IDs must be non-empty.")
        return self


class ProductRecord(WireModel):
    product_id: ProductId
    env: Environment
    name: ShortText
    purpose: str = Field(min_length=1, max_length=4000)
    owner: ShortText
    classifications: list[ShortText] = Field(min_length=1, max_length=10)
    member_dataset_ids: list[str] = Field(min_length=1, max_length=50)
    version: int = Field(ge=1)
    status: Literal["draft"] = "draft"
    created_at: str
    updated_at: str


class ProductMember(WireModel):
    dataset_id: str
    canonical: DatasetProjection | None
    reason: str | None


class ProductView(ProductRecord):
    members: list[ProductMember]
    sharing_status: Literal["unavailable"] = "unavailable"
    sharing_reason: str = (
        "Client shares, access requests and signatures are not implemented for draft products."
    )


class ProductInventory(WireModel):
    env: Environment
    items: list[ProductView] = Field(max_length=500)


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


def _records(env: Environment) -> list[ProductRecord]:
    items = load_collection(project_root(), _COLLECTION, _legacy_path())
    if len(items) > 500:
        raise BackendUnavailableError(
            "The product declaration collection exceeds its 500-record limit."
        )
    return [record for item in items if (record := ProductRecord.model_validate(item)).env == env]


@router.get("/data-products", response_model=ProductInventory)
async def v1_data_products(request: Request, env: Environment = Query()) -> ProductInventory:
    """List declared collections; every product remains an unpublished draft."""
    _target(request, env)
    authority = _authority()
    visible = await _visible_relations(request, env)
    return ProductInventory(
        env=env, items=[_view(record, authority, visible) for record in _records(env)]
    )


@router.get("/data-products/{product_id}", response_model=ProductView)
async def v1_data_product(
    request: Request, product_id: ProductId, env: Environment = Query()
) -> ProductView:
    _target(request, env)
    record = next((item for item in _records(env) if item.product_id == product_id), None)
    if record is None:
        raise NotFoundError("Data product was not found in this environment.")
    return _view(record, _authority(), await _visible_relations(request, env))


async def _write_product(
    request: Request, env: Environment, payload: ProductDeclaration
) -> ProductView:
    _target(request, env)
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
        records = [ProductRecord.model_validate(item) for item in items]
        current = next(
            (item for item in records if item.env == env and item.product_id == payload.product_id),
            None,
        )
        if current is None and len(records) >= 500:
            raise UnprocessableInputError(
                "The project has reached its 500-product declaration limit."
            )
        version = current.version if current else 0
        if payload.expected_version != version:
            raise ConflictError("The product declaration changed. Reload before saving.")
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

    mutate_collection(project_root(), _COLLECTION, _legacy_path(), change)
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
