from __future__ import annotations

from importlib import resources
from pathlib import Path


def test_dagster_runtime_image_installs_prerelease_phlo_with_postgres_driver() -> None:
    dockerfile = resources.files("phlo_dagster").joinpath("Dockerfile").read_text()

    assert dockerfile.startswith("FROM python:3.12-alpine")
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
    assert "cargo=1.96.1-r0" in dockerfile
    assert "rust=1.96.1-r0" in dockerfile
    assert "su-exec=0.3-r0" in dockerfile


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
    assert "uv pip install --system -e /app" in entrypoint
    assert 'if [ "$(id -u)" -ne 0 ]; then' in entrypoint
    assert 'runtime_user="phlo"' in entrypoint
    assert "touch /tmp/phlo-dagster-ready" in entrypoint
    assert 'exec su-exec "$runtime_user" env HOME=/tmp "$@"' in entrypoint
    assert entrypoint.index("uv pip install --system -e /app") < entrypoint.index(
        'exec su-exec "$runtime_user" env HOME=/tmp "$@"'
    )


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
