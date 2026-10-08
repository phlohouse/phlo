"""Check native Dagster validation without instance construction or secret resolution."""

import pytest

from phlo_dagster.service_config import validate_service_file


@pytest.mark.parametrize(
    ("content", "field"),
    [
        ("sensors: {num_workers: wrong}", "sensors.num_workers"),
        ("sensors: {typo: 2}", "sensors.typo"),
        (
            "run_launcher: {module: dagster, class: DefaultRunLauncher, config: {typo: 1}}",
            "run_launcher.config.typo",
        ),
        (
            "run_coordinator: {module: dagster, class: QueuedRunCoordinator, config: {max_concurrent_runs: -2}}",
            "max_concurrent_runs",
        ),
        (
            "run_queue: {}\nrun_coordinator: {module: dagster, class: QueuedRunCoordinator}",
            "incompatible",
        ),
        (
            "storage: {sqlite: {base_dir: /tmp/dagster}}\nrun_storage: {module: custom, class: CustomStorage}",
            "incompatible",
        ),
    ],
)
def test_native_schema_and_section_conflicts_fail_with_field_location(content, field):
    with pytest.raises(ValueError, match=field):
        validate_service_file("dagster/dagster.yaml", content)


def test_native_env_reference_is_not_resolved_or_echoed(monkeypatch):
    monkeypatch.setenv("QUEUE_LIMIT", "private-value-not-an-integer")
    content = (
        "run_coordinator:\n  module: phlo_dagster.daemon_identity\n"
        "  class: PhloQueuedRunCoordinator\n  config:\n"
        "    max_concurrent_runs: {env: QUEUE_LIMIT}\n"
    )
    assert validate_service_file("dagster/dagster.yaml", content) is None
    with pytest.raises(ValueError) as error:
        validate_service_file("dagster/dagster.yaml", "sensors: {num_workers: private-value}")
    assert "sensors.num_workers" in str(error.value)
    assert "private-value" not in str(error.value)


def test_custom_class_config_is_envelope_validated_without_importing_class():
    content = "run_coordinator: {module: nonexistent_project_module, class: Custom, config: {custom_option: 7}}"
    assert validate_service_file("dagster/dagster.yaml", content) is None
    with pytest.raises(ValueError, match="module"):
        validate_service_file("dagster/dagster.yaml", "run_coordinator: {class: Custom}")
