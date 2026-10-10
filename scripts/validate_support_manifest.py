#!/usr/bin/env python3
"""Validate the checked-in v1 support boundary without network access.

Checks registry/support/v1.json against its schema (a minimal in-repo
validator, not a full JSON Schema implementation), verifies that manifest
paths, markdown anchors, and named claims bind to committed files, and that
provider packages declare the current core compatibility epoch. Exits 0
only when the boundary is internally consistent with the repository.
"""

from __future__ import annotations

import json
import re
import sys
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Any, NamedTuple

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "registry/support/v1.json"
SCHEMA_PATH = ROOT / "registry/support/schema/v1.json"
REGISTRY_RELATIVE_PATHS = ("registry/plugins.json", "src/phlo/plugins/registry_data.json")
REQUIRED_V1_CAPABILITIES = {
    "mandatory_authorization",
    "secure_production_deployment",
    "maintenance_operations",
    "upgrade_restore",
    "golden_path_ci",
    "observatory_run_report",
}
APPROVED_V1_CAPABILITIES = REQUIRED_V1_CAPABILITIES | {
    "iceberg_tables",
    "nessie_branch_write_audit_publish",
    "dlt_ingestion",
    "dbt_transformations",
    "pandera_quality",
}
APPROVED_PROFILES = {
    "blessed_core": "scope-frozen",
    "supported_optional": "target_capabilities_only",
    "preview": "available_without_v1_guarantee",
    "development_only": "local_or_test_use",
}
# Named claims that must bind to specific committed source, tests, and workflow
# commands so that semantically stale prose cannot pass path-only validation.
NAMED_CLAIM_BINDINGS: dict[str, dict[str, object]] = {
    "observatory_run_report": {
        "required_evidence": [
            "packages/phlo-api/src/phlo_api/observatory_api/run_report.py",
            "packages/phlo-api/tests/test_observatory_api.py",
        ],
        "forbidden_evidence": [],
        "forbidden_reason_phrases": [
            "does not expose an authoritative durable per-run report",
        ],
    },
    "upgrade_restore": {
        "required_evidence": [
            "scripts/recovery_drill.py",
            "tests/scripts/test_recovery_drill.py",
            ".github/workflows/integration.yml",
        ],
        "forbidden_evidence": [],
        "forbidden_reason_phrases": [
            "Upgrade and recovery are documentation procedures",
        ],
        "workflow_commands": {
            ".github/workflows/integration.yml": "scripts/recovery_drill.py",
        },
    },
    "golden_path_ci": {
        "required_evidence": [
            "scripts/release_golden_path.py",
            "tests/scripts/test_release_golden_path.py",
            ".github/workflows/windows-compose-portability.yml",
        ],
        "forbidden_evidence": [
            "scripts/run_golden_path.py",
        ],
        "forbidden_reason_phrases": [
            "required CI does not invoke",
        ],
        "workflow_commands": {
            ".github/workflows/windows-compose-portability.yml": "test_release_golden_path",
        },
    },
}
SERVICE_PRIMARY_OVERRIDES = {
    "phlo-postgres": {
        "postgres-exporter": "src/phlo_postgres/exporter_service.yaml",
        "postgres-volume-setup": "src/phlo_postgres/volume_setup.yaml",
    }
}


class ValidationError(ValueError):
    """Raised when the support boundary is internally inconsistent."""


def provider_core_requirement(repo_root: Path) -> str:
    """Return the workspace-wide core compatibility epoch for provider packages."""
    with (repo_root / "pyproject.toml").open("rb") as handle:
        version = tomllib.load(handle)["project"]["version"]
    match = re.fullmatch(r"(\d+)\.(\d+)\.\d+", version)
    if not match:
        raise ValidationError(f"phlo version {version!r} must use a major.minor.patch release")
    major, minor = (int(part) for part in match.groups())
    return f"phlo>={version},<{major}.{minor + 1}"


def provider_core_compatibility_errors(repo_root: Path) -> list[str]:
    """Ensure every workspace provider declares the current core compatibility epoch."""
    expected = provider_core_requirement(repo_root)
    errors: list[str] = []
    for path in sorted((repo_root / "packages").glob("*/pyproject.toml")):
        with path.open("rb") as handle:
            project = tomllib.load(handle)["project"]
        requirements = [
            requirement
            for requirement in project.get("dependencies", [])
            if re.match(r"^phlo(?:\[[^]]+\])?(?:[<>=!~ ]|$)", requirement, re.IGNORECASE)
        ]
        if requirements != [expected]:
            errors.append(
                f"provider {project['name']!r} must declare {expected!r}; found {requirements!r}"
            )
    return errors


def _type_matches(value: object, expected: str) -> bool:
    return {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "boolean": isinstance(value, bool),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
    }.get(expected, True)


