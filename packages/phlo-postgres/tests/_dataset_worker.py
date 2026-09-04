"""Multiprocess Dataset transition worker (not collected by pytest).

Run as a script by test_dataset_state_store.py's two-process proof. Binds one
worker process to the shared file-locked settings backend and drives one
publication transition through the core DatasetService, printing the outcome
as one JSON line.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from _dataset_test_backends import FileLockSettingsStore

from phlo.dataset import (
    DatasetPolicy,
    DatasetService,
    StaticPolicySource,
    TransitionRequest,
    state_store_namespace,
)
from phlo.dataset.evidence import EvidenceRecord
from phlo_postgres.dataset_state_store import SettingsDatasetStateStore


class NoEvidence:
    """Evidence source returning nothing; the test policy needs no evidence."""

    def evidence(self, subject: str, kinds) -> tuple[EvidenceRecord, ...]:
        return ()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-file", required=True)
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--action-id", required=True)
    parser.add_argument("--expected-state", default="draft")
    args = parser.parse_args()

    store = SettingsDatasetStateStore(
        settings_store=FileLockSettingsStore(Path(args.state_file)),
        namespace=state_store_namespace(args.project_root),
    )
    service = DatasetService(
        store=store,
        evidence_source=NoEvidence(),
        policy_source=StaticPolicySource(policy=DatasetPolicy(policy_version="test-policy")),
    )
    outcome = service.transition(
        TransitionRequest(
            resource_id="gold.demo",
            action="publish",
            action_id=args.action_id,
            actor=f"worker-{args.action_id}",
            scope="lakehouse:operate",
            expected_state=args.expected_state or None,
        )
    )
    record = store.load("gold.demo")
    print(
        json.dumps(
            {
                "status": outcome.status.value,
                "after_state": outcome.after_state,
                "message": outcome.message,
                "observed_state": record.current_state if record else None,
                "audit_count": len(store.audit_events()),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
