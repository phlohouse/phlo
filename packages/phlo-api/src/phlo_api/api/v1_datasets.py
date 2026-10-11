"""Project-scoped Dataset declarations using the same authority as the CLI."""

from typing import Any, Literal

from fastapi import APIRouter, Query, Request
from pydantic import ConfigDict

from phlo.dataset_projection import build_dataset_authority
from phlo.dataset_state import DatasetStoreResolutionError
from phlo.plugins.observatory_settings import StorageUnavailableError
from phlo_api.api.operation_controls import project_root
from phlo_api.errors import BackendUnavailableError, BadInputError
from phlo_api.observatory_api import observatory
from phlo_api.v1_contract import WireModel

router = APIRouter(tags=["v1 datasets"])


class DatasetReadiness(WireModel):
    action: str
    ready: bool
    policy_version: str
    reasons: list[str]
    blockers: list[dict[str, Any]]
    warnings: list[dict[str, Any]]
    missing_evidence: list[dict[str, Any]]


class DatasetProjection(WireModel):
    model_config = ConfigDict(extra="allow")
    dataset_id: str
    table_id: str
    candidate: bool
    owner: str | None
    classifications: list[str]
    workflow_state: str | None
    publication_state: str | None
    approval_state: str | None
    declared: bool
    readiness: DatasetReadiness
    allowed_transitions: list[str]


class DatasetInventory(WireModel):
    scope: Literal["project"] = "project"
    source: Literal["core_dataset_authority"] = "core_dataset_authority"
    items: list[DatasetProjection]
    truncated: bool


@router.get("/datasets", response_model=DatasetInventory)
def v1_datasets(
    request: Request, limit: int = Query(default=100, ge=1, le=500)
) -> DatasetInventory:
    """Project declarations, not environment materialisations or all stored candidates."""
    if set(request.query_params) - {"limit"}:
        raise BadInputError(
            "Dataset authority is project-scoped; environment filters are unsupported."
        )
    if observatory._load_capability_registry() is None:
        raise BackendUnavailableError("Project declaration registry is unavailable.")
    try:
        authority = build_dataset_authority(str(project_root()))
        tables = sorted(authority.surface.tables)
        return DatasetInventory(
            items=[
                DatasetProjection.model_validate(authority.projection(table))
                for table in tables[:limit]
            ],
            truncated=len(tables) > limit,
        )
    except (DatasetStoreResolutionError, StorageUnavailableError) as exc:
        raise BackendUnavailableError("Core Dataset authority is unavailable.") from exc
