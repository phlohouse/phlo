from __future__ import annotations

from importlib import resources
from pathlib import Path
import shutil
import subprocess

import pytest


def test_dagster_runtime_image_installs_prerelease_phlo_with_postgres_driver() -> None:
    dockerfile = resources.files("phlo_dagster").joinpath("Dockerfile").read_text()

    assert "FROM python:3.12-slim" in dockerfile
    assert (
        'uv pip install --system --no-deps --prerelease explicit "phlo==$PHLO_VERSION"'
        in dockerfile
    )
    assert "PHLO_PRERELEASE_REQUIREMENTS" in dockerfile
    assert (
        'base_requirements=("phlo[defaults]==$PHLO_VERSION" "$PHLO_DBT_REQUIREMENT"' in dockerfile
    )
    assert 'uv pip install --system --prerelease explicit "${base_requirements[@]}"' in dockerfile
    assert '"dbt-core<1.12"' in dockerfile
    assert 'dagster-postgres "psycopg[binary]"' in dockerfile
    assert (
        '"$PHLO_DAGSTER_REQUIREMENT" "PyJWT[crypto]>=2.13.0" "cryptography>=48.0.1"' in dockerfile
    )
    assert 'pip install --no-cache-dir "uv==0.12.5"' in dockerfile
    assert ("apt-get install --yes --no-install-recommends \\\n") in dockerfile
    assert "gosu=1.17-3+b4" in dockerfile
    assert "bash=5.2.37-2+b9" in dockerfile


def test_dagster_runtime_image_is_glibc_based_for_external_binaries() -> None:
    """Ingestion providers execute glibc-linked external binaries (Sling).

    Sling publishes no musl build and its binary fails under gcompat, so an
    Alpine base silently breaks every phlo.ingest.sling asset at runtime.
    """
    dockerfile = resources.files("phlo_dagster").joinpath("Dockerfile").read_text()

    assert "FROM python:3.12-slim" in dockerfile
    assert "apk" not in dockerfile
    assert "su-exec" not in dockerfile


def test_dagster_runtime_image_pins_dbt_when_the_provider_version_is_populated() -> None:
    dockerfile = resources.files("phlo_dagster").joinpath("Dockerfile").read_text()

    assert 'ARG PHLO_DBT_VERSION=""' in dockerfile
    assert 'ARG PHLO_DAGSTER_VERSION=""' in dockerfile
    assert (
        'if [ -n "$PHLO_DBT_VERSION" ]; then '
        'PHLO_DBT_REQUIREMENT="phlo-dbt==$PHLO_DBT_VERSION"; fi;'
    ) in dockerfile
    assert (
        'if [ -n "$PHLO_DBT_VERSION" ]; then uv pip install --system --no-index '
        "--no-deps --reinstall --find-links /opt/phlo-wheelhouse "
        '"$PHLO_DBT_REQUIREMENT"; fi;'
    ) in dockerfile
    assert (
        "uv pip install --system --no-index --no-deps --reinstall --find-links "
        '/opt/phlo-wheelhouse "phlo==$PHLO_VERSION" "$PHLO_DAGSTER_REQUIREMENT";'
    ) in dockerfile
    assert dockerfile.count('"$PHLO_DBT_REQUIREMENT"') == 4
    package_metadata = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text()
    assert '"phlo-dbt' not in package_metadata


def test_dagster_runtime_image_keeps_dbt_unpinned_when_provider_version_is_empty() -> None:
    dockerfile = resources.files("phlo_dagster").joinpath("Dockerfile").read_text()

    assert 'PHLO_DBT_REQUIREMENT="phlo-dbt";' in dockerfile
    assert '"phlo-dbt==$PHLO_DBT_VERSION" dagster-webserver' not in dockerfile
    assert (
        '"phlo[defaults]" "$PHLO_DBT_REQUIREMENT" "dbt-core<1.12" dagster-webserver' in dockerfile
    )
    assert 'PHLO_DAGSTER_REQUIREMENT="phlo-dagster";' in dockerfile


def test_dagster_runtime_image_removes_all_python_build_caches() -> None:
    dockerfile = resources.files("phlo_dagster").joinpath("Dockerfile").read_text()

    assert "rm -rf /root/.cache/uv /root/.cache/puccinialin" in dockerfile


def test_dagster_runtime_entrypoint_installs_mounted_project() -> None:
    entrypoint = resources.files("phlo_dagster").joinpath("entrypoint.sh").read_text()

    assert "if [ -f /app/pyproject.toml ]; then" in entrypoint
    assert 'name.startswith("phlo-")' in entrypoint
    assert 'local_path="/opt/phlo-dev/packages/$package"' in entrypoint
    assert "(cd /app && uv pip install --system -e .)" in entrypoint
    assert "uv pip install --system -e /app" not in entrypoint
    assert "--no-sources -e /app" not in entrypoint
    assert 'if [ "$(id -u)" -ne 0 ]; then' in entrypoint
    assert 'runtime_user="phlo"' in entrypoint
    assert "touch /tmp/phlo-dagster-ready" in entrypoint
    assert 'runtime_home="/var/lib/phlo-runtime"' in entrypoint
    assert 'mkdir -p "$runtime_home"' in entrypoint
    assert 'chown "$runtime_user" "$runtime_home"' in entrypoint
    assert 'exec gosu "$runtime_user" env HOME="$runtime_home" "$@"' in entrypoint
    assert entrypoint.index("(cd /app && uv pip install --system -e .)") < entrypoint.index(
        'exec gosu "$runtime_user" env HOME="$runtime_home" "$@"'
    )


