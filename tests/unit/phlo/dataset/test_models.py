"""Identity and record invariants of the neutral Dataset core."""

from __future__ import annotations

import pytest

from phlo.dataset import (
    DATASET_STATE_SCHEMA_VERSION,
    CandidateRecord,
    DatasetRecord,
    PublicationState,
    WorkflowState,
    candidate_dataset_id,
    dataset_table_id,
    is_candidate_dataset_id,
)


class TestIdentity:
    def test_candidate_ids_use_the_canonical_prefix(self) -> None:
        assert candidate_dataset_id("gold.customer_health") == "candidate:gold.customer_health"
        assert is_candidate_dataset_id("candidate:gold.customer_health")
        assert not is_candidate_dataset_id("gold.customer_health")

    def test_promoted_identity_is_the_table_key(self) -> None:
        assert not is_candidate_dataset_id("gold.customer_health")
        assert dataset_table_id("gold.customer_health") == "gold.customer_health"

    def test_promotion_preserves_the_table_key(self) -> None:
        table_id = "gold.customer_health"
        assert dataset_table_id(candidate_dataset_id(table_id)) == table_id

    def test_invalid_ids_are_rejected(self) -> None:
        with pytest.raises(ValueError):
            candidate_dataset_id("")
        with pytest.raises(ValueError):
            candidate_dataset_id("gold:nested")
        with pytest.raises(ValueError):
            dataset_table_id("candidate:")


class TestStates:
    def test_terminal_states(self) -> None:
        from phlo.dataset import (
            TERMINAL_PUBLICATION_STATES,
            TERMINAL_WORKFLOW_STATES,
        )

        assert {WorkflowState.REJECTED} == TERMINAL_WORKFLOW_STATES
        assert {PublicationState.RETIRED} == TERMINAL_PUBLICATION_STATES

    def test_state_enums_match_the_adr_vocabulary(self) -> None:
        assert {state.value for state in WorkflowState} == {
            "claimed",
            "review",
            "promoted",
            "rejected",
        }
        assert {state.value for state in PublicationState} == {"draft", "published", "retired"}


class TestRecords:
    def test_records_carry_schema_version_two(self) -> None:
        candidate = CandidateRecord(
            dataset_id="candidate:gold.customer_health",
            table_id="gold.customer_health",
            state="claimed",
        )
        dataset = DatasetRecord(
            dataset_id="gold.customer_health",
            table_id="gold.customer_health",
            publication_state="draft",
        )
        assert candidate.schema_version == DATASET_STATE_SCHEMA_VERSION == 2
        assert dataset.schema_version == 2

    def test_candidate_record_identity_is_validated(self) -> None:
        with pytest.raises(ValueError):
            CandidateRecord(
                dataset_id="candidate:other_table",
                table_id="gold.customer_health",
                state="claimed",
            )
        with pytest.raises(ValueError):
            CandidateRecord(
                dataset_id="candidate:gold.customer_health",
                table_id="gold.customer_health",
                state="nonsense",
            )
        with pytest.raises(ValueError):
            CandidateRecord(
                dataset_id="candidate:gold.customer_health",
                table_id="gold.customer_health",
                state="claimed",
                promoted_dataset_id="gold.customer_health",
            )

    def test_dataset_record_rejects_candidate_ids(self) -> None:
        with pytest.raises(ValueError):
            DatasetRecord(
                dataset_id="candidate:gold.customer_health",
                table_id="gold.customer_health",
                publication_state="draft",
            )

    def test_read_models_round_trip_key_fields(self) -> None:
        dataset = DatasetRecord(
            dataset_id="gold.customer_health",
            table_id="gold.customer_health",
            publication_state="published",
            policy_version="v1",
        )
        model = dataset.to_read_model()
        assert model["dataset_id"] == "gold.customer_health"
        assert model["publication_state"] == "published"
        assert model["schema_version"] == 2
