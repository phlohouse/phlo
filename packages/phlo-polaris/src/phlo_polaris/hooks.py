"""Polaris service lifecycle hooks (invoked via ``python -m phlo_polaris.hooks``).

``bootstrap`` waits for the Polaris API, then creates the Phlo catalog, the
writer and reader principals, and their grants. It is idempotent: existing
objects are reused, never recreated or deleted.
"""

from __future__ import annotations

import argparse
import sys
import time

from phlo.logging import get_logger

logger = get_logger(__name__)

BOOTSTRAP_TIMEOUT_SECONDS = 90


def wait_for_polaris(client, *, timeout_seconds: int = BOOTSTRAP_TIMEOUT_SECONDS) -> bool:
    """Poll the Polaris health endpoint until it responds or the timeout lapses."""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if client.health_check():
            return True
        time.sleep(2)
    return False


def ensure_catalog(client, *, name: str, warehouse: str, endpoint: str | None = None) -> bool:
    """Create the Phlo catalog when absent."""
    if client.get_catalog(name) is not None:
        logger.info("polaris_bootstrap_catalog_exists", catalog=name)
        return False
    import os

    client.create_catalog(
        name=name,
        warehouse=warehouse,
        endpoint=endpoint or os.getenv("ICEBERG_S3_ENDPOINT", "http://minio:9000"),
    )
    logger.info("polaris_bootstrap_catalog_created", catalog=name)
    return True


def ensure_principal(client, *, name: str, credentials: dict[str, str] | None = None) -> bool:
    """Create one service principal when absent, capturing its credentials.

    Polaris generates the client secret at creation time; the returned
    credentials are merged into ``credentials`` (keyed by principal name) so
    callers can persist them for REST catalog clients.
    """
    principals = {principal.get("name") for principal in client.list_principals()}
    if name in principals:
        logger.info("polaris_bootstrap_principal_exists", principal=name)
        return False
    created = client.create_principal(name=name)
    payload = created.get("principal", {}) if isinstance(created, dict) else {}
    creds = payload.get("credentials") or created.get("credentials") or {}
    if isinstance(creds, dict) and credentials is not None:
        credentials[name] = f"{creds.get('clientId', '')}:{creds.get('clientSecret', '')}"
    logger.info("polaris_bootstrap_principal_created", principal=name)
    return True


def bootstrap(client=None) -> int:
    """Bootstrap the Phlo realm objects in Polaris. Always exit 0 (best effort)."""
    if client is None:
        from phlo_polaris.resource import PolarisResource

        client = PolarisResource()
    if not wait_for_polaris(client):
        logger.warning("polaris_bootstrap_unavailable")
        return 0
    from phlo_polaris.settings import get_settings

    settings = get_settings()
    warehouse = f"s3://lake/warehouse/{settings.polaris_catalog}"
    ensure_catalog(client, name=settings.polaris_catalog, warehouse=warehouse)
    credentials: dict[str, str] = {}
    for principal in (
        settings.polaris_writer_client_id,
        settings.polaris_reader_client_id,
    ):
        ensure_principal(client, name=principal, credentials=credentials)
    grants = client.bootstrap_grants()
    logger.info("polaris_bootstrap_grants_applied", grants=grants)
    _persist_credentials(credentials)
    return 0


def _persist_credentials(credentials: dict[str, str]) -> None:
    """Write captured principal credentials next to the project state.

    Polaris returns each principal's secret exactly once at creation; the
    file lets REST catalog clients authenticate without pre-shared secrets.
    """
    import json
    import os
    from pathlib import Path

    if not credentials:
        return
    root = Path(os.getenv("PHLO_PROJECT_PATH", "."))
    path = root / ".phlo" / "polaris-principals.json"
    existing: dict[str, str] = {}
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        existing = {}
    existing.update(credentials)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(existing, indent=2, sort_keys=True), encoding="utf-8")
    logger.info("polaris_bootstrap_credentials_persisted", path=str(path))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="phlo_polaris.hooks")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("bootstrap", help="Create the Phlo catalog and principals")
    args = parser.parse_args(argv)
    if args.command == "bootstrap":
        return bootstrap()
    return 1


if __name__ == "__main__":
    sys.exit(main())
