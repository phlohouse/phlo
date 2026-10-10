#!/usr/bin/env python3
"""Build a unique GHCR publication matrix from rendered Compose JSON.

Emits one build target per unique published image; services sharing an image
collapse into a single target. Built services must publish a ghcr.io/phlohouse
image whose context and Dockerfile resolve inside the generated or source root,
otherwise matrix construction fails.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

_REMOTE_CONTEXT_PREFIXES = ("http://", "https://", "git@", "ssh://")
_COMPARABLE_KEYS = ("root", "context", "dockerfile", "build_args")


def _resolve_build_paths(
    service_name: str,
    build: dict[str, Any],
    roots: tuple[tuple[str, Path | None], ...],
) -> tuple[str, Path, Path]:
    """Return the root name and root-relative context and Dockerfile paths."""
    context = Path(str(build.get("context", ""))).resolve()
    dockerfile = Path(str(build.get("dockerfile", "Dockerfile")))
    dockerfile = dockerfile if dockerfile.is_absolute() else context / dockerfile
    for root_name, root in roots:
        if root is None:
            continue
        try:
            return (
                root_name,
                context.relative_to(root.resolve()),
                dockerfile.resolve().relative_to(root.resolve()),
            )
        except ValueError:
            continue
    raise ValueError(f"built service {service_name!r} escapes publication roots")


def _build_target(
    service_name: str,
    service: Any,
    roots: tuple[tuple[str, Path | None], ...],
) -> dict[str, Any] | None:
    """Return the publication target for one service, or None when it is not published."""
    if not isinstance(service, dict) or not service.get("build"):
        return None
    build = service["build"]
    if not isinstance(build, dict):
        raise ValueError(f"built service {service_name!r} has invalid build configuration")
    if str(build.get("context", "")).startswith(_REMOTE_CONTEXT_PREFIXES):
        # Remote-context builds compile third-party source into a
        # local-only image; they are not publishable phlo images.
        return None
    image = service.get("image")
    if not isinstance(image, str) or not image.startswith("ghcr.io/phlohouse/phlo-"):
        raise ValueError(f"built service {service_name!r} has no Phlo GHCR image")
    context_root, context_relative, dockerfile_relative = _resolve_build_paths(
        service_name, build, roots
    )
    # A digest-pinned reference still publishes under its repository tag.
    tag = image.split("@", 1)[0]
    return {
        "service": service_name,
        "services": [service_name],
        "image": tag,
        "root": context_root,
        "context": str(context_relative),
        "dockerfile": str(dockerfile_relative),
        "build_args": build.get("args") or {},
    }


def _select_targets(
    targets: list[dict[str, Any]], selected_services: set[str] | None
) -> list[dict[str, Any]]:
    """Keep targets publishing any selected service; reject unknown selections."""
    if not selected_services:
        return targets
    known_services = {service for target in targets for service in target["services"]}
    unknown_services = selected_services - known_services
    if unknown_services:
        unknown = ", ".join(sorted(unknown_services))
        raise ValueError(f"selected services are not published build services: {unknown}")
    return [target for target in targets if selected_services.intersection(target["services"])]


def publication_matrix(
    compose: dict[str, Any],
    project_root: Path,
    source_root: Path | None = None,
    selected_services: set[str] | None = None,
) -> dict[str, Any]:
    """Return one build target per unique published image."""
    services = compose.get("services")
    if not isinstance(services, dict):
        raise ValueError("Compose JSON has no services object")
    roots = (("generated", project_root), ("source", source_root))
    published: dict[str, dict[str, Any]] = {}
    for service_name, service in services.items():
        target = _build_target(service_name, service, roots)
        if target is None:
            continue
        tag = target["image"]
        existing = published.get(tag)
        # Several services may publish the same tag only when their build
        # definitions match exactly; otherwise the matrix would silently pick
        # whichever service appeared first.
        if existing is None:
            published[tag] = target
            continue
        if any(existing[key] != target[key] for key in _COMPARABLE_KEYS):
            raise ValueError(f"published image {tag!r} has conflicting build definitions")
        existing["services"].append(service_name)
    if not published:
        raise ValueError("Compose JSON has no published build services")
    return {"include": _select_targets(list(published.values()), selected_services)}


def main() -> int:
    """Parse CLI arguments, print the GitHub Actions matrix JSON, and exit 0."""
    parser = argparse.ArgumentParser()
    parser.add_argument("compose_json", type=Path)
    parser.add_argument("project_root", type=Path)
    parser.add_argument("source_root", type=Path)
    parser.add_argument("--services", default="")
    args = parser.parse_args()
    compose = json.loads(args.compose_json.read_text(encoding="utf-8"))
    print(
        json.dumps(
            publication_matrix(
                compose,
                args.project_root,
                args.source_root,
                {service.strip() for service in args.services.split(",") if service.strip()},
            ),
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