def _resolve_schema_ref(schema: dict[str, Any], root: dict[str, Any]) -> Any:
    target: Any = root
    for part in schema["$ref"][2:].split("/"):
        target = target[part]
    return target


def _array_schema_errors(
    value: list[Any], schema: dict[str, Any], path: str, root: dict[str, Any]
) -> list[str]:
    errors: list[str] = []
    if len(value) < schema.get("minItems", 0):
        errors.append(f"{path}: requires at least {schema['minItems']} item(s)")
    if "items" in schema:
        for index, item in enumerate(value):
            errors.extend(_schema_errors(item, schema["items"], f"{path}[{index}]", root))
    return errors


def _object_schema_errors(
    value: dict[str, Any], schema: dict[str, Any], path: str, root: dict[str, Any]
) -> list[str]:
    errors = [
        f"{path}: missing required property {key!r}"
        for key in schema.get("required", [])
        if key not in value
    ]
    properties = schema.get("properties", {})
    additional = schema.get("additionalProperties")
    if additional is False:
        errors.extend(f"{path}: unknown property {key!r}" for key in value if key not in properties)
    for key, child_schema in properties.items():
        if key in value:
            errors.extend(_schema_errors(value[key], child_schema, f"{path}.{key}", root))
    if isinstance(additional, dict):
        for key, child in value.items():
            if key not in properties:
                errors.extend(_schema_errors(child, additional, f"{path}.{key}", root))
    return errors


