"""Tests for Dagster daemon platform principal.

Covers daemon principal construction, run-tag stamping, trigger inference from
Dagster run tags, authorization that is enforced only in regulated mode, and
principal detection from request headers for queued runs.
"""

from __future__ import annotations

from unittest.mock import MagicMock, PropertyMock, patch

import pytest

from phlo_dagster.daemon_identity import (
    DAEMON_PRINCIPAL_TYPE,
    DAEMON_SUBJECT,
    PHLO_INITIATOR_TAG,
    PHLO_PRINCIPAL_TAG,
    PHLO_TRIGGER_KIND_TAG,
    PHLO_TRIGGER_TAG,
    PhloQueuedRunCoordinator,
    _has_request_principal,
    _infer_daemon_trigger,
    authorize_daemon_run,
    build_run_tags,
    create_daemon_principal,
)


class TestCreateDaemonPrincipal:
    def test_basic_schedule_principal(self):
        principal = create_daemon_principal("schedule", "daily_dbt")
        assert principal.subject == DAEMON_SUBJECT
        assert principal.principal_type == DAEMON_PRINCIPAL_TYPE
        assert principal.attributes["trigger_kind"] == "schedule"
        assert principal.attributes["trigger_name"] == "daily_dbt"
        assert principal.attributes["authentication_source"] == "daemon"
        assert "dagster-daemon" in principal.groups

    def test_sensor_principal(self):
        principal = create_daemon_principal("sensor", "wap_promote")
        assert principal.attributes["trigger_kind"] == "sensor"
        assert principal.attributes["trigger_name"] == "wap_promote"

    def test_with_initiating_user(self):
        principal = create_daemon_principal(
            "schedule", "daily_dbt", initiating_user="alice@example.com"
        )
        assert principal.attributes["initiating_principal"] == "alice@example.com"

    def test_without_initiating_user(self):
        principal = create_daemon_principal("schedule", "daily_dbt")
        assert "initiating_principal" not in principal.attributes

    def test_auto_materialize_principal(self):
        principal = create_daemon_principal("auto_materialize", "orders_table")
        assert principal.attributes["trigger_kind"] == "auto_materialize"

    def test_retry_principal(self):
        principal = create_daemon_principal("retry", "failed_run_abc")
        assert principal.attributes["trigger_kind"] == "retry"


class TestBuildRunTags:
    def test_basic_tags(self):
        tags = build_run_tags("schedule", "daily_dbt")
        assert tags[PHLO_PRINCIPAL_TAG] == DAEMON_SUBJECT
        assert tags[PHLO_TRIGGER_TAG] == "daily_dbt"
        assert tags[PHLO_TRIGGER_KIND_TAG] == "schedule"
        assert PHLO_INITIATOR_TAG not in tags

    def test_tags_with_initiator(self):
        tags = build_run_tags("schedule", "daily_dbt", initiating_user="alice@co.com")
        assert tags[PHLO_INITIATOR_TAG] == "alice@co.com"


