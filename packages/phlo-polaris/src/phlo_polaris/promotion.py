"""Snapshot-based write-audit-publish promotion for Polaris-backed Iceberg.

Implements the neutral ``SnapshotPromotionCatalog`` contract (see
``phlo.capabilities.interfaces``) on top of Iceberg snapshot references:

- A run opens a candidate branch per table, named after the run and created
  at the table's current snapshot, so audit reads the exact candidate
  snapshots while main still hides them from consumers.
- Writers land rows into the candidate branch with idempotent unique-key
  semantics: replays of already-present keys are dropped and in-batch
  duplicates keep their last occurrence, so at-least-once delivery produces
  no duplicate logical records.
- Promotion overwrites main with the audited candidate content in one
  atomic Iceberg commit and records durable release rows before dropping branches
  while advancing a Phlo-controlled release pointer with a compare-and-swap
  guard on the ledger snapshot read before promotion. Candidates retain their
  original release revision; retries never rebase them onto newer releases.
- Abort drops candidate branches so rejected snapshots can never be
  promoted while remaining discoverable for audit until retention.

pyiceberg has no branch fast-forward, so promotion is a full-replace
overwrite of main from the candidate branch content. This is the
catalog-agnostic WAP move: readers see pre- or post-promotion data exactly,
never intermediate state.

This is deliberately not a branch/merge emulation of Nessie: Nessie remains
the branch-based catalog, Polaris the snapshot-promotion catalog.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

from phlo.capabilities.interfaces import CandidateSnapshot, ReleaseRecord
from phlo.exceptions import PhloTableError
from phlo.logging import get_logger
from phlo_polaris.catalog_backend import current_snapshot_id

logger = get_logger(__name__)

RELEASES_NAMESPACE = "phlo_wap"
RELEASES_TABLE = "releases"
STATE_ROW_KEY = "__state__"
CANDIDATE_REF_PREFIX = "phlo-wap-"


class ReleaseConflictError(PhloTableError):
    """Raised when promotion loses a compare-and-swap race on the release pointer."""


def candidate_ref_for_run(run_id: str) -> str:
    """Return the deterministic Iceberg snapshot reference name for a run."""
    safe_run = run_id.replace("/", "_")
    return f"{CANDIDATE_REF_PREFIX}{safe_run}"


def run_id_from_namespace(namespace: str) -> str:
    """Recover the logical run ID from a WAP candidate namespace binding.

    Accepts both the Dagster tag prefix (``pipeline-run-``) and the neutral
    helper prefix (``phlo_candidates__``).
    """
    for prefix in ("pipeline-run-", "phlo_candidates__"):
        if namespace.startswith(prefix):
            return namespace.removeprefix(prefix)
    return namespace


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _pending_promotions(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    completed = {row.get("release_id") for row in rows if row.get("kind") == "state"}
    return [
        row
        for row in rows
        if row.get("kind") == "promotion" and row.get("release_id") not in completed
    ]


class IcebergReleaseStore:
    """Durable candidate/release ledger backed by one Iceberg table.

    All rows (pointer state, candidates, releases) live in a single table so
    every promotion's ledger update is one atomic Iceberg commit. PyIceberg's
    optimistic concurrency serializes writers; the compare-and-swap check in
    :class:`PolarisSnapshotPromotionCatalog` refuses stale revisions.
    """

    def __init__(self, *, full_table_name: str | None = None, catalog: Any = None) -> None:
        self._full_table_name = full_table_name or f"{RELEASES_NAMESPACE}.{RELEASES_TABLE}"
        self._catalog = catalog

    def _load_catalog(self) -> Any:
        if self._catalog is None:
            from phlo_polaris.catalog_backend import load_pyiceberg_catalog

            self._catalog = load_pyiceberg_catalog()
        return self._catalog

    @contextmanager
    def publication_lock(self):
        """Serialize publishers and ledger appends using the shared Phlo database.

        The transaction lock spans destination writes and the ledger CAS, so
        disjoint-table publishers cannot expose data and then lose to another
        release. Every writer must use the same PostgreSQL database. Process
        failure releases the lock when PostgreSQL closes the connection.
        """
        from phlo_postgres.resource import PostgresResource

        with PostgresResource() as database, database.transactional_cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))",
                (f"phlo:polaris:release:{self._full_table_name}",),
            )
            yield

    def ensure_table(self) -> Any:
        """Create the ledger table when absent and return it loaded."""
        catalog = self._load_catalog()
        try:
            return catalog.load_table(self._full_table_name)
        except Exception:
            catalog.create_namespace_if_not_exists(RELEASES_NAMESPACE)
            from pyiceberg.schema import Schema
            from pyiceberg.types import LongType, NestedField, StringType, TimestamptzType

            schema = Schema(
                NestedField(1, "kind", StringType(), required=True),
                NestedField(2, "table_name", StringType(), required=False),
                NestedField(3, "snapshot_id", LongType(), required=False),
                NestedField(4, "release_id", StringType(), required=False),
                NestedField(5, "revision", LongType(), required=False),
                NestedField(6, "run_id", StringType(), required=False),
                NestedField(7, "status", StringType(), required=False),
                NestedField(8, "recorded_at", TimestamptzType(), required=False),
            )
            return catalog.create_table_if_not_exists(self._full_table_name, schema)

    def rows(self) -> list[dict[str, Any]]:
        """Return every ledger row."""
        rows, _ = self.read_for_update()
        return rows

    def read_for_update(self) -> tuple[list[dict[str, Any]], Any]:
        """Read rows and retain the exact Iceberg table version for a later CAS."""
        table = self.ensure_table()
        return [dict(row) for row in table.scan().to_arrow().to_pylist()], table

    def append_if_unchanged(self, rows: list[dict[str, Any]], version: Any) -> None:
        """Append only if the ledger still has the snapshot that was read.

        PyIceberg's append transaction asserts the original main snapshot at
        catalog commit. Never reload or retry against a newer table here:
        doing so would detach the write from the revision we validated.
        """
        import pyarrow as pa
        from pyiceberg.exceptions import CommitFailedException

        arrow = pa.Table.from_pylist(rows, schema=version.schema().as_arrow())
        try:
            version.append(arrow)
        except CommitFailedException as exc:
            raise ReleaseConflictError(message="Release ledger changed during promotion.") from exc

    def append(self, rows: list[dict[str, Any]]) -> None:
        """Append rows in one atomic Iceberg commit."""
        if not rows:
            return
        import pyarrow as pa

        with self.publication_lock():
            table = self.ensure_table()
            arrow = pa.Table.from_pylist(rows, schema=table.schema().as_arrow())
            table.append(arrow)

    def current_revision(self) -> int:
        """Return the latest recorded release-pointer revision (0 when unset)."""
        revisions = [
            int(row["revision"])
            for row in self.rows()
            if row.get("kind") == "state" and row.get("revision") is not None
        ]
        return max(revisions, default=0)


class PolarisSnapshotPromotionCatalog:
    """Polaris-backed snapshot promotion catalog.

    ``store`` and the per-table opener are injectable so tests exercise the
    promotion state machine without a live catalog; production wiring uses
    :class:`IcebergReleaseStore` and the PyIceberg REST catalog.
    """

    def __init__(
        self,
        *,
        store: Any | None = None,
        table_opener: Any | None = None,
    ) -> None:
        self._store = store
        self._table_opener = table_opener

    @property
    def store(self) -> Any:
        if self._store is None:
            self._store = IcebergReleaseStore()
        return self._store

    def _open_table(self, table_name: str) -> Any:
        if self._table_opener is not None:
            return self._table_opener(table_name)
        from phlo_polaris.catalog_backend import load_pyiceberg_catalog

        return load_pyiceberg_catalog().load_table(table_name)

    # -- Candidate lifecycle ------------------------------------------------

    def create_candidate(self, *, table_name: str, run_id: str) -> CandidateSnapshot:
        """Open a run-scoped candidate branch on ``table_name``.

        The branch is created at the table's current snapshot, so the
        candidate starts as an exact copy of the released state and only
        diverges when a writer lands rows into it.
        """
        existing = [
            row for row in self._open_candidate_rows(run_id) if row["table_name"] == table_name
        ]
        if existing:
            return next(
                candidate
                for candidate in self.list_candidates(namespace=f"pipeline-run-{run_id}")
                if candidate.table_name == table_name
            )
        revision = self.release_revision()
        table = self._open_table(table_name)
        ref = candidate_ref_for_run(run_id)
        snapshot_id = current_snapshot_id(table)
        if snapshot_id is None:
            raise PhloTableError(
                message=f"Table {table_name!r} has no current snapshot to stage as a candidate.",
                suggestions=["Materialize the table before opening a WAP candidate."],
            )
        if ref in table.metadata.refs:
            raise ReleaseConflictError(
                message=f"Candidate branch {ref!r} has no ledger baseline; stage a new run."
            )
        table.manage_snapshots().create_branch(snapshot_id, ref).commit()
        self.store.append(
            [
                {
                    "kind": "candidate",
                    "table_name": table_name,
                    "snapshot_id": int(snapshot_id),
                    "release_id": None,
                    "revision": revision,
                    "run_id": run_id,
                    "status": "open",
                    "recorded_at": _now(),
                }
            ]
        )
        return CandidateSnapshot(
            table_name=table_name,
            snapshot_id=int(snapshot_id),
            run_id=run_id,
            namespace=f"pipeline-run-{run_id}",
            created_at=_now(),
            metadata={"release_revision": revision},
        )

    def _branch_tip(self, table: Any, ref: str) -> int | None:
        metadata_ref = table.metadata.refs.get(ref)
        return int(metadata_ref.snapshot_id) if metadata_ref else None

    def _existing_keys(self, table: Any, ref: str, unique_key: list[str]) -> set[tuple]:
        """Return the unique-key tuples already present on the candidate branch."""
        if not unique_key:
            return set()
        arrow = table.scan(snapshot_id=self._branch_tip(table, ref)).select(*unique_key).to_arrow()
        columns = [arrow.column(name).to_pylist() for name in unique_key]
        return set(zip(*columns))

    def merge_rows_into_candidate(
        self,
        *,
        table_name: str,
        run_id: str,
        rows: list[dict[str, Any]],
        unique_key: list[str] | None = None,
    ) -> dict[str, Any]:
        """Land rows into the candidate branch with idempotent key semantics.

        Rows whose unique key already exists anywhere on the branch (which
        includes the full table history the branch was created from) are
        dropped, and in-batch duplicates keep their last occurrence, so
        replaying a range produces no duplicate logical records. Returns the
        branch-tip snapshot the audit phase reads.
        """
        if not rows:
            return {"appended": 0, "duplicates_dropped": 0, "snapshot_id": None}
        table = self._open_table(table_name)
        ref = candidate_ref_for_run(run_id)
        if ref not in table.metadata.refs:
            raise PhloTableError(
                message=f"Candidate branch {ref!r} is not open on {table_name!r}.",
                suggestions=["Call create_candidate before writing candidate rows."],
            )

        import pyarrow as pa

        candidate_rows = list(rows)
        duplicates_dropped = 0
        if unique_key:
            seen_in_batch: set[tuple] = set()
            deduped: list[dict[str, Any]] = []
            for row in reversed(candidate_rows):
                key = tuple(row.get(column) for column in unique_key)
                if key in seen_in_batch:
                    duplicates_dropped += 1
                    continue
                seen_in_batch.add(key)
                deduped.append(row)
            deduped.reverse()

            existing = self._existing_keys(table, ref, unique_key)
            fresh = [
                row for row in deduped if tuple(row.get(c) for c in unique_key) not in existing
            ]
            duplicates_dropped += len(deduped) - len(fresh)
            candidate_rows = fresh

        if not candidate_rows:
            return {
                "appended": 0,
                "duplicates_dropped": duplicates_dropped,
                "snapshot_id": self._branch_tip(table, ref),
            }

        arrow = pa.Table.from_pylist(candidate_rows)
        table.append(arrow, branch=ref)
        return {
            "appended": len(candidate_rows),
            "duplicates_dropped": duplicates_dropped,
            "snapshot_id": self._branch_tip(table, ref),
        }

    # -- SnapshotPromotionCatalog contract ---------------------------------

    def list_candidates(self, *, namespace: str) -> list[CandidateSnapshot]:
        """List open candidate snapshots under ``namespace``.

        The snapshot id reported is the candidate branch's live tip, i.e.
        the exact snapshot an audit reads. The ledger is append-only, so the
        latest row per candidate key decides open versus closed.
        """
        run_id = run_id_from_namespace(namespace)
        candidates: list[CandidateSnapshot] = []
        for row in self._open_candidate_rows(run_id):
            snapshot_id: int | None = None
            try:
                table = self._open_table(str(row["table_name"]))
                snapshot_id = self._branch_tip(table, candidate_ref_for_run(run_id))
            except Exception:
                logger.warning(
                    "polaris_candidate_tip_read_failed",
                    table_name=row.get("table_name"),
                    run_id=run_id,
                    exc_info=True,
                )
            candidates.append(
                CandidateSnapshot(
                    table_name=str(row["table_name"]),
                    snapshot_id=snapshot_id if snapshot_id is not None else int(row["snapshot_id"]),
                    run_id=run_id,
                    namespace=namespace,
                    created_at=row.get("recorded_at"),
                    metadata={"release_revision": row.get("revision")},
                )
            )
        return candidates

    def _open_candidate_rows(
        self, run_id: str, rows: list[dict[str, Any]] | None = None
    ) -> list[dict[str, Any]]:
        """Return the ledger rows whose latest status is ``open`` for a run."""
        latest: dict[tuple[str, int], dict[str, Any]] = {}
        for row in self.store.rows() if rows is None else rows:
            if row.get("kind") != "candidate" or row.get("run_id") != run_id:
                continue
            key = (str(row.get("table_name")), int(row.get("snapshot_id") or 0))
            current = latest.get(key)
            if current is None or (row.get("recorded_at") or _now()) >= (
                current.get("recorded_at") or _now()
            ):
                latest[key] = row
        return [row for row in latest.values() if row.get("status") == "open"]

    def promote_candidates(
        self,
        *,
        namespace: str,
        release_id: str,
        expected_revision: int | None = None,
        tables: list[str] | None = None,
    ) -> list[ReleaseRecord]:
        """Publish audited snapshots while holding the warehouse publication lock."""
        with self.store.publication_lock():
            return self._promote_candidates(
                namespace=namespace,
                release_id=release_id,
                expected_revision=expected_revision,
                tables=tables,
            )

    def _promote_candidates(
        self,
        *,
        namespace: str,
        release_id: str,
        expected_revision: int | None,
        tables: list[str] | None,
    ) -> list[ReleaseRecord]:
        """Publish one release under the store's exclusive publication lock.

        The CAS guard on ``expected_revision`` rejects promotions computed
        against a stale release pointer. Per table the move is one atomic
        Iceberg overwrite of main with the candidate branch content; the
        ledger release rows are appended after, so a crash between the two
        converges on retry (the repeated overwrite is identical).
        """
        rows, ledger_version = self.store.read_for_update()
        current_revision = max(
            (
                int(row["revision"])
                for row in rows
                if row.get("kind") == "state" and row.get("revision") is not None
            ),
            default=0,
        )
        if expected_revision is not None and int(expected_revision) != current_revision:
            raise ReleaseConflictError(
                message=(
                    f"Release pointer moved: expected revision {expected_revision}, "
                    f"current revision {current_revision}."
                ),
                suggestions=[
                    "Re-run the audit against the current release state before promoting.",
                ],
            )
        run_id = run_id_from_namespace(namespace)
        # Acknowledgement loss must not overwrite tables a second time.
        released = [
            row
            for row in rows
            if row.get("kind") == "release"
            and row.get("release_id") == release_id
            and row.get("run_id") == run_id
            and (tables is None or row.get("table_name") in tables)
        ]
        if released:
            return [
                ReleaseRecord(
                    table_name=str(row["table_name"]),
                    snapshot_id=int(row["snapshot_id"]),
                    release_id=release_id,
                    revision=int(row["revision"]),
                    promoted_at=row.get("recorded_at"),
                    run_id=run_id,
                )
                for row in released
            ]
        pending = _pending_promotions(rows)
        if any(
            row.get("release_id") != release_id or row.get("run_id") != run_id for row in pending
        ):
            raise ReleaseConflictError(
                message="An interrupted promotion must be resumed before another release."
            )
        selected = [
            row
            for row in self._open_candidate_rows(run_id, rows)
            if tables is None or row.get("table_name") in tables
        ]
        if not selected:
            logger.warning(
                "polaris_promotion_no_open_candidates", namespace=namespace, release_id=release_id
            )
            return []

        if any(row.get("revision") != current_revision for row in selected):
            raise ReleaseConflictError(
                message="Candidate release revision is stale or missing; stage and audit a new run."
            )

        promoted_at = _now()
        new_revision = current_revision + 1
        ref = candidate_ref_for_run(run_id)
        prepared = []
        for row in selected:
            table_name = str(row["table_name"])
            table = self._open_table(table_name)
            candidate_snapshot = self._branch_tip(table, ref)
            if candidate_snapshot is None:
                raise ReleaseConflictError(message=f"Candidate branch {ref!r} is missing.")
            current = table.current_snapshot()
            summary = current.summary if current is not None else None
            properties = summary.additional_properties if summary is not None else {}
            already_written = properties.get("phlo.release_id") == release_id and properties.get(
                "phlo.candidate_snapshot_id"
            ) == str(candidate_snapshot)
            if not already_written:
                # Pin main as well as the ledger: a concurrent publisher may
                # have changed this table since the candidate was created.
                if current_snapshot_id(table) != int(row["snapshot_id"]):
                    raise ReleaseConflictError(message=f"Main changed for {table_name!r}.")
            prepared.append((row, table, candidate_snapshot, already_written))

        intended = {(str(row["table_name"]), int(row["snapshot_id"])) for row in pending}
        actual = {(str(row["table_name"]), tip) for row, _, tip, _ in prepared}
        if pending and intended != actual:
            raise ReleaseConflictError(
                message="Pending promotion candidates changed; refusing to publish."
            )
        if not pending:
            # A process can fail after writing main but before the receipt.
            # Persist ownership and the exact candidate set first so another
            # publisher cannot overtake that recoverable operation.
            self.store.append_if_unchanged(
                [
                    {
                        "kind": "promotion",
                        "table_name": row["table_name"],
                        "snapshot_id": tip,
                        "release_id": release_id,
                        "revision": current_revision,
                        "run_id": run_id,
                        "status": "pending",
                        "recorded_at": promoted_at,
                    }
                    for row, _, tip, _ in prepared
                ],
                ledger_version,
            )
            _, ledger_version = self.store.read_for_update()

        rows_to_append: list[dict[str, Any]] = []
        records: list[ReleaseRecord] = []
        for row, table, candidate_snapshot, already_written in prepared:
            table_name = str(row["table_name"])
            if not already_written:
                candidate_arrow = table.scan(snapshot_id=candidate_snapshot).to_arrow()
                table.overwrite(
                    candidate_arrow,
                    snapshot_properties={
                        "phlo.release_id": release_id,
                        "phlo.candidate_snapshot_id": str(candidate_snapshot),
                    },
                )
            released_snapshot = current_snapshot_id(table)
            if released_snapshot is None:
                raise PhloTableError(
                    message=f"Table {table_name!r} has no snapshot after release overwrite.",
                    suggestions=["Retry the promotion once the catalog responds."],
                )
            record = ReleaseRecord(
                table_name=table_name,
                snapshot_id=int(released_snapshot),
                release_id=release_id,
                revision=new_revision,
                promoted_at=promoted_at,
                run_id=run_id,
            )
            records.append(record)
            rows_to_append.append(
                {
                    "kind": "release",
                    "table_name": table_name,
                    "snapshot_id": record.snapshot_id,
                    "release_id": record.release_id,
                    "revision": record.revision,
                    "run_id": record.run_id,
                    "status": "released",
                    "recorded_at": promoted_at,
                }
            )
        rows_to_append.append(
            {
                "kind": "state",
                "table_name": STATE_ROW_KEY,
                "snapshot_id": None,
                "release_id": release_id,
                "revision": new_revision,
                "run_id": run_id,
                "status": "released",
                "recorded_at": promoted_at,
            }
        )
        # One atomic ledger commit closes every member table of the release
        # together with the advanced pointer state row.
        self.store.append_if_unchanged(rows_to_append, ledger_version)
        # Keep candidates available when the CAS fails or acknowledgement is
        # lost. Cleanup follows the durable release receipt, never precedes it.
        for record in records:
            try:
                self._open_table(record.table_name).manage_snapshots().remove_branch(ref).commit()
            except Exception:
                logger.warning(
                    "polaris_candidate_ref_drop_failed",
                    table_name=record.table_name,
                    ref=ref,
                    exc_info=True,
                )
        return records

    def resolve_release(self, *, table_name: str) -> ReleaseRecord | None:
        """Return the release record consumers currently resolve for a table."""
        matches = [
            row
            for row in self.store.rows()
            if row.get("kind") == "release"
            and row.get("table_name") == table_name
            and row.get("status") == "released"
        ]
        if not matches:
            return None
        latest = max(matches, key=lambda row: int(row.get("revision") or 0))
        return ReleaseRecord(
            table_name=str(latest["table_name"]),
            snapshot_id=int(latest["snapshot_id"]),
            release_id=str(latest["release_id"]),
            revision=int(latest["revision"]),
            promoted_at=latest.get("recorded_at"),
            run_id=latest.get("run_id"),
        )

    def release_revision(self) -> int:
        """Return the current release-pointer revision for CAS guards."""
        return self.store.current_revision()

    def abort_candidates(self, *, namespace: str) -> bool:
        """Drop candidate branches under ``namespace``; they can never promote."""
        run_id = run_id_from_namespace(namespace)
        if any(row.get("run_id") == run_id for row in _pending_promotions(self.store.rows())):
            return False
        open_rows = self._open_candidate_rows(run_id)
        ref = candidate_ref_for_run(run_id)
        for row in open_rows:
            try:
                table = self._open_table(str(row["table_name"]))
                if ref in table.metadata.refs:
                    table.manage_snapshots().remove_branch(ref).commit()
            except Exception:
                logger.warning(
                    "polaris_candidate_ref_drop_failed",
                    table_name=row.get("table_name"),
                    ref=ref,
                    exc_info=True,
                )
                return False
        if not open_rows:
            # Nothing to abort is an idempotent success, mirroring branch
            # deletion of an already-absent ref.
            return True
        self.store.append(
            [
                {
                    "kind": "candidate",
                    "table_name": str(row["table_name"]),
                    "snapshot_id": int(row["snapshot_id"]),
                    "release_id": None,
                    "revision": None,
                    "run_id": run_id,
                    "status": "aborted",
                    "recorded_at": _now(),
                }
                for row in open_rows
            ]
        )
        return True

    def prune_candidates(self, *, older_than: datetime) -> list[str]:
        """Drop open candidate branches created before the retention cutoff."""
        pruned: list[str] = []
        candidate_runs = {
            str(row.get("run_id"))
            for row in self.store.rows()
            if row.get("kind") == "candidate" and row.get("run_id")
        }
        for run_id in sorted(candidate_runs):
            if any(row.get("run_id") == run_id for row in _pending_promotions(self.store.rows())):
                continue
            open_rows = self._open_candidate_rows(run_id)
            expired = [
                row
                for row in open_rows
                if row.get("recorded_at") is not None and row["recorded_at"] < older_than
            ]
            if not expired:
                continue
            ref = candidate_ref_for_run(run_id)
            failed = False
            for row in expired:
                try:
                    table = self._open_table(str(row["table_name"]))
                    if ref in table.metadata.refs:
                        table.manage_snapshots().remove_branch(ref).commit()
                except Exception:
                    logger.warning(
                        "polaris_candidate_prune_failed",
                        table_name=row.get("table_name"),
                        ref=ref,
                        exc_info=True,
                    )
                    failed = True
            if failed:
                continue
            self.store.append(
                [
                    {
                        "kind": "candidate",
                        "table_name": str(row["table_name"]),
                        "snapshot_id": int(row["snapshot_id"]),
                        "release_id": None,
                        "revision": None,
                        "run_id": run_id,
                        "status": "pruned",
                        "recorded_at": _now(),
                    }
                    for row in expired
                ]
            )
            pruned.append(f"pipeline-run-{run_id}")
        return pruned
