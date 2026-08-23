"""Publish target helper utilities.

Routes publishing through the resolved publish-target capability so core
never imports a provider directly. Governance readiness is checked before
publishing and can block with PhloConfigError; providers lacking an
operation raise rather than being skipped.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from phlo.capabilities import resolve_capability
from phlo.exceptions import PhloConfigError


def resolve_publish_target(name: str | None = None) -> Any:
    """Resolve a publish target provider."""
    resolution = resolve_capability("publish_target", name)
    if resolution is None:
        raise PhloConfigError(
            message="No publish_target capability could be resolved",
            suggestions=["Install/configure a publish target such as Postgres or ClickHouse."],
        )
    return resolution.provider


def publish_table(
    table_name: str,
    *,
    target_table: str | None = None,
    target: Any = None,
    mode: str = "replace",
    require_governance: bool = False,
    **options: Any,
) -> Any:
    """Publish a lakehouse table through a publish target provider."""
    if require_governance:
        require_governance_ready(table_name)

    provider = target or resolve_publish_target()
    if hasattr(provider, "publish_table"):
        return provider.publish_table(
            table_name=table_name,
            target_table=target_table or table_name,
            mode=mode,
            **options,
        )
    raise PhloConfigError(message="Publish target provider does not expose publish_table")


def publish_many(
    tables: list[str],
    *,
    target: Any = None,
    mode: str = "replace",
    require_governance: bool = False,
) -> dict[str, Any]:
    """Publish many tables and collect per-table results."""
    provider = target or resolve_publish_target()
    results: dict[str, Any] = {}
    for table in tables:
        results[table] = publish_table(
            table,
            target=provider,
            mode=mode,
            require_governance=require_governance,
        )
    return results


def governance_publish_readiness(table_name: str) -> dict[str, Any]:
    """Return whether a table can be published under declared governance rules."""
    from phlo.governance import GovernanceWarning, build_governance_surface

    surface = build_governance_surface()
    table = surface.tables.get(table_name)
    warnings = [warning for warning in surface.warnings if warning.table == table_name]
    if table is None:
        warnings.append(
            GovernanceWarning(
                table=table_name,
                code="missing_governance_declaration",
                message=(
                    f"{table_name} has no Phlo governance declaration. Add @phlo.contract, "
                    "@phlo.publish, and @phlo.access declarations before requiring governance."
                ),
            )
        )

    return {
        "ready": not warnings,
        "table": table_name,
        "governance": table.to_read_model() if table is not None else None,
        "warning_count": len(warnings),
        "warnings": [warning.to_read_model() for warning in warnings],
    }


def require_governance_ready(table_name: str) -> dict[str, Any]:
    """Raise if a table has governance warnings that should block publishing."""
    report = governance_publish_readiness(table_name)
    if report["ready"]:
        return report

    warnings = report["warnings"]
    warning_codes = ", ".join(str(warning["code"]) for warning in warnings)
    warning_messages = [str(warning["message"]) for warning in warnings]
    raise PhloConfigError(
        message=f"{table_name} is not governance-ready for publishing: {warning_codes}",
        suggestions=[
            *warning_messages,
            "Run `phlo governance check --json` to inspect every declared table.",
        ],
    )


def create_api_view(name: str, sql: str, *, target: Any = None) -> Any:
    """Create or update an API-ready view when the target supports it."""
    provider = target or resolve_publish_target()
    if hasattr(provider, "create_api_view"):
        return provider.create_api_view(name=name, sql=sql)
    raise PhloConfigError(message="Publish target provider does not expose create_api_view")


def _check_passed(check: Mapping[str, Any]) -> bool:
    return bool(check.get("passed", check.get("status") in {"passed", "success", "ok"}))


def publish_eligibility_report(
    *,
    checks: list[Mapping[str, Any]] | None = None,
    required_states: list[str] | tuple[str, ...] = (),
    current_state: str | None = None,
    policy_results: list[Mapping[str, Any]] | None = None,
    reference_reports: list[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Summarize whether a workflow output is eligible to publish."""
    check_list = list(checks or [])
    policy_list = list(policy_results or [])
    reference_list = list(reference_reports or [])

    failed_checks = [check for check in check_list if not _check_passed(check)]
    failed_policies = [policy for policy in policy_list if not _check_passed(policy)]
    reference_gaps = [
        report for report in reference_list if int(report.get("missing_key_count", 0) or 0) > 0
    ]
    state_allowed = not required_states or current_state in set(required_states)

    eligible = not failed_checks and not failed_policies and not reference_gaps and state_allowed
    return {
        "eligible": eligible,
        "current_state": current_state,
        "required_states": list(required_states),
        "state_allowed": state_allowed,
        "check_count": len(check_list),
        "failed_check_count": len(failed_checks),
        "failed_checks": failed_checks,
        "policy_count": len(policy_list),
        "failed_policy_count": len(failed_policies),
        "failed_policies": failed_policies,
        "reference_report_count": len(reference_list),
        "reference_gap_count": len(reference_gaps),
        "reference_gaps": reference_gaps,
    }
