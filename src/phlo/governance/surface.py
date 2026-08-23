"""Derived governance surface from existing Phlo declarations.

Folds every registered flow, contract, access-policy, and observability
declaration into one immutable GovernanceSurface of GovernedTable entries,
emitting GovernanceWarnings for inconsistencies instead of failing the
build. Merged values are deep-copied into JSON-serializable form.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from phlo.flow import (
    AccessPolicySpec,
    ContractSpec,
    get_access_policies,
    get_contract_specs,
    get_observe_assets,
    get_publish_assets,
)


@dataclass(frozen=True, slots=True)
class GovernanceWarning:
    """Validation warning raised while deriving the governance surface."""

    table: str
    code: str
    message: str
    severity: str = "error"

    def to_read_model(self) -> dict[str, str]:
        return {
            "table": self.table,
            "code": self.code,
            "message": self.message,
            "severity": self.severity,
        }


@dataclass(frozen=True, slots=True)
class AccessPolicyReadModel:
    """Serialized access policy attached to a governed table."""

    key: str
    roles: tuple[str, ...]

    pii_columns: tuple[str, ...]
    policy: str
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_spec(cls, spec: AccessPolicySpec) -> AccessPolicyReadModel:
        """Build a read model from an access policy specification."""
        return cls(
            key=spec.key,
            roles=tuple(spec.roles),
            pii_columns=tuple(spec.pii_columns),
            policy=spec.policy,
            metadata=dict(spec.metadata),
        )

    def to_read_model(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "roles": list(self.roles),
            "pii_columns": list(self.pii_columns),
            "policy": self.policy,
            "metadata": _copy_json_like(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class GovernanceObservability:
    """Observability signals collected for a governed table."""

    freshness_hours: int | None = None
    row_count_change: dict[str, float] = field(default_factory=dict)
    checks: tuple[str, ...] = ()

    def to_read_model(self) -> dict[str, Any]:
        return {
            "freshness_hours": self.freshness_hours,
            "row_count_change": dict(self.row_count_change),
            "checks": list(self.checks),
        }


@dataclass(frozen=True, slots=True)
class GovernedTable:
    """Governance metadata for a single table."""

    table: str
    owner: str | None = None
    lifecycle: str | None = None
    pii: bool = False
    published: bool = False
    audience: tuple[str, ...] = ()
    consumers: tuple[dict[str, Any], ...] = ()
    sla: dict[str, Any] | None = None
    access_policies: tuple[AccessPolicyReadModel, ...] = ()
    observability: GovernanceObservability = field(default_factory=GovernanceObservability)
    warnings: tuple[GovernanceWarning, ...] = ()

    def to_read_model(self) -> dict[str, Any]:
        return {
            "table": self.table,
            "owner": self.owner,
            "lifecycle": self.lifecycle,
            "pii": self.pii,
            "published": self.published,
            "audience": list(self.audience),
            "consumers": [_copy_json_like(consumer) for consumer in self.consumers],
            "sla": _copy_json_like(self.sla),
            "access_policies": [policy.to_read_model() for policy in self.access_policies],
            "observability": self.observability.to_read_model(),
            "warnings": [warning.to_read_model() for warning in self.warnings],
        }


@dataclass(frozen=True, slots=True)
class GovernanceSurface:
    """Aggregated governance view over all registered table declarations."""

    tables: dict[str, GovernedTable]
    warnings: tuple[GovernanceWarning, ...] = ()

    @property
    def warning_count(self) -> int:
        return len(self.warnings)

    def table(self, table_name: str) -> GovernedTable:
        """Return the governed table registered under ``table_name``."""
        return self.tables[table_name]

    def to_read_model(self) -> dict[str, Any]:
        return {
            "tables": [self.tables[table].to_read_model() for table in sorted(self.tables)],
            "warnings": [warning.to_read_model() for warning in self.warnings],
            "warning_count": self.warning_count,
        }

    def to_check_result(self) -> dict[str, Any]:
        """Return a pass/fail summary suitable for governance checks."""
        return {
            "ok": self.warning_count == 0,
            "warning_count": self.warning_count,
            "warnings": [warning.to_read_model() for warning in self.warnings],
        }


@dataclass(slots=True)
class _TableBuilder:
    table: str
    owner: str | None = None
    lifecycle: str | None = None
    pii: bool = False
    published: bool = False
    audience: list[str] = field(default_factory=list)
    consumers: list[dict[str, Any]] = field(default_factory=list)
    sla: dict[str, Any] | None = None
    access_policies: list[AccessPolicyReadModel] = field(default_factory=list)
    observability: GovernanceObservability = field(default_factory=GovernanceObservability)
    declared: bool = False

    def apply_contract(self, spec: ContractSpec) -> None:
        self.declared = True
        self.owner = self.owner or spec.owner
        self.lifecycle = self.lifecycle or spec.lifecycle
        self.pii = self.pii or spec.pii
        self.consumers = _merge_dict_lists(self.consumers, spec.consumers, key="name")
        self.sla = self.sla or _copy_json_like(spec.sla)

    def apply_publish(self, metadata: dict[str, Any]) -> None:
        self.declared = True
        self.published = True
        owner = metadata.get("owner")
        if isinstance(owner, str) and owner:
            self.owner = self.owner or owner
        audience = metadata.get("audience")
        if isinstance(audience, list):
            self.audience = _merge_strings(self.audience, [str(item) for item in audience])
        consumers = metadata.get("consumers")
        if isinstance(consumers, list):
            self.consumers = _merge_dict_lists(self.consumers, consumers, key="name")
        sla = metadata.get("sla")
        if isinstance(sla, dict):
            self.sla = self.sla or _copy_json_like(sla)
        freshness_hours = metadata.get("freshness_hours")
        if self.sla is None and isinstance(freshness_hours, int):
            self.sla = {"freshness_hours": freshness_hours}

    def apply_observe(self, metadata: dict[str, Any], checks: list[str]) -> None:
        self.declared = True
        freshness_hours = metadata.get("freshness_hours")
        row_count_change = metadata.get("row_count_change")
        merged_row_count_change = dict(self.observability.row_count_change)
        if isinstance(row_count_change, dict):
            merged_row_count_change.update(row_count_change)
        self.observability = GovernanceObservability(
            freshness_hours=(
                freshness_hours
                if isinstance(freshness_hours, int)
                else self.observability.freshness_hours
            ),
            row_count_change=merged_row_count_change,
            checks=(*self.observability.checks, *checks),
        )

    def apply_access(self, spec: AccessPolicySpec) -> None:
        self.access_policies.append(AccessPolicyReadModel.from_spec(spec))

    def build(self, warnings: list[GovernanceWarning]) -> GovernedTable:
        own_warnings = tuple(warning for warning in warnings if warning.table == self.table)
        return GovernedTable(
            table=self.table,
            owner=self.owner,
            lifecycle=self.lifecycle,
            pii=self.pii,
            published=self.published,
            audience=tuple(sorted(self.audience)),
            # Dict consumers have no natural ordering; repr provides a stable
            # total order so repeated builds yield identical output.
            consumers=tuple(sorted((_copy_json_like(item) for item in self.consumers), key=repr)),
            sla=_copy_json_like(self.sla),
            access_policies=tuple(sorted(self.access_policies, key=lambda item: item.key)),
            observability=self.observability,
            warnings=own_warnings,
        )


def build_governance_surface() -> GovernanceSurface:
    """Build the governance surface from all registered declarations.

    Sources are applied in a fixed order: contracts, publish metadata,
    observe metadata, then access policies. Scalar fields keep their first
    non-empty value, so whichever source declares a field earliest wins;
    list-valued fields accumulate with deduplication instead.
    """
    builders: dict[str, _TableBuilder] = {}

    for spec in get_contract_specs():
        _builder(builders, spec.table).apply_contract(spec)

    for asset in get_publish_assets():
        table = asset.metadata.get("table")
        if isinstance(table, str) and table:
            _builder(builders, table).apply_publish(asset.metadata)

    for asset in get_observe_assets():
        table = asset.metadata.get("table")
        if isinstance(table, str) and table:
            _builder(builders, table).apply_observe(
                asset.metadata,
                [check.name for check in asset.checks],
            )

    # Access policies may reference tables that never declare a contract,
    # publish, or observe asset. Track those before applying any policy so
    # validation can flag them: carrying a policy alone does not count as
    # declaring the table.
    access_only_tables: set[str] = set()
    for spec in get_access_policies():
        builder = _builder(builders, spec.table)
        if not builder.declared:
            access_only_tables.add(spec.table)
        builder.apply_access(spec)

    warnings = _validate_builders(builders, access_only_tables)
    tables = {table: builders[table].build(warnings) for table in sorted(builders)}
    return GovernanceSurface(tables=tables, warnings=tuple(warnings))


def _builder(builders: dict[str, _TableBuilder], table: str) -> _TableBuilder:
    if table not in builders:
        builders[table] = _TableBuilder(table=table)
    return builders[table]


def _validate_builders(
    builders: dict[str, _TableBuilder],
    access_only_tables: set[str],
) -> list[GovernanceWarning]:
    warnings: list[GovernanceWarning] = []
    for table in sorted(builders):
        builder = builders[table]
        if table in access_only_tables:
            warnings.append(
                GovernanceWarning(
                    table=table,
                    code="access_policy_without_table",
                    message=(
                        f"{table} has an access policy but no contract, publish, "
                        "or observe declaration."
                    ),
                )
            )
        if builder.published and not builder.owner:
            warnings.append(
                GovernanceWarning(
                    table=table,
                    code="missing_owner",
                    message=f"{table} is published but has no owner declaration.",
                )
            )
        if builder.published and not builder.access_policies:
            warnings.append(
                GovernanceWarning(
                    table=table,
                    code="missing_access_policy",
                    message=f"{table} is published but has no access policy.",
                )
            )
        if builder.pii and not any(policy.pii_columns for policy in builder.access_policies):
            warnings.append(
                GovernanceWarning(
                    table=table,
                    code="missing_pii_column_policy",
                    message=f"{table} declares PII but no access policy names PII columns.",
                )
            )
        if builder.lifecycle == "production" and not builder.sla:
            warnings.append(
                GovernanceWarning(
                    table=table,
                    code="missing_production_sla",
                    message=f"{table} is production but has no SLA declaration.",
                )
            )
    return warnings


def _merge_strings(current: list[str], incoming: list[str]) -> list[str]:
    result = list(current)
    for item in incoming:
        if item not in result:
            result.append(item)
    return result


def _merge_dict_lists(
    current: list[dict[str, Any]],
    incoming: list[dict[str, Any]],
    *,
    key: str,
) -> list[dict[str, Any]]:
    result = [_copy_json_like(item) for item in current]
    seen = {str(item.get(key)) for item in result}
    for item in incoming:
        item_key = str(item.get(key))
        if item_key not in seen:
            result.append(_copy_json_like(item))
            seen.add(item_key)
    return result


def _copy_json_like(value: Any) -> Any:
    """Deep-copy a JSON-like value into JSON-serializable form.

    Mapping keys become strings, sequences become lists, and sets become
    lists ordered by repr, since set elements may not define a comparison
    that sorted() can use directly.
    """
    if isinstance(value, dict):
        return {str(key): _copy_json_like(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_copy_json_like(item) for item in value]
    if isinstance(value, set):
        return sorted((_copy_json_like(item) for item in value), key=repr)
    return value
