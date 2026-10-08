"""Negotiate optional write policies without changing legacy provider calls."""

from inspect import signature
from typing import Any

from phlo.exceptions import PhloConfigError


def schema_policy_kwargs(
    provider: Any,
    policy: str,
    *,
    methods: tuple[str, ...],
    legacy_default: str = "strict",
) -> dict[str, Any]:
    """Validate policy support and selected signatures before any write.

    A non-opted-in provider receives no extra keyword for its caller's existing
    default. Explicit alternatives fail rather than being silently ignored.
    ``SchemaPolicyTableStore`` describes the opt-in contract; signature binding
    also detects advertisers that satisfy runtime protocol attribute checks but
    cannot accept the keyword.
    """
    if policy not in {"strict", "additive", "drop_extra"}:
        raise PhloConfigError(message=f"Unknown schema policy: {policy!r}")
    policies = getattr(getattr(provider, "support", None), "schema_policies", frozenset())
    if not policies:
        if policy != legacy_default:
            raise PhloConfigError(
                message="Active table store does not support explicit schema policies"
            )
        return {}
    if policy not in policies:
        raise PhloConfigError(message=f"Table store does not support {policy!r}")
    for name in methods:
        try:
            signature(getattr(provider, name)).bind_partial(schema_policy=policy)
        except (AttributeError, TypeError, ValueError) as exc:
            raise PhloConfigError(
                message=f"Table store advertises schema policies but {name} cannot accept schema_policy"
            ) from exc
    return {"schema_policy": policy}
