"""Validated SQL evidence inputs. Only the store adapter parses raw rows."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, BeforeValidator, ConfigDict, Field, ValidationError

from phlo.run_evidence.models import EvidenceCompleteness, RunStatus


class InvalidRunEvidence(ValueError):
    """A durable evidence row violates the storage contract."""


type StoredStatus = Annotated[RunStatus, BeforeValidator(RunStatus.parse)]


class StoredRow(BaseModel):
    """Identity and attempt shared by all persisted evidence."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    project_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    attempt: int = Field(ge=1, strict=True)


class StoredRun(StoredRow):
    """Typed parent record, including reconciliation timestamps."""

    status: StoredStatus
    pipeline_name: str | None = None
    provider_run_id: str | None = None
    started_at: AwareDatetime | None = None
    finished_at: AwareDatetime | None = None
    last_heartbeat_at: AwareDatetime | None = None
    evidence_completeness: EvidenceCompleteness


class EventPayload(BaseModel):
    """Lifecycle fields consumed by reconciliation; other payload fields are opaque."""

    model_config = ConfigDict(frozen=True, extra="allow")

    status: StoredStatus | None = None
    run_status: StoredStatus | None = None
    no_data: bool = Field(default=False, strict=True)
    stage_id: str | None = None


class StoredEvent(StoredRow):
    """An event with a decoded and validated lifecycle payload."""

    event_id: str = Field(min_length=1)
    producer: str = Field(min_length=1)
    event_type: str = Field(min_length=1)
    schema_version: str
    stage_id: str | None = None
    observed_at: AwareDatetime
    sequence: int | None = None
    payload: EventPayload
    payload_checksum: str


class StoredStage(StoredRow):
    """A stage whose timestamps and lifecycle status were checked at ingress."""

    stage_id: str = Field(min_length=1)
    stage_type: str
    provider: str | None = None
    tool: str | None = None
    asset: str | None = None
    status: StoredStatus
    started_at: AwareDatetime | None = None
    finished_at: AwareDatetime | None = None
    metrics: dict[str, object]
    error: str | None = None
    record_checksum: str


class EvidenceMetadata(BaseModel):
    """Completeness is typed; provider-specific metadata remains opaque."""

    model_config = ConfigDict(frozen=True, extra="allow")
    evidence_completeness: EvidenceCompleteness | None = None


class StoredRecord(StoredRow):
    """Common checksum and completeness contract for evidence families."""

    record_checksum: str
    metadata: EvidenceMetadata = Field(default_factory=EvidenceMetadata)

    @property
    def record_id(self) -> str:
        """Stable family-specific identifier."""
        raise NotImplementedError

    @property
    def evidence_status(self) -> str | None:
        """Status vocabulary specific to this family."""
        return None


class StoredResource(StoredRecord):
    family: Literal["resource"] = "resource"
    resource_id: str = Field(min_length=1)

    @property
    def record_id(self) -> str:
        return self.resource_id


class StoredQualityResult(StoredRecord):
    family: Literal["quality_result"] = "quality_result"
    quality_result_id: str = Field(min_length=1)
    passed: bool = Field(strict=True)

    @property
    def record_id(self) -> str:
        return self.quality_result_id

    @property
    def evidence_status(self) -> str:
        return "passed" if self.passed else "failed"


class StoredCatalogChange(StoredRecord):
    family: Literal["catalog_change"] = "catalog_change"
    catalog_change_id: str = Field(min_length=1)
    merge_outcome: Literal["merged", "failed", "conflict", "skipped", "cancelled"] | None = None

    @property
    def record_id(self) -> str:
        return self.catalog_change_id

    @property
    def evidence_status(self) -> str | None:
        return self.merge_outcome


class StoredArtifact(StoredRecord):
    family: Literal["artifact"] = "artifact"
    artifact_id: str = Field(min_length=1)
    status: EvidenceCompleteness
    expires_at: AwareDatetime | None = None
    legal_hold: bool = Field(strict=True)

    @property
    def record_id(self) -> str:
        return self.artifact_id

    @property
    def evidence_status(self) -> str:
        return self.status.value


type EvidenceRecord = Annotated[
    StoredResource | StoredQualityResult | StoredCatalogChange | StoredArtifact,
    Field(discriminator="family"),
]


def parse_stored_row[T: BaseModel](model: type[T], row: dict[str, object]) -> T:
    """Translate validation errors without including potentially sensitive row values."""
    try:
        return model.model_validate(row)
    except ValidationError as exc:
        fields = ", ".join(".".join(map(str, error["loc"])) for error in exc.errors())
        raise InvalidRunEvidence(f"invalid {model.__name__} fields: {fields}") from exc
