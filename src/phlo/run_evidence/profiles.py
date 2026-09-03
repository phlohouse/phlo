"""Neutral run-evidence profile composition (ADR 0048, Plan 007).

Providers contribute declarative evidence requirements through the
``evidence_profile_contribution`` capability family. Composition is
deterministic and versioned: it compares the ADR-frozen required
contribution set with what is actually registered, rejects duplicates,
conflicts, and dependency cycles, and produces a canonical digest. A
missing required contribution yields an unavailable/incomplete profile,
never an empty or partially healthy one.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from phlo.run_evidence.reconciliation import (
    RequiredEvidenceProfile,
    RequiredEvidenceRecord,
    RequiredEvidenceStage,
)

CANONICAL_STAGES = frozenset({"ingest", "transform", "check", "publish", "lineage"})
PROFILE_SCHEMA_VERSION = "1"


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


@dataclass(frozen=True, slots=True)
class RequiredEvidenceContributorSet:
    """The ADR-frozen required contribution set for one profile."""

    profile_id: str
    profile_version: str
    required_contribution_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ComposedEvidenceProfile:
    """Deterministic composition result for one profile."""

    profile: RequiredEvidenceProfile
    required_contribution_ids: tuple[str, ...]
    discovered_contribution_ids: tuple[str, ...]
    missing_contribution_ids: tuple[str, ...]
    digest: str
    conflicts: tuple[str, ...] = ()
    available: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile.profile_id,
            "profile_version": self.profile.version,
            "available": self.available,
            "required_contribution_ids": list(self.required_contribution_ids),
            "discovered_contribution_ids": list(self.discovered_contribution_ids),
            "missing_contribution_ids": list(self.missing_contribution_ids),
            "conflicts": list(self.conflicts),
            "digest": self.digest,
        }


class EvidenceProfileCompositionError(RuntimeError):
    """Stable, named composition failure."""

    def __init__(self, code: str, identifiers: Iterable[str] = ()) -> None:
        self.code = code
        self.identifiers = tuple(identifiers)
        super().__init__(f"{code}: {', '.join(identifiers)}")


def _canonical_profile_dict(profile: RequiredEvidenceProfile) -> dict[str, Any]:
    return {
        "profile_id": profile.profile_id,
        "version": profile.version,
        "pipeline_name": profile.pipeline_name,
        "provider": profile.provider,
        "stages": [
            {
                "stage_type": stage.stage_type,
                "provider": stage.provider,
                "required_event_types": list(stage.required_event_types),
                "required_status": stage.required_status,
                "allowed_statuses": list(stage.allowed_statuses),
                "allow_no_data": stage.allow_no_data,
            }
            for stage in profile.stages
        ],
        "run_terminal_event_types": list(profile.run_terminal_event_types),
        "required_run_fields": list(profile.required_run_fields),
        "required_records": [
            {
                "family": record.family,
                "minimum": record.minimum,
                "required_status": record.required_status,
            }
            for record in profile.required_records
        ],
    }


def _digest(
    profile: RequiredEvidenceProfile,
    required: tuple[str, ...],
    discovered: tuple[str, ...],
) -> str:
    payload = {
        "schema_version": PROFILE_SCHEMA_VERSION,
        "profile": _canonical_profile_dict(profile),
        "required_contribution_ids": list(required),
        "discovered_contribution_ids": list(discovered),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def compose_evidence_profile(
    profile_id: str,
    profile_version: str,
    required_contribution_ids: Iterable[str],
    contributions: Iterable[EvidenceProfileContribution],
) -> ComposedEvidenceProfile:
    """Compose one deterministic evidence profile from neutral contributions.

    Steps (ADR 0048 §2): exact profile/version match only; root-set comparison
    before dependencies; dependency presence; cycle rejection; canonical union
    of requirements; conflict rejection; canonical digest.
    """
    required = tuple(sorted(set(required_contribution_ids)))
    if not all(required):
        raise EvidenceProfileCompositionError("blank_contribution_id", required)

    by_id: dict[str, EvidenceProfileContribution] = {}
    for contribution in contributions:
        if not isinstance(contribution, EvidenceProfileContribution):
            raise EvidenceProfileCompositionError("invalid_contribution")
        if contribution.contribution_id in by_id:
            raise EvidenceProfileCompositionError(
                "duplicate_contribution", (contribution.contribution_id,)
            )
        if contribution.profile_id != profile_id or contribution.profile_version != profile_version:
            continue  # exact profile/version match only; never coerce versions.
        by_id[contribution.contribution_id] = contribution

    discovered = tuple(sorted(by_id))
    missing = tuple(sorted(set(required) - set(by_id)))
    if missing:
        unavailable_profile = RequiredEvidenceProfile(
            profile_id=profile_id, version=profile_version
        )
        return ComposedEvidenceProfile(
            profile=unavailable_profile,
            required_contribution_ids=required,
            discovered_contribution_ids=discovered,
            missing_contribution_ids=missing,
            digest=_digest(unavailable_profile, required, discovered),
            available=False,
        )

    for contribution in by_id.values():
        for dependency in contribution.requires_contributions:
            if dependency not in by_id:
                raise EvidenceProfileCompositionError(
                    "missing_dependency", (contribution.contribution_id, dependency)
                )

    # Topological validation for cycles.
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(contribution_id: str) -> None:
        if contribution_id in visited:
            return
        if contribution_id in visiting:
            raise EvidenceProfileCompositionError("dependency_cycle", (contribution_id,))
        visiting.add(contribution_id)
        for dependency in by_id[contribution_id].requires_contributions:
            visit(dependency)
        visiting.remove(contribution_id)
        visited.add(contribution_id)

    for contribution_id in by_id:
        visit(contribution_id)

    # Canonical union of requirements.
    stages: dict[tuple[str, str | None], RequiredEvidenceStage] = {}
    records: dict[str, RequiredEvidenceRecord] = {}
    run_fields: set[str] = set()
    requires_terminal = False
    conflicts: list[str] = []
    for contribution in by_id.values():
        requires_terminal = requires_terminal or contribution.requires_terminal_event
        for stage in contribution.stages:
            key = (stage.stage_type, stage.provider)
            existing = stages.get(key)
            if existing is not None and existing != stage:
                conflicts.append(
                    f"{contribution.contribution_id}:{stage.stage_type} conflicts with existing"
                )
            stages[key] = stage
        for record in contribution.required_records:
            existing = records.get(record.family)
            if existing is not None and existing != record:
                conflicts.append(f"{contribution.contribution_id}:{record.family} conflicts")
            records[record.family] = record
        run_fields.update(contribution.required_run_fields)

    if conflicts:
        raise EvidenceProfileCompositionError("conflicting_requirements", conflicts)

    profile = RequiredEvidenceProfile(
        profile_id=profile_id,
        version=profile_version,
        stages=tuple(sorted(stages.values(), key=lambda s: (s.stage_type, s.provider or ""))),
        required_run_fields=tuple(sorted(run_fields)),
        required_records=tuple(sorted(records.values(), key=lambda r: r.family)),
        run_terminal_event_types=("run.terminal",) if requires_terminal else (),
    )
    return ComposedEvidenceProfile(
        profile=profile,
        required_contribution_ids=required,
        discovered_contribution_ids=discovered,
        missing_contribution_ids=(),
        digest=_digest(profile, required, discovered),
    )


def resolve_composed_evidence_profile(
    profile_id: str,
    profile_version: str,
    required_contribution_ids: Iterable[str],
) -> ComposedEvidenceProfile:
    """Resolve a composed profile from the capability registry.

    Reports an unavailable profile (never an empty healthy one) when a
    required selection has no contributions.
    """
    from phlo.capabilities import list_capabilities, resolve_capability
    from phlo.capabilities.discovery import discover_capabilities

    discover_capabilities()
    contributions: list[EvidenceProfileContribution] = []
    for contribution_id in list_capabilities("evidence_profile_contribution") or []:
        resolution = resolve_capability("evidence_profile_contribution", contribution_id)
        if resolution is None:
            continue
        provider = resolution.provider
        if isinstance(provider, EvidenceProfileContribution):
            contributions.append(provider)
        elif hasattr(provider, "contribution"):
            contribution = provider.contribution
            if isinstance(contribution, EvidenceProfileContribution):
                contributions.append(contribution)
    return compose_evidence_profile(
        profile_id, profile_version, required_contribution_ids, contributions
    )
