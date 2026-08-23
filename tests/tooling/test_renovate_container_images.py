"""Semantic contracts for Renovate-managed package runtime images.

Replays the configured regex manager's match and mustache replacement against
real source strings to prove image references in package service.yaml files
update in place, including digest-pinned forms.
"""

from __future__ import annotations

import json
import re
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any, cast

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO_ROOT / "renovate.json"


def _manager() -> dict[str, Any]:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    managers = [
        manager
        for manager in config["customManagers"]
        if manager.get("customType") == "regex" and manager.get("datasourceTemplate") == "docker"
    ]
    assert len(managers) == 1
    return cast(dict[str, Any], managers[0])


def _python_regex(pattern: str) -> re.Pattern[str]:
    # Renovate uses RE2's JavaScript named-group spelling; Python uses ?P<name>.
    return re.compile(pattern.replace("(?<", "(?P<"))


def _image_patterns(manager: dict[str, Any]) -> list[re.Pattern[str]]:
    return [_python_regex(pattern) for pattern in manager["matchStrings"]]


def _image_match(manager: dict[str, Any], source: str) -> re.Match[str] | None:
    matches = [match for pattern in _image_patterns(manager) if (match := pattern.search(source))]
    assert len(matches) <= 1
    return matches[0] if matches else None


def _render_update(manager: dict[str, Any], source: str, *, new_value: str, new_digest: str) -> str:
    """Faithfully simulate Renovate's configured mustache replacement template."""
    match = _image_match(manager, source)
    assert match
    values = {"wrapperPrefix": "", "wrapperSuffix": ""}
    values.update({key: value or "" for key, value in match.groupdict().items()})
    values.update(newValue=new_value, newDigest=new_digest)
    rendered = manager["autoReplaceStringTemplate"]
    for key, value in values.items():
        rendered = rendered.replace("{{{" + key + "}}}", value)
    assert "{{{" not in rendered
    return source[: match.start()] + rendered + source[match.end() :]


def test_runtime_image_manager_covers_only_recursive_package_source_yaml() -> None:
    manager = _manager()
    file_patterns = [_python_regex(pattern) for pattern in manager["managerFilePatterns"]]

    assert any(
        pattern.fullmatch("packages/phlo-postgres/src/phlo_postgres/service.yaml")
        for pattern in file_patterns
    )
    assert any(
        pattern.fullmatch("packages/phlo-openmetadata/src/phlo_openmetadata/setup/service.yml")
        for pattern in file_patterns
    )
    delta_path = REPO_ROOT / "packages/phlo-delta/tests/compose/docker-compose.yml"
    delta_relative = delta_path.relative_to(REPO_ROOT).as_posix()
    delta_source = delta_path.read_text(encoding="utf-8")
    assert "    image: trinodb/trino:477" in delta_source
    assert not any(pattern.fullmatch(delta_relative) for pattern in file_patterns)
    assert _image_match(manager, delta_source) is None
    nested_immutable = delta_source.replace(
        "    image: trinodb/trino:477",
        "    image: trinodb/trino:483@sha256:" + "a" * 64,
        1,
    )
    assert _image_match(manager, nested_immutable) is None


def test_runtime_image_manager_extracts_every_real_immutable_root_image() -> None:
    manager = _manager()
    matched: set[Path] = set()
    eligible: set[Path] = set()

    for path in sorted((REPO_ROOT / "packages").glob("*/src/**/*.yaml")):
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(document, dict) or not isinstance(document.get("image"), str):
            continue
        relative = path.relative_to(REPO_ROOT)
        if "@sha256:" in document["image"]:
            eligible.add(relative)
        match = _image_match(manager, path.read_text(encoding="utf-8"))
        if match:
            matched.add(relative)
            assert match.group("depName")
            assert match.group("currentValue")
            assert re.fullmatch(r"sha256:[0-9a-f]{64}", match.group("currentDigest"))

    assert matched == eligible


def test_runtime_image_replacement_updates_real_plain_and_env_defaults_exactly() -> None:
    manager = _manager()
    new_digest = "sha256:" + "f" * 64
    examples = (
        (
            REPO_ROOT / "packages/phlo-alloy/src/phlo_alloy/service.yaml",
            "grafana/alloy:v1.18.0@sha256:491b0578c04983fd54fe99b587b6fab4404dc46d0dc16677bd6b00cc1140b308",
            "grafana/alloy:v9.9.9@" + new_digest,
            "image: grafana/alloy:v9.9.9@" + new_digest,
        ),
        (
            REPO_ROOT / "packages/phlo-hasura/src/phlo_hasura/service.yaml",
            "hasura/graphql-engine:v2.49.5@sha256:a9f427a9078b75c5f43ea40abd4ba4e426f45777f862eff7265f411a5ac96086",
            "hasura/graphql-engine:v9.9.9@" + new_digest,
            "image: ${HASURA_IMAGE:-hasura/graphql-engine:v9.9.9@" + new_digest + "}",
        ),
    )

    for path, old_reference, new_reference, expected_line in examples:
        source = path.read_text(encoding="utf-8")
        updated = _render_update(
            manager,
            source,
            new_value="v9.9.9",
            new_digest=new_digest,
        )
        assert updated == source.replace(old_reference, new_reference, 1)
        assert expected_line in updated.splitlines()
        assert updated.endswith("\n") == source.endswith("\n")
        assert updated.count("\n") == source.count("\n")


def test_runtime_image_manager_rejects_malformed_env_defaults() -> None:
    manager = _manager()
    assert (
        _image_match(
            manager,
            "image: ${ALLOY_IMAGE-grafana/alloy:v1.0.0@sha256:" + "a" * 64 + "}",
        )
        is None
    )


def test_phlo_owned_images_are_explicitly_disabled_and_never_automerged() -> None:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    manager = _manager()
    rules = config["packageRules"]
    disabled = [
        rule
        for rule in rules
        if rule.get("matchManagers") == ["custom.regex"]
        and rule.get("matchPackageNames") == ["ghcr.io/phlohouse/phlo-*"]
    ]
    review_required = [
        rule
        for rule in rules
        if rule.get("matchManagers") == ["custom.regex"] and rule.get("enabled") is not False
    ]
    assert disabled == [
        {
            "description": "Do not update Phlo-owned published images as upstream dependencies.",
            "matchManagers": ["custom.regex"],
            "matchPackageNames": ["ghcr.io/phlohouse/phlo-*"],
            "enabled": False,
        }
    ]
    assert review_required and all(rule.get("automerge") is False for rule in review_required)
    assert fnmatchcase("ghcr.io/phlohouse/phlo-api", disabled[0]["matchPackageNames"][0])
    internal_files = sorted((REPO_ROOT / "packages").glob("*/src/**/*.yaml"))
    internal_files = [
        path
        for path in internal_files
        if "ghcr.io/phlohouse/phlo-" in path.read_text(encoding="utf-8")
    ]
    assert len(internal_files) == 4
    assert all(
        _image_match(manager, path.read_text(encoding="utf-8")) is None for path in internal_files
    )
