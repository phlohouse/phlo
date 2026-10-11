"""Declarative evidence contribution contracts, without provider discovery."""

from dataclasses import dataclass
from typing import Protocol

from phlo.run_evidence.reconciliation import RequiredEvidenceRecord, RequiredEvidenceStage

CANONICAL_STAGES = frozenset({"ingest", "transform", "check", "publish", "lineage"})


@dataclass(frozen=True, slots=True)
class EvidenceProfileContribution:
    """Declarative evidence requirements for one provider/stage."""

    contribution_id: str
    provider: str
    profile_id: str
    profile_version: str
    stages: tuple[RequiredEvidenceStage, ...] = ()
    required_run_fields: tuple[str, ...] = ()
    required_records: tuple[RequiredEvidenceRecord, ...] = ()
    requires_terminal_event: bool = True
    requires_contributions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.contribution_id.strip() or not self.provider.strip():
            raise ValueError("contribution_id and provider must be non-empty")
        if not self.profile_id.strip() or not self.profile_version.strip():
            raise ValueError("profile_id and profile_version must be non-empty")
        for stage in self.stages:
            if stage.stage_type not in CANONICAL_STAGES:
                raise ValueError(f"unsupported stage {stage.stage_type!r}")
        if len({stage.stage_type for stage in self.stages}) != len(self.stages):
            raise ValueError("a contribution must not repeat a stage")
        if not all(dep.strip() for dep in self.requires_contributions):
            raise ValueError("requires_contributions must contain non-empty ids")


class EvidenceProfileContributionProvider(Protocol):
    """Read-only provider wrapper for declarative contribution data."""

    @property
    def contribution(self) -> EvidenceProfileContribution: ...