class TestAuthorizeDaemonRun:
    def test_noop_when_not_regulated(self, monkeypatch):
        monkeypatch.setenv("PHLO_REGULATED", "false")
        with patch("phlo_dagster.daemon_identity.enforce") as mock_enforce:
            authorize_daemon_run("schedule", "daily_dbt")
        mock_enforce.assert_not_called()

    @patch("phlo_dagster.daemon_identity.is_regulated", return_value=True)
    @patch("phlo_dagster.daemon_identity.enforce")
    def test_calls_enforce_when_regulated(self, mock_enforce, _mock_reg):
        mock_enforce.return_value = MagicMock(allowed=True, reason_code=None)
        authorize_daemon_run("schedule", "daily_dbt", run_id="run-123", asset_selection=["orders"])

        mock_enforce.assert_called_once()
        call_kwargs = mock_enforce.call_args.kwargs
        assert call_kwargs["principal"].subject == DAEMON_SUBJECT
        assert call_kwargs["principal"].principal_type == "platform"
        assert call_kwargs["action"] == "run.execute"
        assert call_kwargs["surface"] == "dagster-daemon"
        assert call_kwargs["request_id"] == "run-123"

    @patch("phlo_dagster.daemon_identity.is_regulated", return_value=True)
    @patch("phlo_dagster.daemon_identity.enforce")
    def test_raises_when_denied(self, mock_enforce, _mock_reg):
        mock_enforce.return_value = MagicMock(allowed=False, reason_code="explicit_deny")
        with pytest.raises(RuntimeError, match="Daemon run denied"):
            authorize_daemon_run("schedule", "daily_dbt")

    @patch("phlo_dagster.daemon_identity.is_regulated", return_value=True)
    @patch("phlo_dagster.daemon_identity.enforce")
    def test_asset_selection_in_resource_id(self, mock_enforce, _mock_reg):
        mock_enforce.return_value = MagicMock(allowed=True, reason_code=None)
        authorize_daemon_run("sensor", "wap_promote", asset_selection=["orders", "customers"])
        call_kwargs = mock_enforce.call_args.kwargs
        assert call_kwargs["resource"].resource_id == "orders,customers"

    @patch("phlo_dagster.daemon_identity.is_regulated", return_value=True)
    @patch("phlo_dagster.daemon_identity.enforce")
    def test_initiating_user_in_principal(self, mock_enforce, _mock_reg):
        mock_enforce.return_value = MagicMock(allowed=True, reason_code=None)
        authorize_daemon_run("schedule", "daily_dbt", initiating_user="alice@example.com")
        principal = mock_enforce.call_args.kwargs["principal"]
        assert principal.attributes["initiating_principal"] == "alice@example.com"

    @patch("phlo_dagster.daemon_identity.is_regulated", return_value=True)
    @patch("phlo_dagster.daemon_identity.enforce")
    def test_trigger_metadata_in_context(self, mock_enforce, _mock_reg):
        mock_enforce.return_value = MagicMock(allowed=True, reason_code=None)
        authorize_daemon_run("sensor", "wap_promote", asset_selection=["orders"])
        context = mock_enforce.call_args.kwargs["context"]
        assert context.attributes["trigger_kind"] == "sensor"
        assert context.attributes["trigger_name"] == "wap_promote"
        assert context.attributes["asset_selection"] == "orders"


class TestTriggerInference:
    def test_schedule_trigger(self):
        assert _infer_daemon_trigger({"dagster/schedule_name": "daily_dbt"}) == (
            "schedule",
            "daily_dbt",
        )

    def test_sensor_trigger(self):
        assert _infer_daemon_trigger({"dagster/sensor_name": "wap_promote"}) == (
            "sensor",
            "wap_promote",
        )

    def test_auto_materialize_trigger(self):
        assert _infer_daemon_trigger({"dagster/auto_materialize": "true"}) == (
            "auto_materialize",
            "auto_materialize",
        )

    def test_retry_trigger(self):
        assert _infer_daemon_trigger({"dagster/auto_retry_run_id": "run-123"}) == (
            "retry",
            "run-123",
        )


