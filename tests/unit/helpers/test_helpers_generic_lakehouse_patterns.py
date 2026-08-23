"""Tests for reusable lakehouse patterns: bitemporal scopes, crosswalk
detection and coverage, effective-dated joins, sequence gap checks,
and artifact manifests."""

from __future__ import annotations

from datetime import UTC, datetime

from phlo.helpers import (
    ArtifactManifest,
    BitemporalScope,
    ReferenceContract,
    StateTransitionRule,
    artifact_manifest_to_table_rows,
    as_of_query_scope,
    assert_no_reference_gap,
    assert_reference_unique,
    bitemporal_predicate,
    build_crosswalk,
    canonical_groups,
    changed_keys_since,
    collect_workflow_evidence,
    correction_chain,
    crosswalk_coverage_report,
    detect_crosswalk_collisions,
    effective_join,
    event_sequence_gaps,
    events_from_rows,
    evidence_passed,
    file_checksum,
    invalid_transitions,
    latest_event_per_key,
    latest_records,
    manifest_from_paths,
    manifest_summary,
    missing_reference_keys,
    partition_scope,
    publish_eligibility_report,
    reconcile_key_sets,
    reference_coverage_report,
    reference_required_field_gaps,
    reference_snapshot,
    render_evidence_table,
    resolve_watermark,
    state_transition_counts,
    supersession_key,
    terminal_state_filter,
    unmapped_source_ids,
    valid_at_predicate,
    verify_manifest_checksums,
)


def test_crosswalk_helpers_detect_collisions_and_missing_ids() -> None:
    entries = build_crosswalk(
        [
            {"source_system": "erp", "source_id": "A", "canonical_id": "1"},
            {"source_system": "lims", "source_id": "A", "canonical_id": "1"},
            {"source_system": "erp", "source_id": "A", "canonical_id": "2"},
        ]
    )

    assert detect_crosswalk_collisions(entries) == {("erp", "A"): ["1", "2"]}
    assert unmapped_source_ids([("erp", "A"), ("erp", "B")], entries) == [("erp", "B")]
    assert canonical_groups(entries)["1"] == [("erp", "A"), ("lims", "A")]
    assert crosswalk_coverage_report([("erp", "A"), ("erp", "B")], entries) == {
        "observed_count": 2,
        "mapped_count": 1,
        "unmapped_count": 1,
        "collision_count": 1,
        "coverage_ratio": 0.5,
        "unmapped_source_ids": [("erp", "B")],
        "collisions": {("erp", "A"): ["1", "2"]},
    }


def test_event_ledger_helpers_find_latest_events_and_transitions() -> None:
    rows = [
        {"entity": "A", "state": "created", "ts": datetime(2026, 1, 1, tzinfo=UTC)},
        {"entity": "A", "state": "approved", "ts": datetime(2026, 1, 2, tzinfo=UTC)},
        {"entity": "B", "state": "created", "ts": datetime(2026, 1, 1, tzinfo=UTC)},
    ]
    events = events_from_rows(
        rows, entity_key_field="entity", event_type_field="state", event_time_field="ts"
    )

    assert latest_event_per_key(events)["A"].event_type == "approved"
    assert state_transition_counts(events) == {("created", "approved"): 1}
    assert event_sequence_gaps(
        [
            {"entity_key": "A", "sequence": 1},
            {"entity_key": "A", "sequence": 3},
            {"entity_key": "A", "sequence": 3},
        ]
    ) == [
        {"entity_key": "A", "kind": "duplicate", "sequence": 3},
        {
            "entity_key": "A",
            "kind": "gap",
            "previous_sequence": 1,
            "current_sequence": 3,
            "missing_sequences": [2],
        },
    ]


def test_effective_dated_reference_helpers_join_and_report_gaps() -> None:
    facts = [
        {"id": "A", "event_date": "2026-02-01", "value": 10},
        {"id": "B", "event_date": "2026-02-01", "value": 20},
    ]
    refs = [
        {"id": "A", "label": "alpha", "valid_from": "2026-01-01", "valid_to": None},
    ]

    assert reference_snapshot(refs, as_of="2026-02-01", key_field="id")["A"]["label"] == "alpha"
    assert (
        effective_join(facts, refs, fact_key="id", reference_key="id", fact_time="event_date")[0][
            "ref_label"
        ]
        == "alpha"
    )
    assert assert_no_reference_gap(
        facts, refs, fact_key="id", reference_key="id", fact_time="event_date"
    ) == [facts[1]]


def test_supersession_helpers_return_latest_and_correction_chains() -> None:
    rows = [
        {"record_id": "1", "business_id": "A", "version": 1, "invalidated": False},
        {
            "record_id": "2",
            "business_id": "A",
            "version": 2,
            "corrects_record_id": "1",
            "invalidated": False,
        },
        {"record_id": "3", "business_id": "B", "version": 1, "invalidated": True},
    ]

    assert supersession_key(rows[0], "business_id") == "A"
    assert latest_records(rows, key_fields=["business_id"], order_field="version") == [rows[1]]
    assert [row["record_id"] for row in correction_chain(rows, original_id="1")] == ["1", "2"]


