# Debian (glibc) is required, not Alpine (musl): ingestion providers such as
# phlo-sling execute external binaries that upstream publishes glibc-only. The
# Sling CLI fails under musl even with gcompat (fcntl64 relocation missing).
FROM python:3.12-slim AS phlo-build-context

WORKDIR /opt/phlo-build-context

COPY . .
RUN mkdir -p /opt/phlo-build-context/wheelhouse

FROM python:3.12-slim

WORKDIR /opt/dagster

ARG PHLO_VERSION=""
ARG PHLO_DBT_VERSION=""
ARG PHLO_DAGSTER_VERSION=""
ARG PHLO_WHEELHOUSE=""

# Install system dependencies and uv.
# gosu performs the entrypoint privilege drop.
# No C/C++/Rust toolchain: every pinned Python dependency ships manylinux
# wheels on glibc, so source builds are not expected in this image.
RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        bash=5.2.37-2+b9 \
        ca-certificates=20250419 \
        curl=8.14.1-2+deb13u4 \
        git=1:2.47.3-0+deb13u1 \
        gosu=1.17-3+b4 \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir "uv==0.12.5"

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# Copy only the artifact directory from the context stage; normal generated builds receive an
# empty directory and retain the existing PyPI installation path.
COPY --from=phlo-build-context /opt/phlo-build-context/wheelhouse /opt/phlo-wheelhouse

# Install Phlo from the local wheelhouse when supplied; normal generated stacks retain PyPI behavior.
RUN \
    PHLO_DBT_REQUIREMENT="phlo-dbt"; \
    if [ -n "$PHLO_DBT_VERSION" ]; then PHLO_DBT_REQUIREMENT="phlo-dbt==$PHLO_DBT_VERSION"; fi; \
    PHLO_DAGSTER_REQUIREMENT="phlo-dagster"; \
    if [ -n "$PHLO_DAGSTER_VERSION" ]; then PHLO_DAGSTER_REQUIREMENT="phlo-dagster==$PHLO_DAGSTER_VERSION"; fi; \
    if [ -n "$PHLO_VERSION" ]; then \
    if [ -n "$PHLO_WHEELHOUSE" ]; then \
        test -d /opt/phlo-wheelhouse; \
        uv pip install --system --no-index --no-deps --reinstall --find-links /opt/phlo-wheelhouse "phlo==$PHLO_VERSION"; \
        uv pip install --system --prerelease explicit --find-links /opt/phlo-wheelhouse "phlo[defaults]==$PHLO_VERSION" "$PHLO_DBT_REQUIREMENT" dagster-webserver dagster-postgres "psycopg[binary]"; \
        uv pip install --system --no-index --no-deps --reinstall --find-links /opt/phlo-wheelhouse "phlo==$PHLO_VERSION"; \
        if [ -n "$PHLO_DBT_VERSION" ]; then uv pip install --system --no-index --no-deps --reinstall --find-links /opt/phlo-wheelhouse "$PHLO_DBT_REQUIREMENT"; fi; \
        uv pip install --system --no-index --no-deps --reinstall --find-links /opt/phlo-wheelhouse "$PHLO_DAGSTER_REQUIREMENT"; \
    else \
        uv pip install --system --no-deps --prerelease explicit "phlo==$PHLO_VERSION"; \
        PHLO_PRERELEASE_REQUIREMENTS="$(python -c 'import importlib.metadata as md, re; print(" ".join(req.split(";")[0].strip() for req in (md.metadata("phlo").get_all("Requires-Dist") or []) if "extra == '\''defaults'\''" in req and re.search(r"(a|b|rc|dev)[0-9]+", req)))')"; \
        base_requirements=("phlo[defaults]==$PHLO_VERSION" "$PHLO_DBT_REQUIREMENT" "dbt-core<1.12" dagster-webserver dagster-postgres "psycopg[binary]"); \
        if [ -n "$PHLO_PRERELEASE_REQUIREMENTS" ]; then read -r -a prerelease_requirements <<< "$PHLO_PRERELEASE_REQUIREMENTS"; base_requirements+=("${prerelease_requirements[@]}"); fi; \
        uv pip install --system --prerelease explicit "${base_requirements[@]}"; \
    fi; \
    else \
        uv pip install --system "phlo[defaults]" "$PHLO_DBT_REQUIREMENT" "dbt-core<1.12" dagster-webserver dagster-postgres "psycopg[binary]"; \
    fi \
    && uv pip install --system "$PHLO_DAGSTER_REQUIREMENT" "PyJWT[crypto]>=2.13.0" "cryptography>=48.0.1" \
    && if [ -n "$PHLO_WHEELHOUSE" ]; then \
        uv pip install --system --no-index --no-deps --reinstall --find-links /opt/phlo-wheelhouse "phlo==$PHLO_VERSION" "$PHLO_DAGSTER_REQUIREMENT"; \
    fi

# Build caches contain package manifests that vulnerability scanners treat as runtime
# dependencies. They are unnecessary after installation and must not ship in the image.
RUN rm -rf /root/.cache/uv /root/.cache/puccinialin /root/.cargo/registry

# Keep entrypoint outside /opt/dagster so dev volume mounts never hide it.
COPY dagster/entrypoint.sh /usr/local/bin/phlo-dagster-entrypoint.sh
RUN chmod +x /usr/local/bin/phlo-dagster-entrypoint.sh \
    && groupadd --system phlo \
    && useradd --system --gid phlo --no-create-home --home-dir /opt/dagster phlo \
    && chown -R phlo:phlo /opt/dagster

# Copy workspace configuration
COPY dagster/workspace.yaml /opt/dagster/workspace.yaml
COPY dagster/dagster.yaml /opt/dagster/dagster.yaml

# The entrypoint installs the mounted project, then drops to the phlo account.
# Keep the image startup user explicit so a base-image change cannot bypass that
# bootstrap phase.
# hadolint ignore=DL3002
USER root

EXPOSE 3000

ENTRYPOINT ["/usr/local/bin/phlo-dagster-entrypoint.sh"]
CMD ["dagster-webserver", "-h", "0.0.0.0", "-p", "3000"]
