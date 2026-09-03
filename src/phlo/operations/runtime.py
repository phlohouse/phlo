"""Deployment identity and version inventory for continuity operations.

``get_deployment_id`` provides the stable per-deployment identity recorded in
backup-set manifests (ADR 0049 §3); ``get_package_versions`` records the
version inventory so a set can be judged compatible before restore.
"""

from __future__ import annotations

import importlib.metadata
import os
import socket

_VERSIONED_PACKAGES = ("phlo", "phlo-postgres", "phlo-minio", "phlo-nessie", "phlo-iceberg")


def get_deployment_id() -> str:
    """Return the stable deployment identity for this Phlo instance.

    Uses ``PHLO_DEPLOYMENT_ID`` when set (compose sets it per project);
    otherwise falls back to a hostname-derived identity.
    """
    configured = os.environ.get("PHLO_DEPLOYMENT_ID", "").strip()
    if configured:
        return configured
    host = socket.gethostname().strip() or "unknown-host"
    project = os.environ.get("PHLO_PROJECT_NAME", "").strip()
    return f"{project}@{host}" if project else host


def get_package_versions() -> dict[str, str]:
    """Return the installed version inventory recorded in backup manifests."""
    versions: dict[str, str] = {}
    for package in _VERSIONED_PACKAGES:
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            continue
    versions.setdefault("backup_set_schema", "1")
    return versions


__all__ = ["get_deployment_id", "get_package_versions"]