def test_artifact_manifest_helpers_checksum_and_summarize(tmp_path) -> None:
    path = tmp_path / "extract.csv"
    path.write_text("id,value\n1,2\n")

    manifest = manifest_from_paths("extract", [path])

    assert isinstance(manifest, ArtifactManifest)
    assert manifest.artifacts[0].checksum == file_checksum(path)
    assert verify_manifest_checksums(manifest) == {str(path): True}
    assert manifest_summary(manifest)["artifact_count"] == 1
    assert artifact_manifest_to_table_rows(manifest)[0]["manifest_name"] == "extract"


def test_bitemporal_helpers_render_predicates() -> None:
    assert valid_at_predicate("2026-01-01") == (
        "(valid_from IS NULL OR valid_from <= '2026-01-01') "
        "AND (valid_to IS NULL OR '2026-01-01' < valid_to)"
    )
    assert "observed_from" in bitemporal_predicate(
        BitemporalScope(valid_at="2026-01-01", observed_at="2026-02-01")
    )
    assert "_phlo_partition_date = '2026-01-01'" in as_of_query_scope(
        partition_scope=partition_scope("2026-01-01"),
        bitemporal_scope=BitemporalScope(valid_at="2026-01-01"),
    )


def test_state_transition_helpers_find_invalid_transitions() -> None:
    rule = StateTransitionRule(
        allowed={"draft": {"reviewed"}, "reviewed": {"approved"}, "approved": {"approved"}},
        terminal_states={"approved"},
    )
    events = [
        {"id": "A", "state": "draft", "ts": 1},
        {"id": "A", "state": "approved", "ts": 2},
        {"id": "B", "state": "approved", "ts": 1},
    ]

    assert invalid_transitions(
        events, entity_field="id", state_field="state", order_field="ts", rule=rule
    ) == [{"entity": "A", "from": "draft", "to": "approved", "at": 2}]
    assert terminal_state_filter(events, state_field="state", terminal_states={"approved"}) == [
        events[1],
        events[2],
    ]


def test_reference_contract_helpers_find_duplicates_missing_and_gaps() -> None:
    contract = ReferenceContract(name="status", key_fields=["code"], required_fields=["label"])
    refs = [{"code": "A", "label": "Active"}, {"code": "A", "label": ""}]
    facts = [{"status_code": "A"}, {"status_code": "B"}]

    assert assert_reference_unique(refs, contract)[0]["key"] == "A"
    assert missing_reference_keys(
        facts, refs, fact_fields=["status_code"], reference_fields=["code"]
    ) == ["B"]
    assert reference_required_field_gaps(refs, contract) == [{"key": "A", "missing": ["label"]}]
    coverage = reference_coverage_report(
        facts,
        refs,
        fact_fields=["status_code"],
        reference_fields=["code"],
    )
    assert coverage["coverage_ratio"] == 0.5
    assert coverage["missing_keys"] == ["B"]


def test_evidence_helpers_summarize_workflow_outputs() -> None:
    summary = collect_workflow_evidence(
        name="publish",
        inputs=["raw.events"],
        outputs=["mart.events"],
        checks=[{"name": "row_count", "passed": True}],
        lineage_edges=[("raw.events", "mart.events")],
        decisions=[{"decision": "publish"}],
    )

    assert evidence_passed(summary) is True
    assert summary.to_dict()["name"] == "publish"
    assert render_evidence_table(summary)[2] == {"section": "checks", "count": 1}


def test_publish_eligibility_report_combines_checks_state_and_references() -> None:
    report = publish_eligibility_report(
        checks=[{"name": "row_count", "passed": True}],
        required_states=["approved"],
        current_state="approved",
        reference_reports=[{"missing_key_count": 0}],
    )

    assert report["eligible"] is True

    blocked = publish_eligibility_report(
        checks=[{"name": "freshness", "passed": False}],
        required_states=["approved"],
        current_state="draft",
        reference_reports=[{"missing_key_count": 2}],
    )

    assert blocked["eligible"] is False
    assert blocked["failed_check_count"] == 1
    assert blocked["reference_gap_count"] == 1


def test_reconcile_key_sets_reports_missing_and_extra_keys() -> None:
    result = reconcile_key_sets(["A", "B"], ["B", "C"])

    assert result.passed is False
    assert result.metadata["missing_in_target"] == ["A"]
    assert result.metadata["extra_in_target"] == ["C"]


def test_changed_keys_since_returns_unique_changed_entities() -> None:
    watermark = resolve_watermark(
        column="updated_at", stored_value=datetime(2026, 1, 1, tzinfo=UTC)
    )
    rows = [
        {"id": "A", "updated_at": datetime(2026, 1, 1, tzinfo=UTC)},
        {"id": "B", "updated_at": datetime(2026, 1, 2, tzinfo=UTC)},
        {"id": "B", "updated_at": datetime(2026, 1, 3, tzinfo=UTC)},
    ]

    assert changed_keys_since(rows, watermark, key_fields="id") == ["B"]
