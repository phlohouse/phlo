"""Canonical Dataset projection runtime shared by CLI, Governance, and API.

Every non-UI surface returns the same Dataset facts by consuming one projection built here, over
the neutral :mod:`phlo.dataset` core. The module binds a project to its
capabilities -- the durable state store, the governance-surface declarations,
and every registered ``dataset_evidence`` provider -- and exposes one
:class:`DatasetAuthority` whose ``projection`` read model and CAS transitions
are the only facts CLI, Governance, and phlo-api return.

No surface re-derives identity, controls, readiness, or publication state:
surfaces call :meth:`DatasetAuthority.projection` and render. Provider
evidence crosses only the neutral ``dataset_evidence`` capability interface;
the blessed run-evidence profile (``"blessed"``) is the named first
contributor, and any other profile maps through its provider's declared
compatibility mapping before it is served here.
"""

from __future__ import annotations

from collections.abc import Collection
from typing import Any

from phlo.dataset.evidence import EvidenceRecord
from phlo.dataset.models import (
    WORKFLOW_STATE_OPEN,
    DatasetStateRecord,
    TransitionOutcome,
    TransitionRequest,
    dataset_table_id,
    is_candidate_dataset_id,
)
from phlo.dataset.policy import (
    DatasetPolicy,
    EvidenceCondition,
    PolicyRule,
    PolicyVerdict,
    TransitionPolicy,
)
from phlo.dataset.service import DatasetService, StaticPolicySource
from phlo.dataset.store import DatasetStateStore
from phlo.dataset_state import resolve_dataset_state_store
from phlo.governance.surface import GovernanceSurface, GovernedTable, build_governance_surface

BLESSED_EVIDENCE_PROFILE = "blessed"
"""Named first contributor for run-evidence profiles."""

DEFAULT_POLICY_VERSION = "governance-surface-v1"
"""Policy version stamped on records transitioned under the default policy."""

EVIDENCE_KIND_DECLARATIONS = "governance_surface"
EVIDENCE_KIND_OWNERSHIP = "ownership"
EVIDENCE_KIND_CLASSIFICATION = "classification"
EVIDENCE_KIND_QUALITY = "quality_checks"

DECLARATION_EVIDENCE_KINDS = (
    EVIDENCE_KIND_CLASSIFICATION,
    EVIDENCE_KIND_DECLARATIONS,
    EVIDENCE_KIND_OWNERSHIP,
)

CONTROL_DECLARATIONS = "governance_declarations_present"
CONTROL_OWNER = "owner_recorded"
CONTROL_CLASSIFICATION = "classification_declared"
CONTROL_QUALITY = "quality_checks_passed"

_TRANSITION_BLOCKING_ACTIONS = frozenset({"promote", "publish"})


def default_project_policy() -> DatasetPolicy:
    """Return the default project policy derived from Phlo declarations.

    The controls require declarations present
    (``@phlo.contract``/``@phlo.publish``/``@phlo.access``), a recorded owner,
    a declared classification, and passing quality checks -- as blocker rules
    over the evidence kinds the governance surface and ``dataset_evidence``
    providers serve. ``claim``/``review``/``retire`` carry no controls, so a
    Dataset can always be retired.
    """
    return DatasetPolicy(
        policy_version=DEFAULT_POLICY_VERSION,
        rules=(
            PolicyRule(
                control=CONTROL_DECLARATIONS,
                evidence_kind=EVIDENCE_KIND_DECLARATIONS,
                condition=EvidenceCondition(field="declared", operator="true"),
                severity="blocker",
                message="The table must carry @phlo.contract, @phlo.publish, and @phlo.access.",
                applies_to=_TRANSITION_BLOCKING_ACTIONS,
            ),
            PolicyRule(
                control=CONTROL_OWNER,
                evidence_kind=EVIDENCE_KIND_OWNERSHIP,
                condition=EvidenceCondition(field="owner", operator="ne", value=None),
                severity="blocker",
                message="Dataset has no recorded owner.",
                applies_to=_TRANSITION_BLOCKING_ACTIONS,
            ),
            PolicyRule(
                control=CONTROL_CLASSIFICATION,
                evidence_kind=EVIDENCE_KIND_CLASSIFICATION,
                condition=EvidenceCondition(field="declared", operator="true"),
                severity="blocker",
                message="Dataset has no declared classification.",
                applies_to=_TRANSITION_BLOCKING_ACTIONS,
            ),
            PolicyRule(
                control=CONTROL_QUALITY,
                evidence_kind=EVIDENCE_KIND_QUALITY,
                condition=EvidenceCondition(field="passed", operator="true"),
                severity="blocker",
                message="Blocking quality checks must pass before the transition.",
                applies_to=_TRANSITION_BLOCKING_ACTIONS,
            ),
        ),
        transitions=(
            TransitionPolicy(
                action="promote",
                required_evidence=(*DECLARATION_EVIDENCE_KINDS, EVIDENCE_KIND_QUALITY),
            ),
            TransitionPolicy(
                action="publish",
                required_evidence=(*DECLARATION_EVIDENCE_KINDS, EVIDENCE_KIND_QUALITY),
            ),
        ),
    )


