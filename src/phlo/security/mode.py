"""Regulated mode detection.

Checks the PHLO_REGULATED environment variable and optional config file
setting to determine whether regulated mode is active.
"""

from __future__ import annotations

from phlo.config.process import get_process_settings

PHLO_REGULATED_ENV = "PHLO_REGULATED"
_REMOVED_REGULATED_MODE_ENV = "PHLO_REGULATED_MODE"

PHLO_ENVIRONMENT_ENV = "PHLO_ENVIRONMENT"
# Environments that require HTTP authorization independently of regulated
# mode (ADR 0047 decision 2). Development, test, blank, and absent values
# stay opt-in.
_PRODUCTION_HTTP_ENVIRONMENTS = frozenset({"prod", "production", "staging", "regulated"})


def requires_http_authorization() -> bool:
    """Return whether production HTTP authorization is required.

    True for ``PHLO_ENVIRONMENT`` values ``prod``, ``production``,
    ``staging``, or ``regulated``, and whenever regulated mode is active.
    Development, test, blank, and absent values are false.

    This is the production access-control predicate and is deliberately
    separate from :func:`is_regulated`: production must fail closed without
    claiming every regulated-compliance feature.
    """
    if is_regulated():
        return True
    environment = get_process_settings().phlo_environment
    return environment is not None and environment.strip().lower() in _PRODUCTION_HTTP_ENVIRONMENTS


def is_regulated(config_regulated: bool | None = None) -> bool:
    """Return whether regulated mode is enabled, resolving in precedence order:
    PHLO_REGULATED env var, then config_regulated from phlo.yaml, then the
    config file default, otherwise False. Raises when the removed
    PHLO_REGULATED_MODE variable would otherwise have decided the result."""
    env_value = get_process_settings().phlo_regulated
    if env_value is not None:
        return env_value

    if config_regulated is not None:
        return config_regulated

    # Fail closed: a stale removed variable must not silently disable regulated mode.
    if (get_process_settings().phlo_regulated_mode or "").strip():
        raise RuntimeError(
            f"{_REMOVED_REGULATED_MODE_ENV} was removed in 0.19.0; set {PHLO_REGULATED_ENV} instead"
        )

    from phlo.config.project import get_regulated_config

    configured_value = get_regulated_config()
    if configured_value is not None:
        return configured_value

    return False
