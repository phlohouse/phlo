"""Tests for the generated GHCR publication matrix.

The matrix deduplicates services sharing one image/build, accepts build
contexts only from the project checkout or .phlo, rejects images that are not
published to GHCR, and fails on unknown selected services.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "generated_image_matrix", REPO_ROOT / "scripts" / "generated_image_matrix.py"
)
assert _spec and _spec.loader
generated_image_matrix = importlib.util.module_from_spec(_spec)
sys.modules["generated_image_matrix"] = generated_image_matrix
_spec.loader.exec_module(generated_image_matrix)


def test_publication_matrix_deduplicates_shared_images(tmp_path: Path) -> None:
    context = tmp_path / ".phlo"
    context.mkdir()
    dockerfile = context / "openmetadata" / "Dockerfile"
    dockerfile.parent.mkdir()
    dockerfile.touch()
    build = {
        "context": str(context),
        "dockerfile": "openmetadata/Dockerfile",
        "args": {"VERSION": "1.2.3"},
    }
    compose = {
        "services": {
            "openmetadata": {
                "image": "ghcr.io/phlohouse/phlo-openmetadata:1.2.3",
                "build": build,
            },
            "openmetadata-setup": {
                "image": "ghcr.io/phlohouse/phlo-openmetadata:1.2.3",
                "build": build,
            },
        }
    }

    assert generated_image_matrix.publication_matrix(compose, tmp_path) == {
        "include": [
            {
                "service": "openmetadata",
                "services": ["openmetadata", "openmetadata-setup"],
                "image": "ghcr.io/phlohouse/phlo-openmetadata:1.2.3",
                "root": "generated",
                "context": ".phlo",
                "dockerfile": ".phlo/openmetadata/Dockerfile",
                "build_args": {"VERSION": "1.2.3"},
            }
        ]
    }


def test_publication_matrix_rejects_unpublished_build(tmp_path: Path) -> None:
    compose = {
        "services": {
            "local": {
                "image": "local/image:1",
                "build": {"context": str(tmp_path)},
            }
        }
    }

    with pytest.raises(ValueError, match="has no Phlo GHCR image"):
        generated_image_matrix.publication_matrix(compose, tmp_path)


def test_publication_matrix_accepts_checked_out_source_context(tmp_path: Path) -> None:
    project = tmp_path / "project"
    source = tmp_path / "source"
    context = source / "packages" / "observatory"
    context.mkdir(parents=True)
    (context / "Dockerfile").touch()
    compose = {
        "services": {
            "observatory": {
                "image": "ghcr.io/phlohouse/phlo-observatory:0.7.0",
                "build": {"context": str(context), "dockerfile": "Dockerfile"},
            }
        }
    }

    target = generated_image_matrix.publication_matrix(compose, project, source)["include"][0]

    assert target["root"] == "source"
    assert target["context"] == "packages/observatory"


def test_publication_matrix_selects_only_requested_shared_image(tmp_path: Path) -> None:
    build = {"context": str(tmp_path), "dockerfile": "Dockerfile"}
    (tmp_path / "Dockerfile").touch()
    compose = {
        "services": {
            "dagster": {
                "image": "ghcr.io/phlohouse/phlo-dagster:1",
                "build": build,
            },
            "dagster-daemon": {
                "image": "ghcr.io/phlohouse/phlo-dagster:1",
                "build": build,
            },
            "postgres": {
                "image": "ghcr.io/phlohouse/phlo-postgres:1",
                "build": build,
            },
        }
    }

    matrix = generated_image_matrix.publication_matrix(
        compose,
        tmp_path,
        selected_services={"dagster-daemon"},
    )

    assert [target["service"] for target in matrix["include"]] == ["dagster"]


def test_publication_matrix_rejects_unknown_selected_service(tmp_path: Path) -> None:
    (tmp_path / "Dockerfile").touch()
    compose = {
        "services": {
            "postgres": {
                "image": "ghcr.io/phlohouse/phlo-postgres:1",
                "build": {"context": str(tmp_path)},
            }
        }
    }

    with pytest.raises(ValueError, match="unknown"):
        generated_image_matrix.publication_matrix(
            compose,
            tmp_path,
            selected_services={"unknown"},
        )
