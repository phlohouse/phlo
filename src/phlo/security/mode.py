"""Regulated mode detection.

Checks the PHLO_REGULATED environment variable and optional config file
setting to determine whether regulated mode is active.
"""

from __future__ import annotations

import os

from phlo.logging import get_logger

logger = get_logger(__name__)

PHLO_REGULATED_ENV = "PHLO_REGULATED"
_PHLO_REGULATED_MODE_ENV_DEPRECATED = "PHLO_REGULATED_MODE"


def is_regulated(config_regulated: bool | None = None) -> bool:
    """Return whether regulated mode is enabled, resolving in precedence order:
    PHLO_REGULATED env var, then config_regulated from phlo.yaml, then the
    deprecated PHLO_REGULATED_MODE env var, then the config file default,
    otherwise False."""
    env_value = os.environ.get(PHLO_REGULATED_ENV, "").strip().lower()
    if env_value in ("1", "true", "yes", "on"):
        return True
    if env_value in ("0", "false", "no", "off"):
        return False

    if config_regulated is not None:
        return config_regulated

    deprecated_env_value = os.environ.get(_PHLO_REGULATED_MODE_ENV_DEPRECATED, "").strip().lower()
    if deprecated_env_value:
        logger.warning(
            "deprecated_env_var",
            old=_PHLO_REGULATED_MODE_ENV_DEPRECATED,
            new=PHLO_REGULATED_ENV,
            message=f"{_PHLO_REGULATED_MODE_ENV_DEPRECATED} is deprecated, use {PHLO_REGULATED_ENV} instead",
        )
        if deprecated_env_value in ("1", "true", "yes", "on"):
            return True
        if deprecated_env_value in ("0", "false", "no", "off"):
            return False

    from phlo.infrastructure.config import get_regulated_config

    configured_value = get_regulated_config()
    if configured_value is not None:
        return configured_value

    return False


def is_regulated_mode_enabled(config_regulated_mode: bool | None = None) -> bool:
    """Deprecated: use is_regulated() instead."""
    import warnings

    warnings.warn(
        "is_regulated_mode_enabled() is deprecated, use is_regulated() instead",
        DeprecationWarning,
        stacklevel=2,
    )
    return is_regulated(config_regulated_mode)


PHLO_REGULATED_MODE_ENV = PHLO_REGULATED_ENV  # deprecated alias
