"""Acceptance evidence records — ``phlo.observatory-acceptance/v1``.

Every scenario test writes one JSON record per the acceptance runbook schema
(``docs/observatory/acceptance-runbook.md``): commit, sanitized config digest,
fixture identity, timestamps, real run/operation/release ids, per-check
verdicts, artifact references, unsupported capabilities, and a final result.

Sanitization rules are structural, not best-effort greps: any env key whose
name contains PASSWORD/SECRET/TOKEN/CREDENTIAL/KEY/USERS or whose value embeds
a URL userinfo section is redacted before the digest is computed. Screenshots
and other artifacts are referenced by path, never embedded.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "phlo.observatory-acceptance/v1"

_SECRET_KEY_RE = re.compile(r"(PASSWORD|SECRET|TOKEN|CREDENTIAL|PRIVATE|_KEY|USERS)", re.I)
_USERINFO_RE = re.compile(r"://[^/\s:@]+:[^/\s@]+@")


def _redact(key: str, value: str) -> str:
    if _SECRET_KEY_RE.search(key) or _USERINFO_RE.search(value):
        return "<redacted>"
    return value


def sanitized_config_digest(
    phlo_yaml: str,
    env_layers: dict[str, str],
    authorization_files: dict[str, str],
) -> str:
    """SHA-256 over sanitized effective config — stable across port allocation."""
    sanitized_env = {k: _redact(k, v) for k, v in sorted(env_layers.items())}
    # Host ports are per-boot allocations; normalize them out so the digest
    # reflects configuration shape, not the ephemeral port map.
    sanitized_env = {
        k: ("<port>" if k.endswith("_PORT") and v.isdigit() else v)
        for k, v in sanitized_env.items()
    }
    payload = {
        "phlo_yaml": phlo_yaml,
        "env": sanitized_env,
        "authorization": authorization_files,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def repo_commit() -> str:
    """Current HEAD commit, with a dirty marker when the tree has changes."""
    root = Path(__file__).resolve().parents[2]
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    ).stdout.strip()
    dirty = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    ).stdout.strip()
    return f"{sha}{'-dirty' if dirty else ''}" or "unknown"


@dataclass
class Check:
    name: str
    status: str  # passed | failed | skipped | blocked
    evidence: str = ""


@dataclass
class AcceptanceReport:
    """One scenario's acceptance record."""

    scenario: str
    artifact_root: Path
    commit: str
    project_id: str
    fixture: str
    config_digest: str
    started_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    ids: dict[str, Any] = field(default_factory=dict)
    checks: list[Check] = field(default_factory=list)
    artifacts: list[dict[str, str]] = field(default_factory=list)
    unsupported: list[dict[str, str]] = field(default_factory=list)
    finished_at: str = ""
    result: str = "blocked"

    def check(self, name: str, status: str, evidence: str = "") -> None:
        self.checks.append(Check(name=name, status=status, evidence=evidence))

    def passed(self, name: str, evidence: str = "") -> None:
        self.check(name, "passed", evidence)

    def failed(self, name: str, evidence: str = "") -> None:
        self.check(name, "failed", evidence)

    def skipped(self, name: str, evidence: str = "") -> None:
        self.check(name, "skipped", evidence)

    def blocked(self, name: str, evidence: str = "") -> None:
        self.check(name, "blocked", evidence)

    def add_artifact(self, kind: str, path: Path) -> None:
        self.artifacts.append({"kind": kind, "path": str(path)})

    def mark_unsupported(self, capability: str, reason: str) -> None:
        self.unsupported.append({"capability": capability, "reason": reason})

    def finalize(self) -> str:
        """Compute the final result from recorded checks."""
        self.finished_at = datetime.now(UTC).isoformat()
        if any(c.status == "failed" for c in self.checks):
            self.result = "failed"
        elif any(c.status == "blocked" for c in self.checks):
            self.result = "blocked"
        elif self.checks:
            self.result = "passed"
        return self.result

    def write(self) -> Path:
        """Serialize the record under <root>/<commit>/<scenario>.json."""
        if not self.finished_at:
            self.finalize()
        target_dir = self.artifact_root / self.commit
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{self.scenario}.json"
        target.write_text(
            json.dumps(
                {
                    "schema": SCHEMA_VERSION,
                    "commit": self.commit,
                    "config_digest": self.config_digest,
                    "project_id": self.project_id,
                    "fixture": self.fixture,
                    "started_at": self.started_at,
                    "finished_at": self.finished_at,
                    "scenario": self.scenario,
                    "ids": self.ids,
                    "checks": [
                        {"name": c.name, "status": c.status, "evidence": c.evidence}
                        for c in self.checks
                    ],
                    "artifacts": self.artifacts,
                    "unsupported": self.unsupported,
                    "result": self.result,
                },
                indent=2,
            )
            + "\n"
        )
        return target


__all__ = [
    "SCHEMA_VERSION",
    "AcceptanceReport",
    "Check",
    "repo_commit",
    "sanitized_config_digest",
]
