"""Neutral provider workload identity matrix (ADR 0047 §7.1).

Names the distinct production workload identities and the credential
references that supply them, and evaluates those references locally: are they
present, non-default/non-root, and distinct across workloads? Grant and audit
observations are Plan 005's job; this module only checks the reference-level
facts that are locally inspectable from configuration.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class WorkloadIdentitySpec:
    """One required workload identity and its credential references."""

    name: str
    credential_refs: tuple[str, ...]


# The blessed workload identities and the non-secret environment references
# that supply each one's credentials. References, never values, live here.
WORKLOAD_IDENTITY_SPECS: tuple[WorkloadIdentitySpec, ...] = (
    WorkloadIdentitySpec(
        "api",
        ("PHLO_SERVICE_CREDENTIALS_FILE",),
    ),
    WorkloadIdentitySpec(
        "orchestration",
        (
            "DAGSTER_MINIO_ACCESS_KEY",
            "DAGSTER_MINIO_SECRET_KEY",
            "DAGSTER_TRINO_USER",
            "DAGSTER_POSTGRES_USER",
            "DAGSTER_POSTGRES_PASSWORD",
        ),
    ),
    WorkloadIdentitySpec(
        "query",
        (
            "TRINO_QUERY_ACCESS_KEY",
            "TRINO_QUERY_SECRET_KEY",
            "TRINO_USER",
            "TRINO_ROLE",
        ),
    ),
    WorkloadIdentitySpec(
        "catalog",
        (
            "NESSIE_CATALOG_ACCESS_KEY",
            "NESSIE_CATALOG_SECRET_KEY",
            "QUARKUS_DATASOURCE_USERNAME",
            "QUARKUS_DATASOURCE_PASSWORD",
        ),
    ),
    WorkloadIdentitySpec(
        "maintenance",
        (
            "MAINTENANCE_TRINO_USER",
            "MAINTENANCE_TRINO_ROLE",
            "MAINTENANCE_ACCESS_KEY",
            "MAINTENANCE_SECRET_KEY",
        ),
    ),
)

# Values that are never acceptable for a workload credential reference.
_INSECURE_VALUES = frozenset(
    {"", "root", "admin", "minio", "phlo", "minio123", "password", "changeme", "secret"}
)


@dataclass(frozen=True, slots=True)
class WorkloadIdentityEvaluation:
    """Reference-level evaluation result for one workload identity."""

    name: str
    passed: bool
    missing: tuple[str, ...] = ()
    insecure_default: tuple[str, ...] = ()
    shared_with: tuple[tuple[str, str], ...] = ()
    references_observed: bool = False

    def message(self) -> str:
        if self.passed:
            return f"{self.name} workload identity references are distinct and non-default"
        reasons: list[str] = []
        if self.missing:
            reasons.append(f"missing: {', '.join(sorted(self.missing))}")
        if self.insecure_default:
            reasons.append(f"default/root: {', '.join(sorted(self.insecure_default))}")
        if self.shared_with:
            pairs = ", ".join(f"{ref}~{other}" for ref, other in sorted(self.shared_with))
            reasons.append(f"shared across workloads: {pairs}")
        return f"{self.name} workload identity: " + "; ".join(reasons)

    def remediation(self) -> str:
        return (
            "Give each workload its own non-default credential reference and receiver role "
            "before claiming production readiness."
        )


def evaluate_workload_identity_references(
    env: Mapping[str, str],
) -> tuple[WorkloadIdentityEvaluation, ...]:
    """Evaluate every workload identity's references from effective environment.

    A reference value shared by two identities is ``shared_with``; an empty,
    default, or root-like value is ``insecure_default``; an absent value is
    ``missing``. Only the reference-level facts are checked here — grants,
    audit, and drift are Plan 005 observations.
    """
    values: dict[str, str] = {
        ref: env.get(ref, "").strip()
        for spec in WORKLOAD_IDENTITY_SPECS
        for ref in spec.credential_refs
    }
    evaluations: list[WorkloadIdentityEvaluation] = []
    for spec in WORKLOAD_IDENTITY_SPECS:
        missing: list[str] = []
        insecure: list[str] = []
        shared: list[tuple[str, str]] = []
        observed = False
        for ref in spec.credential_refs:
            value = values[ref]
            if not value:
                missing.append(ref)
                continue
            observed = True
            if value.lower() in _INSECURE_VALUES:
                insecure.append(ref)
            for other_spec in WORKLOAD_IDENTITY_SPECS:
                if other_spec is spec:
                    continue
                for other_ref in other_spec.credential_refs:
                    if values.get(other_ref) == value:
                        shared.append((ref, other_ref))
        passed = not missing and not insecure and not shared
        evaluations.append(
            WorkloadIdentityEvaluation(
                name=spec.name,
                passed=passed,
                missing=tuple(missing),
                insecure_default=tuple(insecure),
                shared_with=tuple(shared),
                references_observed=observed,
            )
        )
    return tuple(evaluations)