# Implements exactly the JSON Schema keywords schema/v1.json uses. Unknown
# keywords pass through unchecked; extend here before adding them to the
# schema file.
def _schema_errors(
    value: Any, schema: dict[str, Any], path: str = "$", root: dict[str, Any] | None = None
) -> list[str]:
    root = root or schema
    if "$ref" in schema:
        if not schema["$ref"].startswith("#/"):
            return [f"{path}: unsupported schema reference {schema['$ref']!r}"]
        return _schema_errors(value, _resolve_schema_ref(schema, root), path, root)

    errors: list[str] = []
    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: expected {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: unknown value {value!r}; expected one of {schema['enum']!r}")
    if "type" in schema and not _type_matches(value, schema["type"]):
        return [f"{path}: expected {schema['type']}, got {type(value).__name__}"]
    if isinstance(value, str) and len(value) < schema.get("minLength", 0):
        errors.append(f"{path}: must not be empty")
    if isinstance(value, list):
        errors.extend(_array_schema_errors(value, schema, path, root))
    if isinstance(value, dict):
        errors.extend(_object_schema_errors(value, schema, path, root))
    return errors


def _package_inventory() -> dict[str, Path]:
    packages = {"phlo": ROOT / "pyproject.toml"}
    for path in sorted((ROOT / "packages").glob("*/pyproject.toml")):
        with path.open("rb") as handle:
            packages[tomllib.load(handle)["project"]["name"]] = path
    return packages


def _package_owner_for_path(path: Path, packages: dict[str, Path]) -> str | None:
    """Return the most specific package whose root contains a discovered path."""
    resolved_path = path.resolve()
    candidates: list[tuple[int, str]] = []
    for package_name, pyproject_path in packages.items():
        package_root = pyproject_path.parent.resolve()
        try:
            resolved_path.relative_to(package_root)
        except ValueError:
            continue
        candidates.append((len(package_root.parts), _normalise_package_name(package_name)))
    return max(candidates)[1] if candidates else None


def _is_companion_service_yaml(filename: str) -> bool:
    """Mirror the runtime predicate for recursively loaded companion definitions."""
    return filename.endswith(("-setup.yaml", "-daemon.yaml"))


def _top_level_yaml_name(path: Path) -> str | None:
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.fullmatch(r"name:\s*['\"]?([^'\"#]+?)['\"]?\s*", line)
        if match:
            return match.group(1).strip()
    return None


def _service_image_reference(path: Path) -> str | None:
    """Return the literal image or build reference declared by a service definition."""
    text = path.read_text(encoding="utf-8")
    image = re.search(r"^image:\s*(\S+)\s*$", text, re.MULTILINE)
    if image:
        return re.sub(r"\$\{[^:}]+:-([^}]+)\}", r"\1", image.group(1))
    context = re.search(r"^\s+context:\s*(\S+)\s*$", text, re.MULTILINE)
    dockerfile = re.search(r"^\s+dockerfile:\s*(\S+)\s*$", text, re.MULTILINE)
    if context and dockerfile:
        return f"build:context={context.group(1)};dockerfile={dockerfile.group(1)}"
    return None


def _registered_service_plugins(pyproject_path: Path) -> dict[str, str]:
    with pyproject_path.open("rb") as handle:
        project = tomllib.load(handle)["project"]
    return project.get("entry-points", {}).get("phlo.plugins.services", {})


def _add_discovered_service(
    services: dict[str, Path], package_services: set[str], name: str, path: Path
) -> None:
    if name in services:
        raise ValidationError(f"duplicate discovered service {name!r}: {services[name]} and {path}")
    services[name] = path
    package_services.add(name)


def _service_inventory_for_root(repo_root: Path) -> dict[str, Path]:
    """Discover runtime filenames plus frozen, explicitly verified plugin primaries."""
    services: dict[str, Path] = {}
    for pyproject_path in sorted((repo_root / "packages").glob("*/pyproject.toml")):
        registered_plugins = _registered_service_plugins(pyproject_path)
        registered_names = set(registered_plugins)
        if not registered_plugins:
            continue
        package_services: set[str] = set()

        source_roots = {
            pyproject_path.parent / "src" / target.split(":", 1)[0].split(".", 1)[0]
            for target in registered_plugins.values()
        }
        primary_paths = {source_root / "service.yaml" for source_root in source_roots}
        for path in sorted(primary_paths):
            if not path.is_file():
                continue
            name = _top_level_yaml_name(path)
            if name is not None:
                _add_discovered_service(services, package_services, name, path)

        for path in sorted(
            path for source_root in source_roots for path in source_root.rglob("*.yaml")
        ):
            if path in primary_paths or not _is_companion_service_yaml(path.name):
                continue
            name = _top_level_yaml_name(path)
            if name is not None:
                _add_discovered_service(services, package_services, name, path)

        overrides = SERVICE_PRIMARY_OVERRIDES.get(pyproject_path.parent.name, {})
        unknown_overrides = set(overrides) - registered_names
        if unknown_overrides:
            names = ", ".join(repr(name) for name in sorted(unknown_overrides))
            raise ValidationError(
                f"service primary override(s) {names} have no matching registered plugin"
            )
        for name, relative_path in sorted(overrides.items()):
            path = pyproject_path.parent / relative_path
            if not path.is_file():
                raise ValidationError(f"service primary override {name!r} does not exist: {path}")
            yaml_name = _top_level_yaml_name(path)
            if yaml_name != name:
                raise ValidationError(
                    f"service primary override {name!r} has YAML name {yaml_name!r}"
                )
            _add_discovered_service(services, package_services, name, path)

        undiscovered_plugins = registered_names - package_services
        if undiscovered_plugins:
            names = ", ".join(repr(name) for name in sorted(undiscovered_plugins))
            raise ValidationError(
                f"registered service plugin(s) {names} have no runtime-recognized YAML definition"
            )
    return services


def _service_inventory() -> dict[str, Path]:
    return _service_inventory_for_root(ROOT)


def _package_names_from_requirement(values: list[str]) -> set[str]:
    names = set()
    for value in values:
        match = re.match(r"([A-Za-z0-9][A-Za-z0-9_.-]*)", value)
        if match:
            names.add(match.group(1).lower().replace("_", "-"))
    return names


def _normalise_package_name(name: str) -> str:
    return name.lower().replace("_", "-")


def _resolve_repo_path(repo_root: Path, raw_path: str) -> tuple[Path | None, str | None]:
    """Resolve a manifest path and reject absolute, traversing, or escaping paths."""
    candidate = Path(raw_path)
    if candidate.is_absolute():
        return None, "path must be relative to repo_root"
    if ".." in candidate.parts:
        return None, "path must not contain '..'"

    resolved_root = repo_root.resolve()
    resolved_path = (repo_root / candidate).resolve()
    try:
        resolved_path.relative_to(resolved_root)
    except ValueError:
        return None, "path resolves outside repo_root"
    return resolved_path, None


def _markdown_anchor_exists(path: Path, anchor: str) -> bool:
    if path.suffix.lower() not in {".md", ".markdown"}:
        return True
    slugs: set[str] = set()
    slug_counts: dict[str, int] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^#+\s+(.+?)\s*#*\s*$", line)
        if not match:
            continue
        heading = re.sub(r"[`*_]", "", match.group(1)).lower()
        base_slug = re.sub(r"[^a-z0-9]+", "-", heading).strip("-")
        occurrence = slug_counts.get(base_slug, 0)
        slug_counts[base_slug] = occurrence + 1
        slug = base_slug if occurrence == 0 else f"{base_slug}-{occurrence}"
        slugs.add(slug)
    return anchor in slugs


def _validate_evidence(
    errors: list[str],
    *,
    kind: str,
    name: str,
    evidence_entries: list[str],
    repo_root: Path,
) -> None:
    for evidence in evidence_entries:
        evidence_path = evidence.split("#", 1)[0]
        if not evidence_path:
            errors.append(f"{kind} {name!r}: evidence path must not be empty")
            continue
        full_evidence_path, path_error = _resolve_repo_path(repo_root, evidence_path)
        if path_error:
            errors.append(f"{kind} {name!r}: evidence path {evidence_path!r} {path_error}")
            continue
        assert full_evidence_path is not None
        if not full_evidence_path.exists():
            errors.append(f"{kind} {name!r}: evidence path does not exist: {evidence_path}")
        elif "#" in evidence:
            if full_evidence_path.suffix.lower() not in {".md", ".markdown"}:
                errors.append(
                    f"{kind} {name!r}: evidence fragments are unsupported for {evidence_path}"
                )
            elif not _markdown_anchor_exists(full_evidence_path, evidence.split("#", 1)[1]):
                errors.append(f"{kind} {name!r}: evidence anchor does not exist: {evidence}")


def _registry_core_claims(repo_root: Path) -> tuple[set[str], set[str]]:
    core_packages: set[str] = set()
    core_services: set[str] = set()
    for relative_path in REGISTRY_RELATIVE_PATHS:
        path = repo_root / relative_path
        data = json.loads(path.read_text(encoding="utf-8"))
        for plugin_name, plugin in data.get("plugins", {}).items():
            if plugin.get("core") is True:
                core_packages.add(_normalise_package_name(plugin["package"]))
                if plugin.get("type") == "service":
                    core_services.add(plugin_name)
    return core_packages, core_services


def _validate_named_claim_bindings(
    capabilities: list[dict[str, Any]], *, repo_root: Path
) -> list[str]:
    """Bind named claims to committed source, tests, and workflow commands."""
    errors: list[str] = []
    by_name = {entry["name"]: entry for entry in capabilities}
    for claim_name, binding in NAMED_CLAIM_BINDINGS.items():
        entry = by_name.get(claim_name)
        if entry is None:
            errors.append(f"named claim {claim_name!r} is absent from capabilities")
            continue
        evidence = set(entry["evidence"])
        for required in binding.get("required_evidence", []):
            if required not in evidence:
                errors.append(f"capability {claim_name!r}: evidence must include {required!r}")
        for forbidden in binding.get("forbidden_evidence", []):
            if forbidden in evidence:
                errors.append(f"capability {claim_name!r}: evidence must not include {forbidden!r}")
        reason = entry["reason"]
        for phrase in binding.get("forbidden_reason_phrases", []):
            if phrase in reason:
                errors.append(
                    f"capability {claim_name!r}: reason must not contain stale phrase {phrase!r}"
                )
        for workflow_path, command in binding.get("workflow_commands", {}).items():
            workflow = repo_root / workflow_path
            if not workflow.is_file():
                errors.append(
                    f"capability {claim_name!r}: workflow {workflow_path!r} does not exist"
                )
            elif command not in workflow.read_text(encoding="utf-8"):
                errors.append(
                    f"capability {claim_name!r}: workflow {workflow_path!r} does not invoke {command!r}"
                )
    return errors


_EXPECTED_MATURITY = {
    "supported": "alpha",
    "preview": "preview",
    "experimental": "preview",
    "development_only": "development_only",
    "planned": "planned",
    "required": "planned",
    "excluded": "unverified",
}


class _ManifestContext(NamedTuple):
    """Parsed manifest sections and repository inventories shared by the validators."""

    manifest: dict[str, Any]
    repo_root: Path
    package_inventory: dict[str, Path]
    service_inventory: dict[str, Path]
    root_project: dict[str, Any]

    @property
    def packages(self) -> list[dict[str, Any]]:
        return self.manifest["packages"]

    @property
    def services(self) -> list[dict[str, Any]]:
        return self.manifest["services"]

    @property
    def capabilities(self) -> list[dict[str, Any]]:
        return self.manifest["capabilities"]

    @property
    def manifest_packages(self) -> dict[str, dict[str, Any]]:
        return {_normalise_package_name(entry["name"]): entry for entry in self.packages}

    @property
    def manifest_services(self) -> dict[str, dict[str, Any]]:
        return {entry["name"]: entry for entry in self.services}


def _component_ids(
    kind: str, entries: list[dict[str, Any]], predicate: Callable[[dict[str, Any]], bool]
) -> set[str]:
    return {f"{kind}:{entry['name']}" for entry in entries if predicate(entry)}


def _blessed_core_components(ctx: _ManifestContext) -> set[str]:
    def blessed(entry: dict[str, Any]) -> bool:
        return entry["scope"] == "blessed_core"

    return (
        _component_ids("package", ctx.packages, blessed)
        | _component_ids("service", ctx.services, blessed)
        | _component_ids("capability", ctx.capabilities, blessed)
    )


def _evidence_errors(
    *, kind: str, name: str, evidence_entries: list[str], repo_root: Path
) -> list[str]:
    errors: list[str] = []
    _validate_evidence(
        errors, kind=kind, name=name, evidence_entries=evidence_entries, repo_root=repo_root
    )
    return errors


def _approved_set_errors(ctx: _ManifestContext) -> list[str]:
    capability_names = {entry["name"] for entry in ctx.capabilities}
    profile_name_set = set(ctx.manifest["profiles"])
    errors = [
        f"approved v1 capability {name!r} is absent from the manifest"
        for name in sorted(APPROVED_V1_CAPABILITIES - capability_names)
    ]
    errors.extend(
        f"capability {name!r} is not in the approved v1 capability set"
        for name in sorted(capability_names - APPROVED_V1_CAPABILITIES)
    )
    errors.extend(
        f"approved profile {name!r} is absent from the manifest"
        for name in sorted(set(APPROVED_PROFILES) - profile_name_set)
    )
    errors.extend(
        f"profile {name!r} is not in the approved profile set"
        for name in sorted(profile_name_set - set(APPROVED_PROFILES))
    )
    errors.extend(
        f"profile {name!r} status must be {APPROVED_PROFILES[name]!r}"
        for name in sorted(profile_name_set & set(APPROVED_PROFILES))
        if ctx.manifest["profiles"][name]["status"] != APPROVED_PROFILES[name]
    )
    return errors


def _component_entry_errors(kind: str, entry: dict[str, Any], repo_root: Path) -> list[str]:
    errors: list[str] = []
    name = entry["name"]
    status = entry["target_status"]
    if status == "supported" and entry["scope"] == "outside_v1":
        errors.append(f"{kind} {name!r}: supported target cannot be outside_v1")
    if status == "excluded" and entry["scope"] != "outside_v1":
        errors.append(f"{kind} {name!r}: excluded target must be outside_v1")
    allowed_maturity = {_EXPECTED_MATURITY[status]}
    if kind == "capability" and status == "required":
        allowed_maturity = {"planned", "blocked"}
    if entry["current_maturity"] not in allowed_maturity:
        errors.append(f"{kind} {name!r}: current_maturity contradicts target_status")
    if kind != "capability" and status == "required":
        errors.append(f"{kind} {name!r}: only capabilities may be required targets")
    if kind == "capability" and name in REQUIRED_V1_CAPABILITIES and status != "required":
        errors.append(
            f"capability {name!r}: required v1 capability must use target_status=required"
        )
    errors.extend(
        _evidence_errors(
            kind=kind, name=name, evidence_entries=entry["evidence"], repo_root=repo_root
        )
    )
    return errors


def _component_errors(ctx: _ManifestContext) -> list[str]:
    errors: list[str] = []
    for kind, entries in (
        ("package", ctx.packages),
        ("service", ctx.services),
        ("capability", ctx.capabilities),
    ):
        names = [entry["name"] for entry in entries]
        duplicates = sorted({name for name in names if names.count(name) > 1})
        errors.extend(f"duplicate {kind} entry {name!r}" for name in duplicates)
        for entry in entries:
            errors.extend(_component_entry_errors(kind, entry, ctx.repo_root))
    return errors


def _section_evidence_errors(ctx: _ManifestContext) -> list[str]:
    errors: list[str] = []
    for runtime_name, runtime_entry in ctx.manifest["runtime"].items():
        errors.extend(
            _evidence_errors(
                kind="runtime",
                name=runtime_name,
                evidence_entries=runtime_entry["evidence"],
                repo_root=ctx.repo_root,
            )
        )
    errors.extend(
        _evidence_errors(
            kind="production",
            name="production",
            evidence_entries=ctx.manifest["production"]["evidence"],
            repo_root=ctx.repo_root,
        )
    )
    for exclusion in ctx.manifest["exclusions"]:
        errors.extend(
            _evidence_errors(
                kind="exclusion",
                name=exclusion["name"],
                evidence_entries=exclusion["evidence"],
                repo_root=ctx.repo_root,
            )
        )
    return errors


def _inventory_drift_errors(ctx: _ManifestContext) -> list[str]:
    manifest_packages = set(ctx.manifest_packages)
    actual_packages = {_normalise_package_name(name) for name in ctx.package_inventory}
    manifest_services = set(ctx.manifest_services)
    actual_services = set(ctx.service_inventory)
    errors = [
        f"package {name!r} is present in the workspace but absent from the support manifest"
        for name in sorted(actual_packages - manifest_packages)
    ]
    errors.extend(
        f"package {name!r} is in the support manifest but absent from the workspace"
        for name in sorted(manifest_packages - actual_packages)
    )
    errors.extend(provider_core_compatibility_errors(ctx.repo_root))
    errors.extend(
        f"service {name!r} is present in generated service metadata but absent from the support manifest"
        for name in sorted(actual_services - manifest_services)
    )
    errors.extend(
        f"service {name!r} is in the support manifest but absent from generated service metadata"
        for name in sorted(manifest_services - actual_services)
    )
    return errors


def _service_owner_errors(ctx: _ManifestContext, entry: dict[str, Any]) -> list[str]:
    discovered_source = ctx.service_inventory.get(entry["name"])
    if discovered_source is None:
        return []
    owner = _package_owner_for_path(discovered_source, ctx.package_inventory)
    if owner is None:
        return [f"service {entry['name']!r}: discovered source has no package owner"]
    if _normalise_package_name(entry["package"]) != owner:
        return [
            f"service {entry['name']!r}: package {entry['package']!r} does not own discovered source; expected {owner!r}"
        ]
    return []


def _service_entry_errors(ctx: _ManifestContext, entry: dict[str, Any]) -> list[str]:
    source, path_error = _resolve_repo_path(ctx.repo_root, entry["source"])
    if path_error:
        return [f"service {entry['name']!r}: source path {entry['source']!r} {path_error}"]
    assert source is not None
    errors: list[str] = []
    if not source.is_file():
        errors.append(f"service {entry['name']!r}: source does not exist: {entry['source']}")
    elif (
        entry["name"] in ctx.service_inventory
        and ctx.service_inventory[entry["name"]].resolve() != source
    ):
        errors.append(f"service {entry['name']!r}: source does not match discovered metadata")
    errors.extend(_service_owner_errors(ctx, entry))
    if _normalise_package_name(entry["package"]) not in ctx.manifest_packages:
        errors.append(
            f"service {entry['name']!r}: package {entry['package']!r} is absent from the package manifest"
        )
    return errors


def _release_set_package_errors(ctx: _ManifestContext) -> list[str]:
    release_set = ctx.manifest["release_set"]
    errors: list[str] = []
    release_package_names = [entry["name"] for entry in release_set["packages"]]
    if len(release_package_names) != len(set(release_package_names)):
        errors.append("release_set.packages contains duplicate package names")
    release_packages = {entry["name"]: entry["version"] for entry in release_set["packages"]}
    blessed_packages = {
        _normalise_package_name(entry["name"])
        for entry in ctx.packages
        if entry["scope"] == "blessed_core"
    }
    if set(release_packages) != blessed_packages:
        errors.append("release_set.packages must cover exactly the blessed_core packages")
    for package, version in release_packages.items():
        path = ctx.package_inventory.get(package)
        if path is None:
            continue
        with path.open("rb") as handle:
            declared_version = tomllib.load(handle)["project"]["version"]
        if version != declared_version:
            errors.append(
                f"release_set package {package!r} version {version!r} does not match {path.relative_to(ctx.repo_root)}"
            )
    return errors


def _release_set_service_errors(ctx: _ManifestContext) -> list[str]:
    release_set = ctx.manifest["release_set"]
    errors: list[str] = []
    release_service_names = [entry["name"] for entry in release_set["services"]]
    if len(release_service_names) != len(set(release_service_names)):
        errors.append("release_set.services contains duplicate service names")
    release_services = {
        entry["name"]: entry["image_reference"] for entry in release_set["services"]
    }
    blessed_services = {entry["name"] for entry in ctx.services if entry["scope"] == "blessed_core"}
    if set(release_services) != blessed_services:
        errors.append("release_set.services must cover exactly the blessed_core services")
    for service, image_reference in release_services.items():
        source = ctx.service_inventory.get(service)
        if source is None:
            continue
        if image_reference != _service_image_reference(source):
            errors.append(
                f"release_set service {service!r} image_reference does not match declared metadata"
            )
    return errors


def _release_set_schema_errors(ctx: _ManifestContext) -> list[str]:
    schemas = ctx.manifest["release_set"]["schemas"]
    config_schema = schemas["configuration"]
    database_schema = schemas["database"]
    config_source = ctx.repo_root / config_schema["source"]
    database_source = ctx.repo_root / database_schema["source"]
    errors: list[str] = []
    if not config_source.is_file():
        errors.append("release_set configuration source does not exist")
    elif not re.search(
        rf'^CONFIG_SCHEMA_VERSION\s*=\s*["\']{re.escape(config_schema["version"])}["\']\s*$',
        config_source.read_text(encoding="utf-8"),
        re.MULTILINE,
    ):
        errors.append("release_set configuration version does not match CONFIG_SCHEMA_VERSION")
    if not database_source.is_file():
        errors.append("release_set database source does not exist")
    elif not re.search(
        rf"^RUN_EVIDENCE_SCHEMA_VERSION\s*=\s*{re.escape(database_schema['version'])}\s*$",
        database_source.read_text(encoding="utf-8"),
        re.MULTILINE,
    ):
        errors.append("release_set database version does not match RUN_EVIDENCE_SCHEMA_VERSION")
    return errors


def _registry_core_errors(ctx: _ManifestContext) -> list[str]:
    # Registry ``core`` marks discovery/defaulting roles, not release maturity.
    core_packages, core_services = _registry_core_claims(ctx.repo_root)
    manifest_packages = ctx.manifest_packages
    manifest_services = ctx.manifest_services
    errors = [
        f"registry marks package {package!r} core but the support manifest has no entry"
        for package in sorted(core_packages)
        if package not in manifest_packages
    ]
    errors.extend(
        f"registry marks service {service!r} core but the support manifest has no entry"
        for service in sorted(core_services)
        if service not in manifest_services
    )
    return errors


def _published_extra_errors(ctx: _ManifestContext) -> list[str]:
    errors: list[str] = []
    if ctx.manifest["current_release"]["version"] != ctx.root_project["project"]["version"]:
        errors.append("current_release.version must match pyproject.toml")
    optional = ctx.root_project["project"].get("optional-dependencies", {})
    manifest_packages = ctx.manifest_packages
    for extra_name in ("defaults", "core-services"):
        for package in sorted(_package_names_from_requirement(optional.get(extra_name, []))):
            entry = manifest_packages.get(package)
            if entry is None:
                errors.append(
                    f"published extra {extra_name!r} advertises package {package!r} absent from the support manifest"
                )
            elif not (entry["scope"] == "blessed_core" and entry["target_status"] == "supported"):
                errors.append(
                    f"published extra {extra_name!r} advertises a non-core target package {package!r}"
                )
    return errors


def _expected_profile(ctx: _ManifestContext, profile_name: str) -> set[str] | None:
    """Return the components a derived profile must hold, or None for free-form profiles."""
    if profile_name == "blessed_core":
        return _blessed_core_components(ctx)
    predicates: dict[str, Callable[[dict[str, Any]], bool]] = {
        "supported_optional": lambda entry: (
            entry["scope"] == "optional" and entry["target_status"] == "supported"
        ),
        "preview": lambda entry: entry["target_status"] in {"preview", "experimental"},
        "development_only": lambda entry: entry["target_status"] == "development_only",
    }
    predicate = predicates.get(profile_name)
    if predicate is None:
        return None
    return _component_ids("package", ctx.packages, predicate) | _component_ids(
        "service", ctx.services, predicate
    )


def _profile_errors(ctx: _ManifestContext) -> list[str]:
    def every(_entry: dict[str, Any]) -> bool:
        return True

    profile_names = (
        _component_ids("package", ctx.packages, every)
        | _component_ids("service", ctx.services, every)
        | _component_ids("capability", ctx.capabilities, every)
    )
    errors: list[str] = []
    for profile_name, profile in ctx.manifest["profiles"].items():
        actual_profile = set(profile["components"])
        expected_profile = _expected_profile(ctx, profile_name)
        if expected_profile is not None and actual_profile != expected_profile:
            errors.append(
                f"profile {profile_name!r} membership does not match component scope/status"
            )
        errors.extend(
            f"profile {profile_name!r} references unknown component {component!r}"
            for component in sorted(actual_profile - profile_names)
        )
    return errors


def _gate_component_errors(
    entry: dict[str, Any], gate_status: dict[str, str], repo_root: Path
) -> list[str]:
    name = entry["name"]
    errors = _evidence_errors(
        kind="gate component",
        name=name,
        evidence_entries=entry.get("evidence", []),
        repo_root=repo_root,
    )
    known_gates = set(gate_status)
    applicable = set(entry["applicable_gates"])
    unknown_gates = applicable - known_gates
    if unknown_gates:
        errors.append(
            f"gate component {name!r} names unknown applicable gates: {sorted(unknown_gates)!r}"
        )
    blocked_by = {gate for gate in applicable & known_gates if gate_status[gate] != "passed"}
    if not applicable:
        errors.append(f"gate component {name!r} has no applicable gates")
    if set(entry["blocked_by"]) != blocked_by:
        errors.append(
            f"gate component {name!r} blocked_by does not derive from applicable gate state"
        )
    expected_status = "passed" if not blocked_by else "blocked"
    if entry["status"] != expected_status:
        errors.append(f"gate component {name!r} status does not derive from applicable gate state")
    if entry["status"] == "passed" and not entry.get("evidence"):
        errors.append(f"gate component {name!r} is passed without checked evidence")
    return errors


def _gate_errors(ctx: _ManifestContext) -> list[str]:
    gates = ctx.manifest["gates"]
    errors: list[str] = []
    gate_names = [entry["name"] for entry in gates["components"]]
    if len(gate_names) != len(set(gate_names)):
        errors.append("gates.components contains duplicate component names")
    if set(gate_names) != _blessed_core_components(ctx):
        errors.append(
            "gates.components must cover exactly every blessed_core package, service, and capability"
        )
    if set(gates["required"]) != set(gates["status"]):
        errors.append("gates.required and gates.status must name the same gates")
    for entry in gates["components"]:
        errors.extend(_gate_component_errors(entry, gates["status"], ctx.repo_root))
    gate_states = set(gates["status"].values())
    expected_production_status = (
        "blocked" if gate_states != {"passed"} else "required_before_release"
    )
    if ctx.manifest["production"]["status"] != expected_production_status:
        errors.append("production.status must be derived from the release-gate states")
    return errors


def _python_runtime_errors(ctx: _ManifestContext) -> list[str]:
    python_runtime = ctx.manifest["runtime"]["python"]
    supported_python = set(python_runtime["supported"])
    advertised_unverified = set(python_runtime["advertised_unverified"])
    unverified_python = set(python_runtime["unverified"])
    errors: list[str] = []
    if (
        supported_python & advertised_unverified
        or supported_python & unverified_python
        or advertised_unverified & unverified_python
    ):
        errors.append(
            "runtime Python supported, advertised_unverified, and unverified sets must be disjoint"
        )
    classifiers = set(ctx.root_project["project"].get("classifiers", []))
    classifier_prefix = "Programming Language :: Python :: "
    advertised_python = {
        classifier.removeprefix(classifier_prefix)
        for classifier in classifiers
        if classifier.startswith(classifier_prefix)
        and classifier.removeprefix(classifier_prefix) != "3"
    }
    if advertised_python != supported_python | advertised_unverified:
        errors.append(
            "runtime Python supported and advertised_unverified claims must exactly match pyproject classifiers"
        )
    errors.extend(
        f"runtime Python claim {version!r} is absent from pyproject classifiers"
        for version in supported_python | advertised_unverified
        if f"{classifier_prefix}{version}" not in classifiers
    )
    ci_text = (ctx.repo_root / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    errors.extend(
        f"runtime Python claim {version!r} has no CI matrix evidence"
        for version in supported_python
        if f'"{version}"' not in ci_text
    )
    return errors


def validate_manifest(manifest: dict[str, Any], *, repo_root: Path = ROOT) -> list[str]:
    """Return all manifest and repository consistency errors."""
    schema_path = repo_root / "registry/support/schema/v1.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    errors = _schema_errors(manifest, schema)
    if errors:
        return errors

    ctx = _ManifestContext(
        manifest=manifest,
        repo_root=repo_root,
        package_inventory=(
            _package_inventory() if repo_root == ROOT else _package_inventory_at(repo_root)
        ),
        service_inventory=(
            _service_inventory() if repo_root == ROOT else _service_inventory_at(repo_root)
        ),
        root_project=_load_root_project(repo_root),
    )
    errors.extend(_approved_set_errors(ctx))
    errors.extend(_component_errors(ctx))
    errors.extend(_validate_named_claim_bindings(ctx.capabilities, repo_root=repo_root))
    errors.extend(_section_evidence_errors(ctx))
    errors.extend(_inventory_drift_errors(ctx))
    for entry in ctx.services:
        errors.extend(_service_entry_errors(ctx, entry))
    errors.extend(_release_set_package_errors(ctx))
    errors.extend(_release_set_service_errors(ctx))
    errors.extend(_release_set_schema_errors(ctx))
    errors.extend(_registry_core_errors(ctx))
    errors.extend(_published_extra_errors(ctx))
    errors.extend(_profile_errors(ctx))
    errors.extend(_gate_errors(ctx))
    errors.extend(_python_runtime_errors(ctx))
    return errors


def _load_root_project(repo_root: Path) -> dict[str, Any]:
    with (repo_root / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)


def _package_inventory_at(repo_root: Path) -> dict[str, Path]:
    packages = {"phlo": repo_root / "pyproject.toml"}
    for path in sorted((repo_root / "packages").glob("*/pyproject.toml")):
        with path.open("rb") as handle:
            packages[tomllib.load(handle)["project"]["name"]] = path
    return packages


def _service_inventory_at(repo_root: Path) -> dict[str, Path]:
    return _service_inventory_for_root(repo_root)


def main() -> int:
    """Validate the support manifest and exit nonzero on failure."""
    try:
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        errors = validate_manifest(manifest)
    except (OSError, ValueError, KeyError, tomllib.TOMLDecodeError) as exc:
        print(f"support manifest validation error: {exc}", file=sys.stderr)
        return 1
    if errors:
        print("support manifest validation failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print(f"validated {MANIFEST_PATH.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
