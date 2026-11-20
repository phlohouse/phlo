"""A fresh lakehouse works without hand-set settings: actions, sign-in, upgrades."""

from __future__ import annotations

import errno
import socket
from pathlib import Path

import click
import pytest
import yaml

from phlo.cli.commands.services import init as init_module
from phlo.cli.commands.services import start as start_module
from phlo.cli.commands.services.upgrade import _merge_env_defaults
from phlo.cli.commands.services.utils import (
    DEFAULT_API_EXTRAS,
    apply_action_defaults,
    apply_notebook_defaults,
    grant_dev_access,
)
from phlo.plugins.compose.env import render_env
from tests.support import _service

API = _service("phlo-api")
DAGSTER = _service("dagster")
DAEMON = _service("dagster-daemon")
KERNELS = _service("phlo-notebook-kernels")


def test_generated_single_api_stack_turns_actions_on() -> None:
    """Verify the assertions that hold for the generated stack are recorded."""
    result = apply_action_defaults([API, DAGSTER, DAEMON], {})
    assert result["PHLO_V1_ACTIONS_SINGLE_REPLICA"] == "1"
    assert result["PHLO_V1_ACTIONS_SINGLE_PROCESS"] == "1"
    assert result["PHLO_V1_ACTIONS_REF_TAG_CONTRACT"] == "1"


def test_more_api_workers_keep_the_single_process_assertion_off() -> None:
    """Verify a raised WEB_CONCURRENCY or shared controls are never overridden."""
    workers = apply_action_defaults([API, DAGSTER, DAEMON], {"WEB_CONCURRENCY": "4"})
    assert "PHLO_V1_ACTIONS_SINGLE_PROCESS" not in workers
    shared = apply_action_defaults(
        [API], {"PHLO_API_OPERATION_CONTROLS_DB_URL": "postgresql://controls"}
    )
    assert "PHLO_V1_ACTIONS_SINGLE_REPLICA" not in shared
    assert "PHLO_V1_ACTIONS_REF_TAG_CONTRACT" not in shared
    explicit = apply_action_defaults([API], {"PHLO_V1_ACTIONS_SINGLE_REPLICA": "0"})
    assert explicit["PHLO_V1_ACTIONS_SINGLE_REPLICA"] == "0"


def test_without_the_api_nothing_is_asserted() -> None:
    """Verify a stack without phlo-api gets no API settings."""
    assert apply_action_defaults([DAGSTER, DAEMON], {}) == {}


def test_notebooks_keep_the_default_providers() -> None:
    """Verify adding notebooks does not drop the default API providers."""
    result = apply_notebook_defaults([API, KERNELS], {})
    assert result["PHLO_API_EXTRAS"] == f"{DEFAULT_API_EXTRAS},notebooks"


def test_dev_identity_can_operate_its_own_lakehouse(tmp_path: Path) -> None:
    """Verify the generated dev identity gains the local operator actions, not admin."""
    path = tmp_path / "authorization" / "policies.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(
        yaml.safe_dump({"policies": [{"policy_id": "observatory-dev-discovery"}]}),
        encoding="utf-8",
    )
    assert grant_dev_access(tmp_path, [API], ["sandbox"]) is True
    actions = {
        policy.get("action")
        for policy in yaml.safe_load(path.read_text(encoding="utf-8"))["policies"]
    }
    assert {"asset.execute", "asset.manage", "catalog.manage", "run.manage"} <= actions
    assert "admin.manage" not in actions
    assert "settings.manage" not in actions


def test_upgrade_merge_adds_new_settings_and_fills_owned_blanks(tmp_path: Path) -> None:
    """Verify an upgrade keeps operator values and fills only Phlo-derived blanks."""
    path = tmp_path / ".env"
    path.write_text("# kept\nPHLO_API_EXTRAS=\nWEB_CONCURRENCY=2\nOPERATOR_BLANK=\n")
    rendered = (
        "PHLO_API_EXTRAS=dagster,trino\nWEB_CONCURRENCY=1\nOPERATOR_BLANK=x\n"
        "PHLO_V1_PREVIEW_TRINO_URL=https://trino-preview:8443\n"
    )
    merged = _merge_env_defaults(path, rendered, {"PHLO_API_EXTRAS", "PHLO_V1_PREVIEW_TRINO_URL"})
    assert merged is not None
    text = merged[0].decode()
    assert "# kept" in text
    assert "PHLO_API_EXTRAS=dagster,trino" in text
    assert "WEB_CONCURRENCY=2" in text
    assert "OPERATOR_BLANK=\n" in text
    assert text.count("# Added by phlo services upgrade") == 1
    assert "PHLO_V1_PREVIEW_TRINO_URL=https://trino-preview:8443" in text
    path.write_text(text)
    assert (
        _merge_env_defaults(path, rendered, {"PHLO_API_EXTRAS", "PHLO_V1_PREVIEW_TRINO_URL"})
        is None
    )


