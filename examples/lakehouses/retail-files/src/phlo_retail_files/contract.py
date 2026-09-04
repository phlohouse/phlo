"""Static package contract for the Retail Files project template.

The package is a project template. Its phlo-family dependencies use exact
released versions, its third-party dependencies stay within the declared
allowlist, and its resources must match the recorded digest. The contract is
committed, not generated; `resource_digest()` recomputes the digest from the
shipped resources so tests can verify it remains current.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
RESOURCES_DIR = PACKAGE_DIR / "resources" / "retail_files"
CONTRACT_PATH = PACKAGE_DIR / "blueprint_contract.json"


def load_contract() -> dict:
    """Return the static blueprint contract document."""
    return json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


def resource_digest() -> str:
    """Compute the stable digest over the packaged template resources.

    The digest is the SHA-256 of the canonical JSON array of
    ``[relative-posix-path, sha256(file-bytes)]`` pairs sorted by path, so it
    changes only when resource bytes change and is stable across machines.
    """
    entries: list[list[str]] = []
    for path in sorted(RESOURCES_DIR.rglob("*")):
        if path.is_file():
            entries.append(
                [
                    path.relative_to(RESOURCES_DIR).as_posix(),
                    hashlib.sha256(path.read_bytes()).hexdigest(),
                ]
            )
    canonical = json.dumps(entries, separators=(",", ":"))
    return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"