class GovernanceSurfaceEvidenceSource:
    """Evidence source over the governance surface (core, provider-free).

    Serves declaration, ownership, and classification evidence for one table.
    Ownership and classification records are always present (carrying their
    value) so a missing owner or classification is a *failed* control rather
    than missing evidence; quality evidence is left to executors and never
    invented here.
    """

    def __init__(self, surface: GovernanceSurface) -> None:
        self._surface = surface

    def table(self, table_id: str) -> GovernedTable | None:
        """Return the governed surface row for one table key, or None."""
        return self._surface.tables.get(table_id)

    def evidence(self, subject: str, kinds: Collection[str]) -> tuple[EvidenceRecord, ...]:
        if EVIDENCE_KIND_QUALITY in kinds:
            # Quality results are executor-produced; the surface never claims them.
            kinds = [kind for kind in kinds if kind != EVIDENCE_KIND_QUALITY]
        table = self.table(subject)
        if table is None or not kinds:
            return ()
        records: list[EvidenceRecord] = []
        for kind in kinds:
            if kind == EVIDENCE_KIND_DECLARATIONS:
                records.append(
                    EvidenceRecord(
                        kind=kind,
                        subject=subject,
                        payload={"declared": True, "published": table.published},
                        source="governance surface",
                    )
                )
            elif kind == EVIDENCE_KIND_OWNERSHIP:
                records.append(
                    EvidenceRecord(
                        kind=kind,
                        subject=subject,
                        payload={"owner": table.owner},
                        source="governance surface",
                    )
                )
            elif kind == EVIDENCE_KIND_CLASSIFICATION:
                records.append(
                    EvidenceRecord(
                        kind=kind,
                        subject=subject,
                        payload={
                            "declared": bool(table.classifications),
                            "classifications": list(table.classifications),
                        },
                        source="governance surface",
                    )
                )
        return tuple(records)


class CapabilityEvidenceSource:
    """Evidence source merging every registered ``dataset_evidence`` provider.

    Providers cross the neutral interface only: each registered spec's
    provider must expose ``evidence(subject, kinds)``. A provider that
    returns nothing for a kind is treated the same as missing evidence.
    """

    def evidence(self, subject: str, kinds: Collection[str]) -> tuple[EvidenceRecord, ...]:
        from phlo.capabilities import get_capability_registry

        records: list[EvidenceRecord] = []
        for spec in get_capability_registry().list("dataset_evidence"):
            provider = spec.provider
            evidence = provider.evidence(subject, kinds)
            records.extend(evidence)
        return tuple(records)


class CompositeEvidenceSource:
    """Evidence source concatenating several sources in fixed order."""

    def __init__(self, *sources: Any) -> None:
        self._sources = sources

    def evidence(self, subject: str, kinds: Collection[str]) -> tuple[EvidenceRecord, ...]:
        records: list[EvidenceRecord] = []
        for source in self._sources:
            records.extend(source.evidence(subject, kinds))
        return tuple(records)


