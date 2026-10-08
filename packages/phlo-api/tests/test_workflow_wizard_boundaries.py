"""Characterise discovery failures and safe workflow file application."""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from phlo.capabilities import WorkflowFilePreview
from phlo_api.observatory_api import observatory_workflow_wizard as wizard


@pytest.mark.parametrize("policy", ["fail-on-conflict", "skip-if-exists"])
@pytest.mark.parametrize("verify_only", [False, True])
@pytest.mark.parametrize(
    "state", ["missing", "matching", "different", "binary", "directory", "symlink"]
)
def test_apply_file_preserves_existing_destinations(tmp_path, policy, verify_only, state):
    workflows = tmp_path / "workflows"
    workflows.mkdir()
    target = workflows / "asset.py"
    sentinel = tmp_path / "outside.py"
    sentinel.write_text("outside", encoding="utf-8")
    if state == "matching":
        target.write_text("generated", encoding="utf-8")
    elif state == "different":
        target.write_text("original", encoding="utf-8")
    elif state == "binary":
        target.write_bytes(b"\xff\xfe")
    elif state == "directory":
        target.mkdir()
    elif state == "symlink":
        target.symlink_to(sentinel)
    preview = WorkflowFilePreview(path="workflows/asset.py", content="generated")
    if state == "matching":
        assert (
            wizard._apply_workflow_file(
                tmp_path, preview, conflict_policy=policy, verify_only=verify_only
            )
            == "matching"
        )
    elif state == "missing" and not verify_only:
        assert wizard._apply_workflow_file(tmp_path, preview, conflict_policy=policy) == "written"
        assert target.read_text(encoding="utf-8") == "generated"
    elif policy == "skip-if-exists" and not verify_only:
        assert wizard._apply_workflow_file(tmp_path, preview, conflict_policy=policy) == "skipped"
    else:
        with pytest.raises(HTTPException) as caught:
            wizard._apply_workflow_file(
                tmp_path, preview, conflict_policy=policy, verify_only=verify_only
            )
        assert caught.value.status_code == 409
        assert caught.value.detail == (
            "Applied workflow files changed after completion."
            if verify_only
            else "File conflicts: workflows/asset.py"
        )
    assert sentinel.read_text(encoding="utf-8") == "outside"
    if state == "missing" and verify_only:
        assert not target.exists()
    elif state == "different":
        assert target.read_text(encoding="utf-8") == "original"
    elif state == "binary":
        assert target.read_bytes() == b"\xff\xfe"
    elif state == "directory":
        assert target.is_dir()
    elif state == "symlink":
        assert target.is_symlink()


def test_discovery_keeps_first_ids_and_partial_loader_results(monkeypatch):
    from phlo.plugins import discovery

    def item(identifier, value):
        return SimpleNamespace(to_browser_dict=lambda: {"id": identifier, "value": value})

    def partial_loader():
        yield item("shared", "registry")
        yield item(None, "empty id")
        raise RuntimeError("provider unavailable after first contributions")

    plugins = {
        "partial": SimpleNamespace(get_workflow_wizard_contributions=partial_loader),
        "module": SimpleNamespace(),
        "absent": None,
    }
    registry = SimpleNamespace(
        list=lambda kind: list(plugins), get=lambda kind, name: plugins[name]
    )
    monkeypatch.setattr(discovery, "get_global_registry", lambda: registry)

    def discover(*, plugin_type, auto_register):
        assert auto_register
        if plugin_type == "broken":
            raise RuntimeError("discovery failed")

    monkeypatch.setattr(discovery, "discover_plugins", discover)
    monkeypatch.setattr(wizard, "WORKFLOW_WIZARD_PLUGIN_TYPES", ("broken", "working"))
    monkeypatch.setattr(
        wizard, "WORKFLOW_WIZARD_FALLBACK_MODULES", ("missing", "no-loader", "fallback")
    )

    def import_module(name):
        if name == "missing":
            raise ImportError(name)
        if name == "no-loader":
            return SimpleNamespace()
        return SimpleNamespace(
            get_workflow_wizard_contributions=lambda: [
                item("shared", "duplicate"),
                item("other", "module"),
            ]
        )

    monkeypatch.setattr(wizard.importlib, "import_module", import_module)
    assert wizard.list_workflow_wizard_contributions() == [
        {"id": "shared", "value": "registry"},
        {"id": None, "value": "empty id"},
        {"id": "other", "value": "module"},
    ]


def test_missing_plugin_module_is_ignored(monkeypatch):
    from phlo.plugins import discovery

    monkeypatch.setattr(discovery, "discover_plugins", lambda **kwargs: None)
    monkeypatch.setattr(
        discovery,
        "get_global_registry",
        lambda: SimpleNamespace(list=lambda kind: ["plugin"], get=lambda *args: SimpleNamespace()),
    )
    monkeypatch.setattr(wizard, "WORKFLOW_WIZARD_PLUGIN_TYPES", ("working",))
    monkeypatch.setattr(wizard, "WORKFLOW_WIZARD_FALLBACK_MODULES", ())

    def missing(name):
        raise ImportError(name)

    monkeypatch.setattr(wizard.importlib, "import_module", missing)
    assert wizard.list_workflow_wizard_contributions() == []
