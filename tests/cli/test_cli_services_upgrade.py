"""Generated-stack upgrades preserve project state and require exact review."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from phlo.cli.commands.services import upgrade as upgrade_module
from phlo.cli.commands.services.init import init_cmd
from phlo.cli.commands.services.upgrade import build_plan, upgrade_cmd
from phlo.operations.generated_stack import BASELINE, GeneratedStackPlan, generation_baseline
from phlo.plugins.discovery.services import ServiceDefinition, ServiceDiscovery


@pytest.fixture(autouse=True)
def _compose_validation(request, monkeypatch):
    # Portable planner contracts do not claim real Compose acceptance.
    if not request.node.get_closest_marker("integration"):
        monkeypatch.setattr(upgrade_module, "_validate_compose", lambda *args, **kwargs: None)


def _init(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.chdir(tmp_path)
    # Real installed provider discovery and templates, not fake service files.
    result = CliRunner().invoke(init_cmd, ["--no-dev"])
    assert result.exit_code == 0, result.output
    return tmp_path


def _review(root: Path) -> GeneratedStackPlan:
    result = CliRunner().invoke(upgrade_cmd, ["--dry-run", "--output", str(root / "review.json")])
    assert result.exit_code == 0, result.output
    return GeneratedStackPlan.model_validate_json((root / "review.json").read_text())


def _apply(root: Path, plan: GeneratedStackPlan):
    return CliRunner().invoke(
        upgrade_cmd, ["--apply", str(root / "review.json"), "--confirm", plan.token()]
    )


def test_reviewed_plan_is_stable_between_cli_processes(tmp_path, monkeypatch):
    root = _init(tmp_path, monkeypatch)
    plans = []
    for seed in ("1", "2", "3"):
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "from phlo.cli.commands.services.upgrade import upgrade_cmd; upgrade_cmd()",
                "--dry-run",
                "--output",
                str(root / "review.json"),
            ],
            cwd=root,
            env={**os.environ, "PYTHONHASHSEED": seed},
            capture_output=True,
            text=True,
            check=True,
        )
        assert "Plan token:" in result.stdout
        plans.append((root / "review.json").read_bytes())
    assert plans[0] == plans[1] == plans[2]


def test_upgrade_preserves_secrets_custom_files_and_volumes(tmp_path, monkeypatch):
    root = _init(tmp_path, monkeypatch)
    secrets = root / ".phlo/secrets/.env"
    original_secrets = secrets.read_bytes()
    unrelated = root / ".phlo/trino/catalog/custom.properties"
    unrelated.parent.mkdir(parents=True, exist_ok=True)
    unrelated.write_text("connector.name=memory\n")
    data = root / ".phlo/volumes/existing-object"
    data.write_bytes(b"retained object data")
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    plan = _review(root)
    assert plan.distributions["phlo"]
    assert not plan.migrations
    assert all(p.read_bytes() == content for p, content in before.items())
    result = _apply(root, plan)
    assert result.exit_code == 0, result.output
    assert secrets.read_bytes() == original_secrets
    assert unrelated.read_text() == "connector.name=memory\n"
    assert data.read_bytes() == b"retained object data"
    backup = root / ".phlo/upgrade-backups" / plan.token()
    assert (backup / "receipt.json").is_file()
    if os.name == "posix":
        assert all(p.stat().st_mode & 0o777 == 0o600 for p in backup.rglob("*") if p.is_file())
    repeat = _apply(root, plan)
    assert repeat.exit_code == 0, repeat.output
    secrets.write_text(secrets.read_text() + "\nAFTER_REVIEW=changed\n")
    assert _apply(root, plan).exit_code != 0


def test_upgrade_refuses_direct_edits_and_stale_review(tmp_path, monkeypatch):
    root = _init(tmp_path, monkeypatch)
    target = root / ".phlo/docker-compose.yml"
    content = yaml.safe_load(target.read_text())
    content["services"]["trino"]["environment"]["LOCAL_CUSTOMIZATION"] = "retain-me"
    target.write_text(yaml.safe_dump(content))
    plan = _review(root)
    assert next(c for c in plan.files if c.path == ".phlo/docker-compose.yml").state == "conflict"
    result = _apply(root, plan)
    assert result.exit_code != 0 and "conflicts" in result.output
    assert (
        yaml.safe_load(target.read_text())["services"]["trino"]["environment"][
            "LOCAL_CUSTOMIZATION"
        ]
        == "retain-me"
    )
    (root / "phlo.yaml").write_text((root / "phlo.yaml").read_text() + "\n# changed after review\n")
    result = _apply(root, plan)
    assert result.exit_code != 0 and "stale" in result.output
    assert not (root / ".phlo/upgrade-backups").exists()


def test_upgrade_delegates_minio_storage_migration(tmp_path, monkeypatch):
    root = _init(tmp_path, monkeypatch)
    target = root / ".phlo/docker-compose.yml"
    content = yaml.safe_load(target.read_text())
    content["services"]["minio"]["image"] = "previous/minio:old"
    content["services"]["minio"]["command"] = ["server", "/bitnami/minio/data"]
    content["services"]["minio"]["volumes"] = ["minio_data:/bitnami/minio/data"]
    target.write_text(yaml.safe_dump(content))
    (root / ".phlo" / BASELINE).write_text(generation_baseline(root, [".phlo/docker-compose.yml"]))
    plan = _review(root)
    assert any("phlo minio migrate-image" in migration for migration in plan.migrations)
    result = _apply(root, plan)
    assert result.exit_code != 0 and "provider migrations" in result.output
    assert target.read_text() == yaml.safe_dump(content)


@pytest.mark.parametrize("relative", [".phlo/docker-compose.yml", ".phlo/secrets/.env"])
def test_upgrade_refuses_symlinked_inputs(tmp_path, monkeypatch, relative):
    root = _init(tmp_path, monkeypatch)
    target = root / relative
    outside = root / "outside"
    outside.write_bytes(target.read_bytes())
    target.unlink()
    target.symlink_to(outside)
    with pytest.raises(ValueError, match="symlink"):
        build_plan(root, None, False)


def test_legacy_stack_without_baseline_conflicts_instead_of_adopting(tmp_path, monkeypatch):
    root = _init(tmp_path, monkeypatch)
    (root / ".phlo" / BASELINE).unlink()
    plan = _review(root)
    assert any(change.state == "conflict" for change in plan.files)
    assert _apply(root, plan).exit_code != 0
    assert json.loads((root / "review.json").read_text())["project"] == str(root)


def test_template_upgrade_keeps_declared_overrides_and_introduces_companion(tmp_path, monkeypatch):
    root = _init(tmp_path, monkeypatch)
    (root / "custom-jvm.config").write_text("-Xmx768M\n-Dcustom=true\n")
    (root / "custom-dagster.yaml").write_text("telemetry:\n  enabled: false\n")
    config = yaml.safe_load((root / "phlo.yaml").read_text())
    config["infrastructure"] = {
        "services": {
            "trino": {
                "files": {"trino/jvm.config": {"source": "custom-jvm.config", "mode": "replace"}}
            },
            "dagster": {
                "files": {
                    "dagster/dagster.yaml": {"source": "custom-dagster.yaml", "mode": "merge"}
                }
            },
        }
    }
    (root / "phlo.yaml").write_text(yaml.safe_dump(config))
    discovery = ServiceDiscovery()
    definitions = deepcopy(discovery.discover())
    definitions["dagster"].image = "ghcr.io/phlohouse/phlo-dagster:next"
    definitions["release-setup"] = ServiceDefinition(
        name="release-setup",
        description="Synthetic newly introduced companion",
        category="core",
        depends_on=["dagster"],
        image="example/setup:1",
        compose={"restart": "no"},
    )
    monkeypatch.setattr(discovery, "discover", lambda: definitions)
    monkeypatch.setattr(discovery, "get_service", lambda name: definitions.get(name))
    monkeypatch.setattr(upgrade_module, "ServiceDiscovery", lambda: discovery)
    plan = _review(root)
    assert "release-setup" in plan.new_companions
    assert plan.restart_order.index("dagster") < plan.restart_order.index("release-setup")
    result = _apply(root, plan)
    assert result.exit_code == 0, result.output
    assert (root / ".phlo/trino/jvm.config").read_text() == "-Xmx768M\n-Dcustom=true\n"
    assert (
        yaml.safe_load((root / ".phlo/dagster/dagster.yaml").read_text())["telemetry"]["enabled"]
        is False
    )
    assert yaml.safe_load((root / ".phlo/docker-compose.yml").read_text())["services"]["dagster"][
        "image"
    ].endswith(":next")


def test_failed_file_replacement_restores_originals(tmp_path, monkeypatch):
    root = _init(tmp_path, monkeypatch)
    plan = _review(root)
    before = {
        root / change.path: (root / change.path).read_bytes()
        for change in plan.files
        if change.before
    }
    write = upgrade_module.write_sensitive_file
    failed = False

    def fail_receipt(path, content, **kwargs):
        nonlocal failed
        if Path(path).name == "receipt.json" and not failed:
            failed = True
            raise OSError("synthetic disk failure")
        write(path, content, **kwargs)

    monkeypatch.setattr(upgrade_module, "write_sensitive_file", fail_receipt)
    result = _apply(root, plan)
    assert result.exit_code != 0 and "Upgrade failed (OSError)" in result.output
    assert all(path.read_bytes() == content for path, content in before.items())
    assert not (root / ".phlo/upgrade-backups" / plan.token() / "receipt.json").exists()
    assert (root / ".phlo/upgrade.lock").is_dir()


def test_invalid_private_plan_does_not_print_embedded_credentials(tmp_path, monkeypatch):
    root = _init(tmp_path, monkeypatch)
    plan = _review(root)
    document = plan.model_dump(mode="json")
    document["candidate_files"] = {"bad": {"private-password": "never-print-this"}}
    (root / "review.json").write_text(json.dumps(document))
    result = _apply(root, plan)
    assert result.exit_code != 0 and "Invalid upgrade plan" in result.output
    assert "never-print-this" not in result.output


@pytest.mark.integration
def test_upgrade_validates_resolved_personal_layers_before_mutation(tmp_path, monkeypatch):
    root = _init(tmp_path, monkeypatch)
    overlay = root / ".phlo/overrides/compose.yaml"
    overlay.parent.mkdir(exist_ok=True)
    overlay.write_text("services:\n  trino:\n    depends_on: [not-installed]\n")
    before = (root / ".phlo/docker-compose.yml").read_bytes()
    plan = _review(root)
    result = _apply(root, plan)
    assert result.exit_code != 0 and "Compose validation failed" in result.output
    assert (root / ".phlo/docker-compose.yml").read_bytes() == before
    assert not (root / ".phlo/upgrade-backups").exists()
    assert not (root / ".phlo/upgrade.lock").exists()