def test_externally_issued_secrets_are_never_generated() -> None:
    """Verify `generate: false` secrets stay empty while local ones are generated."""
    service = _service("oauth2-proxy")
    service.env_vars = {
        "OAUTH2_PROXY_CLIENT_SECRET": {"secret": True, "generate": False},
        "OAUTH2_PROXY_COOKIE_SECRET": {"secret": True},
    }
    content = render_env(
        [service],
        env_overrides={},
        include_secrets=True,
        include_non_secrets=False,
        existing_values={},
        header_lines=[],
    )
    values = dict(line.split("=", 1) for line in content.splitlines() if "=" in line)
    assert values["OAUTH2_PROXY_CLIENT_SECRET"] == ""
    assert values["OAUTH2_PROXY_COOKIE_SECRET"]


def test_port_check_ignores_hosts_without_ipv6(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify a host that cannot create IPv6 sockets still reports a free port."""
    real_socket = socket.socket

    def fake_socket(family: int = socket.AF_INET, *args, **kwargs):
        if family == socket.AF_INET6:
            raise OSError(errno.EAFNOSUPPORT, "Address family not supported by protocol")
        return real_socket(family, *args, **kwargs)

    monkeypatch.setattr(start_module.socket, "socket", fake_socket)
    with real_socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    assert start_module._is_host_port_available(port) is True


def test_init_sets_up_local_observatory_only_in_development(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify the local sign-in is automatic for development stacks with the UI."""
    monkeypatch.chdir(tmp_path)
    installed = {"phlo-api": API, "observatory": _service("observatory")}
    assert init_module._wants_local_observatory({}, installed, False, False) is True
    assert init_module._wants_local_observatory({}, installed, True, False) is False
    assert init_module._wants_local_observatory({}, installed, False, True) is False
    assert init_module._wants_local_observatory({}, {"phlo-api": API}, False, False) is False
    assert (
        init_module._wants_local_observatory(
            {"authentication": {"provider": "jwt"}}, installed, False, False
        )
        is False
    )
    disabled = {"services": {"observatory": {"enabled": False}}}
    assert init_module._wants_local_observatory(disabled, installed, False, False) is False


def test_automatic_sign_in_reports_instead_of_failing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Verify a refused automatic setup is reported and init carries on."""

    def refuse(*_args, **_kwargs):
        raise click.ClickException("Existing API authorization will not be overwritten.")

    monkeypatch.setattr(init_module, "_configure_observatory_dev", refuse)
    init_module._apply_observatory_dev(
        {}, "demo", tmp_path, tmp_path / "phlo.yaml", explicit=False, automatic=True
    )
    assert "Local Observatory sign-in not configured" in capsys.readouterr().err
    with pytest.raises(click.ClickException):
        init_module._apply_observatory_dev(
            {}, "demo", tmp_path, tmp_path / "phlo.yaml", explicit=True, automatic=False
        )


def test_sign_in_setup_keeps_phlo_yaml_comments(tmp_path: Path) -> None:
    """Verify new sections are appended and the operator's comments survive."""
    config_file = tmp_path / "phlo.yaml"
    config_file.write_text("# my notes\nname: demo\n", encoding="utf-8")
    original = {"name": "demo"}
    config = {"name": "demo", "environments": {"sandbox": {"nessie_ref": "main"}}}
    text = init_module._render_project_config(config_file, original, config)
    assert text.startswith("# my notes\nname: demo\n")
    assert yaml.safe_load(text) == config


@pytest.mark.parametrize("flag", [[], ["--no-observatory-dev"]])
def test_init_sign_in_is_on_by_default_and_can_be_left_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, flag: list[str]
) -> None:
    """Verify a development API stack gets the local sign-in unless told not to."""
    from click.testing import CliRunner

    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(init_module.init_cmd, ["--no-dev", "--profile", "api", *flag])
    assert result.exit_code == 0, result.output
    config = yaml.safe_load((tmp_path / "phlo.yaml").read_text(encoding="utf-8")) or {}
    assert ("authentication" in config) is (not flag)