def test_runtime_images_pin_current_uv_and_document_git_capability_override() -> None:
    """Runtime resolution preserves consumer sources with the current uv behavior."""
    dagster_dockerfile = resources.files("phlo_dagster").joinpath("Dockerfile").read_text()
    api_dockerfile = (
        Path(__file__).resolve().parents[2] / "phlo-api" / "src" / "phlo_api" / "Dockerfile"
    ).read_text()
    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text()

    assert 'pip install --no-cache-dir "uv==0.12.5"' in dagster_dockerfile
    assert '"uv==0.12.5"' in api_dockerfile
    assert 'override-dependencies = ["phlo[defaults]==0.14.0"]' in readme


def test_dagster_runtime_entrypoint_gives_an_unmapped_uid_an_isolated_writable_home(
    tmp_path: Path,
) -> None:
    """Root bootstrap must not leave runtime telemetry or logs owned by root."""
    if shutil.which("docker") is None:
        pytest.skip("Docker is required for the runtime ownership contract")

    docker_info = subprocess.run(["docker", "info"], capture_output=True, text=True)
    if docker_info.returncode:
        pytest.skip("Docker daemon is unavailable for the runtime ownership contract")

    entrypoint = resources.files("phlo_dagster").joinpath("entrypoint.sh")
    (tmp_path / "entrypoint.sh").write_text(entrypoint.read_text())
    (tmp_path / "Dockerfile").write_text(
        "\n".join(
            [
                "FROM python:3.12-slim",
                "RUN apt-get update && apt-get install --yes --no-install-recommends bash gosu",
                "RUN mkdir -p /opt/dagster",
                "COPY entrypoint.sh /usr/local/bin/phlo-dagster-entrypoint.sh",
                "RUN chmod +x /usr/local/bin/phlo-dagster-entrypoint.sh",
                'ENTRYPOINT ["/usr/local/bin/phlo-dagster-entrypoint.sh"]',
            ]
        )
    )
    image_tag = f"phlo-dagster-runtime-contract-{tmp_path.name}"
    build = subprocess.run(
        ["docker", "build", "--quiet", "--tag", image_tag, str(tmp_path)],
        capture_output=True,
        text=True,
    )
    assert build.returncode == 0, build.stderr

    try:
        runtime = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "-e",
                "PHLO_RUNTIME_UID=12345",
                "-e",
                "PHLO_RUNTIME_GID=23456",
                image_tag,
                "sh",
                "-ec",
                'test "$(id -u)" = 12345; '
                'test "$HOME" = /var/lib/phlo-runtime; '
                'mkdir -p "$HOME/.dagster"; '
                'touch "$HOME/.dagster/telemetry"; '
                "test -f /tmp/phlo-dagster-ready; "
                "printf runtime-log >> /tmp/phlo-20260819.log; "
                "test -w /tmp/phlo-20260819.log",
            ],
            capture_output=True,
            text=True,
        )
        assert runtime.returncode == 0, runtime.stderr
    finally:
        subprocess.run(["docker", "image", "rm", "--force", image_tag], capture_output=True)


def test_dagster_image_starts_as_root_for_bootstrap() -> None:
    dockerfile = resources.files("phlo_dagster").joinpath("Dockerfile").read_text()

    assert "USER root" in dockerfile


def test_dagster_runtime_entrypoint_exposes_mounted_dev_sources_to_python() -> None:
    """The unprivileged runtime can import local adapters without installing them."""
    entrypoint = resources.files("phlo_dagster").joinpath("entrypoint.sh").read_text()

    assert "for source_dir in /opt/phlo-dev/src /opt/phlo-dev/packages/*/src; do" in entrypoint
    assert 'export PYTHONPATH="$source_dir${PYTHONPATH:+:$PYTHONPATH}"' in entrypoint


def test_dagster_runtime_entrypoint_installs_only_requested_dev_packages() -> None:
    entrypoint = resources.files("phlo_dagster").joinpath("entrypoint.sh").read_text()

    assert 'local_path="/opt/phlo-dev/packages/$pkg"' in entrypoint
    assert "for pkg_dir in /opt/phlo-dev/packages/*" not in entrypoint
    assert "phlo-testing" not in entrypoint


def test_dagster_service_uses_the_image_bootstrap_script() -> None:
    """The DAGSTER_HOME bind mount must not hide the image bootstrap script."""
    service = resources.files("phlo_dagster").joinpath("service.yaml").read_text()

    assert 'entrypoint: ["/usr/local/bin/phlo-dagster-entrypoint.sh"]' in service