class TestQueuedRunCoordinator:
    def test_request_principal_detected_from_headers(self):
        context = MagicMock()
        context.get_request_header.side_effect = lambda header: {
            "Authorization": "Bearer token"
        }.get(header)
        assert _has_request_principal(context) is True

    @patch("phlo_dagster.daemon_identity.QueuedRunCoordinator.submit_run")
    @patch("phlo_dagster.daemon_identity.authorize_daemon_run")
    @patch("phlo_dagster.daemon_identity.is_regulated", return_value=True)
    def test_authorizes_daemon_submissions(self, _mock_regulated, mock_authorize, mock_submit):
        coordinator = PhloQueuedRunCoordinator()
        instance = MagicMock()
        dagster_run = MagicMock(
            run_id="run-123",
            tags={"dagster/schedule_name": "daily_dbt"},
        )
        context = MagicMock(dagster_run=dagster_run)
        context.get_request_header.return_value = None
        mock_submit.return_value = dagster_run

        with patch.object(
            PhloQueuedRunCoordinator,
            "_instance",
            new_callable=PropertyMock,
            return_value=instance,
        ):
            result = coordinator.submit_run(context)

        assert result is dagster_run
        mock_authorize.assert_called_once_with(
            trigger_kind="schedule",
            trigger_name="daily_dbt",
            run_id="run-123",
        )
        instance.add_run_tags.assert_called_once_with(
            "run-123",
            build_run_tags(trigger_kind="schedule", trigger_name="daily_dbt"),
        )

    @patch("phlo_dagster.daemon_identity.QueuedRunCoordinator.submit_run")
    @patch("phlo_dagster.daemon_identity.authorize_daemon_run")
    @patch("phlo_dagster.daemon_identity.is_regulated", return_value=True)
    def test_skips_human_submissions(self, _mock_regulated, mock_authorize, mock_submit):
        coordinator = PhloQueuedRunCoordinator()
        instance = MagicMock()
        dagster_run = MagicMock(
            run_id="run-123",
            tags={"dagster/schedule_name": "daily_dbt"},
        )
        context = MagicMock(dagster_run=dagster_run)
        context.get_request_header.side_effect = lambda header: (
            "Bearer token" if header == "Authorization" else None
        )
        mock_submit.return_value = dagster_run

        with patch.object(
            PhloQueuedRunCoordinator,
            "_instance",
            new_callable=PropertyMock,
            return_value=instance,
        ):
            coordinator.submit_run(context)

        mock_authorize.assert_not_called()


class TestDefaultRunRef:
    @staticmethod
    def _submit(tmp_path, monkeypatch, tags, *, regulated=False):
        (tmp_path / "phlo.yaml").write_text(
            "name: demo\n"
            "environments:\n"
            "  nightscout:\n"
            "    dagster_location: demo.definitions\n"
            "    nessie_ref: soak\n"
            "default_environment: nightscout\n"
        )
        monkeypatch.chdir(tmp_path)
        from phlo.config.project import clear_config_cache

        clear_config_cache()
        coordinator = PhloQueuedRunCoordinator()
        instance = MagicMock()
        dagster_run = MagicMock(run_id="run-9", tags=tags)
        context = MagicMock(dagster_run=dagster_run)
        context.get_request_header.return_value = None
        with (
            patch("phlo_dagster.daemon_identity.QueuedRunCoordinator.submit_run"),
            patch("phlo_dagster.daemon_identity.is_regulated", return_value=regulated),
            patch(
                "phlo_dagster.daemon_identity.authorize_daemon_run",
                side_effect=RuntimeError("denied"),
            ),
            patch.object(
                PhloQueuedRunCoordinator,
                "_instance",
                new_callable=PropertyMock,
                return_value=instance,
            ),
        ):
            try:
                if regulated:
                    with pytest.raises(RuntimeError, match="denied"):
                        coordinator.submit_run(context)
                else:
                    coordinator.submit_run(context)
            finally:
                clear_config_cache()
        return instance

    def test_untagged_scheduled_run_gets_default_environment_ref(self, tmp_path, monkeypatch):
        instance = self._submit(tmp_path, monkeypatch, {"dagster/schedule_name": "marts"})

        instance.add_run_tags.assert_called_once_with("run-9", {"phlo/ref": "soak"})

    def test_run_that_names_a_ref_is_left_alone(self, tmp_path, monkeypatch):
        instance = self._submit(tmp_path, monkeypatch, {"phlo/wap_branch": "wap-1"})

        instance.add_run_tags.assert_not_called()

    def test_denied_daemon_run_is_not_attributed_to_an_environment(self, tmp_path, monkeypatch):
        instance = self._submit(
            tmp_path, monkeypatch, {"dagster/schedule_name": "marts"}, regulated=True
        )

        instance.add_run_tags.assert_not_called()
