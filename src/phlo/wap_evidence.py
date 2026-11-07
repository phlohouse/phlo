"""Bounded, read-only ownership of persisted WAP launch evidence.

The caller supplies a project directory and observed run identity. No provider,
configuration discovery, migration or lifecycle writes happen here.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from phlo._attempt import normalize_attempt

_LIMIT = 2 * 1024 * 1024
_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_DIGEST = re.compile(r"^[a-f0-9]{64}$")
_Text = Annotated[str, Field(min_length=1)]


class _Context(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    environment: _Text
    target_ref: _Text
    location: _Text
    backend_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    strategy: Literal["branch", "snapshot"]
    require_candidate_bound_checks: bool = False


class _Binding(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    schema_version: Literal["phlo.wap_launch_manifest.v1", "phlo.wap_launch_manifest.v2"]
    logical_run_id: _Text
    dagster_run_id: str
    branch: _Text
    tags: dict[str, str]
    source_hash: _Text | None
    target_branch: _Text
    target_hash_before: _Text | None
    context: _Context | None = None


class _Report(BaseModel):
    model_config = ConfigDict(strict=True)
    schema_version: Literal["phlo.wap_report.v2"]
    run_id: _Text
    dagster_run_id: _Text
    branch: _Text
    launch_tags: dict[str, str]
    launch_manifest_checksum: str = Field(pattern=r"^[a-f0-9]{64}$")
    strategy: Literal["branch", "snapshot"]
    status: str = Field(min_length=1, max_length=128)
    updated_at: AwareDatetime
    context: _Context | None = None
    target_branch: _Text | None = None


@dataclass(frozen=True)
class VerifiedWapEvidence:
    """A report bound to the exact immutable launch, without changing either."""

    report: dict[str, Any]
    binding: dict[str, Any]
    reported_at: datetime

    @property
    def needs_hash_migration(self) -> bool:
        """Only the lifecycle writer may backfill these legacy report fields."""
        return "launch_source_hash" not in self.report


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError("Duplicate WAP evidence fields")
    return result


def _launch_tags(logical_id: str, tags: Mapping[str, str]) -> dict[str, str]:
    project = tags.get("phlo/project_id", "")
    attempt = tags.get("phlo/attempt", "")
    if (
        not project.strip()
        or project != project.strip()
        or str(normalize_attempt(attempt)) != attempt
    ):
        raise ValueError("Invalid WAP project or attempt")
    branch = f"pipeline-run-{logical_id}"
    expected = {
        "phlo/run_id": logical_id,
        "phlo/wap_branch": branch,
        "phlo/ref": branch,
        "phlo/project_id": project,
        "phlo/attempt": attempt,
    }
    for key in ("phlo/catalog_system", "environment", "phlo/wap_binding"):
        if key in tags:
            if not tags[key]:
                raise ValueError("Empty WAP launch tag")
            expected[key] = tags[key]
    return expected


def _routing_matches(binding: dict[str, Any], tags: Mapping[str, str]) -> bool:
    if any(tags.get(key, binding["branch"]) != binding["branch"] for key in ("ref", "branch")):
        return False
    context = binding.get("context")
    return context is None or all(
        tags.get(key, context["environment"]) == context["environment"]
        for key in ("environment", "env")
    )


class WapEvidence:
    """Read and verify a project's WAP records, including legacy launch hashes."""

    def __init__(self, project_root: Path):
        self.directory = project_root / ".phlo" / "wap-reports"

    def report_path(self, logical_id: str) -> Path:
        """Return the report path only for a safe logical identity."""
        if not _ID.fullmatch(logical_id):
            raise ValueError("Invalid WAP logical run ID")
        return self.directory / f"{logical_id}.json"

    def binding_path(self, logical_id: str, checksum: str) -> Path:
        """Locate a content-addressed launch without trusting caller paths."""
        return self._digest_path(logical_id, checksum, "launches")

    def snapshot_path(self, logical_id: str, checksum: str) -> Path:
        """Locate a content-addressed lifecycle snapshot."""
        return self._digest_path(logical_id, checksum, "evidence")

    def _digest_path(self, logical_id: str, checksum: str, kind: str) -> Path:
        self.report_path(logical_id)
        if not _DIGEST.fullmatch(checksum):
            raise ValueError("Invalid WAP evidence digest")
        run_key = hashlib.sha256(logical_id.encode("utf-8")).hexdigest()[:24]
        return self.directory / kind / f"{run_key}.{checksum}.json"

    @staticmethod
    def encode_report(payload: dict[str, Any]) -> bytes:
        """Serialize a lifecycle update only if the reader can consume its bytes."""
        raw = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
        if len(raw) > _LIMIT:
            raise ValueError("WAP evidence exceeds the read limit")
        return raw

    @staticmethod
    def _read(path: Path) -> bytes:
        with path.open("rb") as handle:
            raw = handle.read(_LIMIT + 1)
        if len(raw) > _LIMIT:
            raise ValueError("WAP evidence exceeds the read limit")
        return raw

    @staticmethod
    def _decode(raw: bytes) -> dict[str, Any]:
        value = json.loads(raw, object_pairs_hook=_object)
        if not isinstance(value, dict):
            raise ValueError("WAP evidence must be an object")
        return value

    def read_report(self, logical_id: str) -> dict[str, Any] | None:
        """Read lifecycle state, including incomplete reports, without writing it."""
        snapshot = self.read_report_snapshot(logical_id)
        return snapshot[1] if snapshot is not None else None

    def read_report_snapshot(
        self, logical_id: str, *, strict: bool = False
    ) -> tuple[bytes, dict[str, Any]] | None:
        """Read exact lifecycle bytes; strict reads distinguish absence from corruption.

        Writers must use strict reads rather than treating failed reads as empty
        reports. This method never creates or repairs evidence.
        """
        try:
            raw = self._read(self.report_path(logical_id))
            report = self._decode(raw)
        except (OSError, ValueError, RecursionError):
            if strict:
                raise
            return None
        if report.get("failure_reason") is None:
            report.pop("failure_reason", None)
        return raw, report

    def read_binding(self, logical_id: str, checksum: str) -> dict[str, Any] | None:
        """Verify a launch's bytes, schema, routing and immutable tag identity."""
        try:
            raw = self._read(self.binding_path(logical_id, checksum))
            if hashlib.sha256(raw).hexdigest() != checksum:
                return None
            binding = self._decode(raw)
            model = _Binding.model_validate(binding)
            expected_tags = _launch_tags(logical_id, model.tags)
            if (
                model.logical_run_id != logical_id
                or model.branch != f"pipeline-run-{logical_id}"
                or model.tags != expected_tags
            ):
                return None
            if model.schema_version == "phlo.wap_launch_manifest.v2":
                if model.context is None or model.target_branch != model.context.target_ref:
                    return None
                if (
                    model.tags.get("environment", model.context.environment)
                    != model.context.environment
                ):
                    return None
            elif "context" in binding or model.target_branch != "main":
                return None
            return binding
        except (OSError, ValueError, RecursionError):
            return None

    def read_execution_seal(
        self, logical_id: str, checksum: str, tags: Mapping[str, str], *, project_id: str
    ) -> dict[str, Any] | None:
        """Verify the prelaunch identity before waiting for physical acknowledgement."""
        binding = self.read_binding(logical_id, checksum)
        if binding is None or binding["dagster_run_id"] != "" or not binding.get("context"):
            return None
        try:
            expected = _launch_tags(logical_id, tags)
        except ValueError:
            return None
        if (
            expected != {**binding["tags"], "phlo/wap_binding": checksum}
            or any(tags.get(key) != value for key, value in expected.items())
            or expected["phlo/project_id"] != project_id
            or not _routing_matches(binding, tags)
        ):
            return None
        return binding

    def verify(
        self,
        logical_id: str,
        physical_id: str,
        tags: Mapping[str, str],
        *,
        project_id: str | None = None,
    ) -> VerifiedWapEvidence | None:
        """Attest report, launch and optional pre-execution seal as one identity.

        Both absent legacy launch-hash fields may be derived from the immutable
        binding. A partial pair or a differing recorded hash is never accepted.
        Extra runtime tags are allowed only when they do not redirect routing.
        """
        snapshot = self.read_report_snapshot(logical_id)
        if snapshot is None or not physical_id:
            return None
        raw, report = snapshot
        try:
            model = _Report.model_validate_json(raw)
            expected_tags = _launch_tags(logical_id, tags)
        except ValueError:
            return None
        binding = self.read_binding(logical_id, model.launch_manifest_checksum)
        if (
            binding is None
            or model.run_id != logical_id
            or model.dagster_run_id != physical_id
            or model.branch != binding["branch"]
            or binding["dagster_run_id"] != physical_id
            or model.launch_tags != expected_tags
            or binding["tags"] != expected_tags
            or any(tags.get(key) != value for key, value in expected_tags.items())
            or (project_id is not None and expected_tags["phlo/project_id"] != project_id)
        ):
            return None
        context = binding.get("context")
        if report.get("context") != context:
            return None
        if context is not None and (
            model.strategy != context["strategy"] or model.target_branch != context["target_ref"]
        ):
            return None
        if not _routing_matches(binding, tags):
            return None
        hash_fields = ("launch_source_hash", "launch_target_hash_before")
        present = [key in report for key in hash_fields]
        if any(present) and (
            not all(present)
            or report[hash_fields[0]] != binding["source_hash"]
            or report[hash_fields[1]] != binding["target_hash_before"]
        ):
            return None
        seal_checksum = tags.get("phlo/wap_binding")
        if seal_checksum is not None:
            seal = self.read_binding(logical_id, seal_checksum)
            if (
                seal is None
                or seal.get("dagster_run_id") != ""
                or binding
                != {
                    **seal,
                    "dagster_run_id": physical_id,
                    "tags": {**seal["tags"], "phlo/wap_binding": seal_checksum},
                }
            ):
                return None
        return VerifiedWapEvidence(report=report, binding=binding, reported_at=model.updated_at)