class DatasetAuthority:
    """One authority per project: canonical projection, readiness, transitions.

    Wraps the neutral :class:`phlo.dataset.service.DatasetService` bound to
    the project's durable store, governance-surface evidence, and project
    policy. All surfaces share one instance shape; nothing here is
    surface-specific.
    """

    def __init__(
        self,
        *,
        service: DatasetService,
        surface: GovernanceSurface,
    ) -> None:
        self._service = service
        self._surface = surface

    @property
    def service(self) -> DatasetService:
        """The neutral core transition service."""
        return self._service

    @property
    def surface(self) -> GovernanceSurface:
        """The project governance surface feeding declaration evidence."""
        return self._surface

    def governed_table(self, table_id: str) -> GovernedTable | None:
        """Return the governed surface row for one table key, or None."""
        return self._surface.tables.get(table_id)

    def record(self, dataset_id: str) -> DatasetStateRecord | None:
        """Return the durable record for one canonical Dataset ID."""
        return self._service.record(dataset_id)

    def readiness(self, dataset_id: str, action: str | None = None) -> PolicyVerdict:
        """Evaluate the project policy for one Dataset's transition."""
        return self._service.readiness(dataset_id, action)

    def allowed_transitions(self, dataset_id: str) -> tuple[str, ...]:
        """Return the state-machine actions available from the current state."""
        return self._service.allowed_transitions(dataset_id)

    def transition(self, request: TransitionRequest) -> TransitionOutcome:
        """Apply one authorized compare-and-set transition through core."""
        return self._service.transition(request)

    def workflow_config(self) -> dict[str, Any]:
        """Return the workflow configuration held by the durable store."""
        getter = getattr(self._service.store, "workflow_config", None)
        if not callable(getter):
            return {}
        return dict(getter())

    def write_workflow_config(
        self,
        config: dict[str, Any],
        *,
        actor: str | None = None,
        scope: str | None = None,
    ) -> None:
        """Persist the workflow configuration through the durable store."""
        writer = getattr(self._service.store, "write_workflow_config", None)
        if not callable(writer):
            raise RuntimeError(
                "The resolved dataset state store does not support workflow configuration writes."
            )
        writer(config=config, actor=actor, scope=scope)

    def projection(self, dataset_id: str, action: str | None = None) -> dict[str, Any]:
        """Return the canonical Dataset projection.

        This dict is the single source every surface serializes: CLI
        ``phlo dataset show --json`` emits it verbatim, the Observatory
        Dataset Profile embeds it as ``canonical``, and Governance rows
        render its controls and readiness. Field order is stable and the
        readiness reasons keep the evaluator's deterministic order.
        """
        is_candidate = is_candidate_dataset_id(dataset_id)
        table_id = dataset_table_id(dataset_id)
        record = self._service.record(dataset_id)
        verdict = self._service.readiness(dataset_id, action)
        table = self.governed_table(table_id)
        evidence = self._service_evidence(dataset_id, verdict)
        owner = _first_non_none(
            record.owner if record else None,
            table.owner if table else None,
        )
        workflow_state = record.current_state if record else None
        if is_candidate and workflow_state is None:
            workflow_state = WORKFLOW_STATE_OPEN
        return {
            "dataset_id": dataset_id,
            "table_id": table_id,
            "candidate": is_candidate,
            "owner": owner,
            "classifications": list(table.classifications) if table else [],
            "workflow_state": workflow_state,
            "publication_state": (
                record.publication_state if record is not None and not is_candidate else None
            ),
            "approval_state": record.approval_state if record else None,
            "policy_version": record.policy_version if record else None,
            "last_action_id": record.last_action_id if record else None,
            "declared": table is not None,
            "controls": [dict(control) for control in verdict.controls],
            "evidence": [
                {
                    "kind": item.kind,
                    "subject": item.subject,
                    "status": item.status,
                    "source": item.source,
                }
                for item in evidence
            ],
            "readiness": {
                "action": verdict.action,
                "ready": verdict.ready,
                "policy_version": verdict.policy_version,
                "reasons": list(verdict.reasons),
                "blockers": [finding.to_read_model() for finding in verdict.blockers],
                "warnings": [finding.to_read_model() for finding in verdict.warnings],
                "missing_evidence": [
                    missing.to_read_model() for missing in verdict.missing_evidence
                ],
            },
            "allowed_transitions": list(self._service.allowed_transitions(dataset_id)),
            "record": record.to_read_model() if record else None,
        }

    def _service_evidence(
        self, dataset_id: str, verdict: PolicyVerdict
    ) -> tuple[EvidenceRecord, ...]:
        # Re-serve the evidence the verdict consumed so projections can show
        # controls next to the evidence behind them.
        service = self._service
        kinds = service.required_evidence_kinds(dataset_id, verdict.action)
        return service.evidence(dataset_id, kinds)


def build_dataset_authority(
    project_root: str | None = None,
    *,
    store: DatasetStateStore | None = None,
    store_mode: str | None = None,
    evidence_source: Any | None = None,
    policy: DatasetPolicy | None = None,
    surface: GovernanceSurface | None = None,
) -> DatasetAuthority:
    """Bind one project to its canonical Dataset authority.

    Resolves the durable state store (durable by default, ``memory`` under
    ``PHLO_DATASET_STATE_STORE=memory`` or an explicit ``store_mode``), the
    governance surface, and the evidence source -- governance-surface
    declarations plus every registered ``dataset_evidence`` provider unless
    the caller supplies one.
    """
    resolved_surface = surface if surface is not None else build_governance_surface()
    if store is None:
        store = resolve_dataset_state_store(project_root or ".", mode=store_mode)
    if evidence_source is None:
        evidence_source = CompositeEvidenceSource(
            GovernanceSurfaceEvidenceSource(resolved_surface),
            CapabilityEvidenceSource(),
        )
    policy_source = StaticPolicySource(policy or default_project_policy())
    service = DatasetService(
        store=store,
        evidence_source=evidence_source,
        policy_source=policy_source,
    )
    return DatasetAuthority(service=service, surface=resolved_surface)


def _first_non_none(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


__all__ = [
    "BLESSED_EVIDENCE_PROFILE",
    "CONTROL_CLASSIFICATION",
    "CONTROL_DECLARATIONS",
    "CONTROL_OWNER",
    "CONTROL_QUALITY",
    "DEFAULT_POLICY_VERSION",
    "EVIDENCE_KIND_CLASSIFICATION",
    "EVIDENCE_KIND_DECLARATIONS",
    "EVIDENCE_KIND_OWNERSHIP",
    "EVIDENCE_KIND_QUALITY",
    "CapabilityEvidenceSource",
    "CompositeEvidenceSource",
    "DatasetAuthority",
    "GovernanceSurfaceEvidenceSource",
    "build_dataset_authority",
    "default_project_policy",
]
