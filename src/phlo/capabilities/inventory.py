"""Provider-neutral evidence for a complete object-prefix inventory.

Defines frozen evidence records only; enumeration itself lives behind the
TableStore capability. A failed traversal yields no partial object set or
digest, and consumers must not treat an inventory as usable for destructive
operations unless ``complete`` is true.
Imported within phlo.capabilities (package init and interfaces) as shared evidence records.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class InventoryObject:
    """One immutable observation from an owned object-storage prefix."""

    identity: str
    size_bytes: int
    modified_at: datetime | None
    checksum_or_version: str | None


@dataclass(frozen=True, slots=True)
class ObjectInventory:
    """The result of enumerating an owned prefix through a paginated API.

    Consumers must treat ``objects`` as unusable for a destructive operation
    unless ``complete`` is true.  A failed traversal deliberately contains no
    partial object set or digest.
    """

    prefix: str
    retention_cutoff: datetime
    objects: tuple[InventoryObject, ...]
    page_count: int
    continuation_exhausted: bool
    complete: bool
    digest: str | None
    failure: str | None = None
