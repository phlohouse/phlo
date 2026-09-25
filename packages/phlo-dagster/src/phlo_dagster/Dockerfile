# Debian (glibc) is required, not Alpine (musl): ingestion providers such as
# phlo-sling execute external binaries that upstream publishes glibc-only. The
# Sling CLI fails under musl even with gcompat (fcntl64 relocation missing).
FROM python:3.12-slim AS phlo-build-context

WORKDIR /opt/phlo-build-context

COPY . .
# Placeholder lock metadata keeps non-uv builds working: generated stacks for
# projects without pyproject.toml/uv.lock copy empty files and retain the
# existing PyPI installation path.
RUN mkdir -p /opt/phlo-build-context/wheelhouse \
    && touch /opt/phlo-build-context/pyproject.toml /opt/phlo-build-context/uv.lock

FROM python:3.12-slim AS runtime

WORKDIR /opt/dagster

ARG PHLO_VERSION=""
ARG PHLO_DBT_VERSION=""
ARG PHLO_DAGSTER_VERSION=""
ARG PHLO_WHEELHOUSE=""
# Set to "true" by `phlo services init` for uv-managed projects (both
# pyproject.toml and uv.lock present) so the image installs the locked
# dependency set instead of resolving a fresh graph at build time.
ARG PHLO_UV_LOCKED=""

# Install system dependencies and uv.
# gosu performs the entrypoint privilege drop.
# No C/C++/Rust toolchain: every pinned Python dependency ships manylinux
# wheels on glibc, so source builds are not expected in this image.
# Debian packages stay unpinned: the archive drops superseded versions at every
# trixie point release, so an exact version becomes a hard build failure for
# consumers running `phlo services start --build` weeks after the phlo release.
# The floating base tag and `apt-get upgrade` below already resolve the current
# point release, so pins cost nothing but breakage.
# hadolint ignore=DL3008
RUN apt-get update \
    && apt-get upgrade --no-install-recommends --yes \
    && apt-get install --yes --no-install-recommends \
        bash \
        ca-certificates \
        curl \
        git \
        gosu \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir "uv==0.12.5"

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# Copy only the artifact directory from the context stage; normal generated builds receive an
# empty directory and retain the existing PyPI installation path.
COPY --from=phlo-build-context /opt/phlo-build-context/wheelhouse /opt/phlo-wheelhouse

# Project lock metadata staged into the build context by `phlo services init`;
# empty placeholders keep non-uv builds unchanged.
COPY --from=phlo-build-context /opt/phlo-build-context/pyproject.toml \
    /opt/phlo-build-context/uv.lock /tmp/project/

# uv-managed projects install the locked dependency set, matching the versions
# `uv sync --locked --no-dev` produces in the repository and in CI. A stale or
# missing lockfile must fail the build rather than silently resolving an
# alternative dependency graph. `--no-install-project` keeps the project source
# out of the image because the project is bind-mounted at /app at runtime; the
# entrypoint installs the mounted project into this venv.
RUN if [ "$PHLO_UV_LOCKED" = "true" ]; then \
        test -s /tmp/project/pyproject.toml || { \
            echo "PHLO_UV_LOCKED=true but pyproject.toml is missing or empty in the build context; run 'phlo services init' to stage project lock metadata" >&2; \
            exit 1; \
        }; \
        test -s /tmp/project/uv.lock || { \
            echo "PHLO_UV_LOCKED=true but uv.lock is missing or empty in the build context; run 'phlo services init' to stage project lock metadata" >&2; \
            exit 1; \
        }; \
        UV_PROJECT_ENVIRONMENT=/opt/phlo-project-venv \
            UV_PYTHON=/usr/local/bin/python \
            uv sync --locked --no-dev --no-install-project --directory /tmp/project; \
    fi

# Lock-aware builds resolve everything else from the project lock, but the
# Dagster Postgres storage backend is an image runtime requirement (see
# templates/dagster.yaml), not a project dependency: install it explicitly,
# mirroring the non-lock path below. Without it the webserver fails to start
# with "Couldn't import module dagster_postgres".
# SQLAlchemy <2.1 is pinned here and below: 2.1 switched the postgresql://
# dialect from psycopg2 to psycopg3, whose `with conn:` closes the connection,
# breaking dagster-postgres's set_pg_statement_timeout hook (every pooled
# connection is returned closed and the webserver dies on its first query).
RUN if [ "$PHLO_UV_LOCKED" = "true" ]; then \
        uv pip install --python /opt/phlo-project-venv/bin/python dagster-postgres "psycopg[binary]" "sqlalchemy>=2.0,<2.1"; \
    fi

ENV PATH="/opt/phlo-project-venv/bin:${PATH}"

# Lock-aware builds take their entire runtime dependency set from the staged
# uv.lock, including phlo itself; the PyPI/wheelhouse resolution below must not
# run for them. Phlo currently pins the unpublished observe packages by URL;
# uv requires transitive URL dependencies to be repeated as direct inputs, so
# the non-lock path reads those requirements from the installed Phlo wheel.
RUN if [ "$PHLO_UV_LOCKED" != "true" ]; then \
    PHLO_DBT_REQUIREMENT="phlo-dbt"; \
    if [ -n "$PHLO_DBT_VERSION" ]; then PHLO_DBT_REQUIREMENT="phlo-dbt==$PHLO_DBT_VERSION"; fi; \
    PHLO_DAGSTER_REQUIREMENT="phlo-dagster"; \
    if [ -n "$PHLO_DAGSTER_VERSION" ]; then PHLO_DAGSTER_REQUIREMENT="phlo-dagster==$PHLO_DAGSTER_VERSION"; fi; \
    if [ -n "$PHLO_VERSION" ]; then \
        if [ -n "$PHLO_WHEELHOUSE" ]; then \
            test -d /opt/phlo-wheelhouse; \
            uv pip install --system --no-index --no-deps --reinstall --find-links /opt/phlo-wheelhouse "phlo==$PHLO_VERSION"; \
        else \
            uv pip install --system --no-deps --prerelease explicit "phlo==$PHLO_VERSION"; \
        fi; \
    else \
        uv pip install --system --no-deps phlo; \
    fi; \
    python -c 'import importlib.metadata as md; print("\n".join(r.split(";", 1)[0].strip() for r in (md.metadata("phlo").get_all("Requires-Dist") or []) if " @ " in r))' > /tmp/phlo-url-requirements.txt; \
    if [ -n "$PHLO_VERSION" ]; then \
        if [ -n "$PHLO_WHEELHOUSE" ]; then \
            uv pip install --system --prerelease explicit --find-links /opt/phlo-wheelhouse --requirement /tmp/phlo-url-requirements.txt "phlo[defaults]==$PHLO_VERSION" "$PHLO_DBT_REQUIREMENT" dagster-webserver dagster-postgres "psycopg[binary]" "sqlalchemy>=2.0,<2.1"; \
            uv pip install --system --no-index --no-deps --reinstall --find-links /opt/phlo-wheelhouse "phlo==$PHLO_VERSION"; \
            if [ -n "$PHLO_DBT_VERSION" ]; then uv pip install --system --no-index --no-deps --reinstall --find-links /opt/phlo-wheelhouse "$PHLO_DBT_REQUIREMENT"; fi; \
            uv pip install --system --no-index --no-deps --reinstall --find-links /opt/phlo-wheelhouse "$PHLO_DAGSTER_REQUIREMENT"; \
        else \
            PHLO_PRERELEASE_REQUIREMENTS="$(python -c 'import importlib.metadata as md, re; print(" ".join(req.split(";")[0].strip() for req in (md.metadata("phlo").get_all("Requires-Dist") or []) if "extra == '\''defaults'\''" in req and re.search(r"(a|b|rc|dev)[0-9]+", req)))')"; \
            base_requirements=("phlo[defaults]==$PHLO_VERSION" "$PHLO_DBT_REQUIREMENT" "dbt-core<1.12" dagster-webserver dagster-postgres "psycopg[binary]" "sqlalchemy>=2.0,<2.1"); \
            if [ -n "$PHLO_PRERELEASE_REQUIREMENTS" ]; then read -r -a prerelease_requirements <<< "$PHLO_PRERELEASE_REQUIREMENTS"; base_requirements+=("${prerelease_requirements[@]}"); fi; \
            uv pip install --system --prerelease explicit --requirement /tmp/phlo-url-requirements.txt "${base_requirements[@]}"; \
        fi; \
    else \
        uv pip install --system --requirement /tmp/phlo-url-requirements.txt "phlo[defaults]" "$PHLO_DBT_REQUIREMENT" "dbt-core<1.12" dagster-webserver dagster-postgres "psycopg[binary]" "sqlalchemy>=2.0,<2.1"; \
    fi \
    && uv pip install --system --requirement /tmp/phlo-url-requirements.txt "$PHLO_DAGSTER_REQUIREMENT" "PyJWT[crypto]>=2.13.0" "cryptography>=48.0.1" \
    && if [ -n "$PHLO_WHEELHOUSE" ]; then \
        uv pip install --system --no-index --no-deps --reinstall --find-links /opt/phlo-wheelhouse "phlo==$PHLO_VERSION" "$PHLO_DAGSTER_REQUIREMENT"; \
    fi; \
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
# bootstrap stage.
# hadolint ignore=DL3002
USER root

EXPOSE 3000

ENTRYPOINT ["/usr/local/bin/phlo-dagster-entrypoint.sh"]
CMD ["dagster-webserver", "-h", "0.0.0.0", "-p", "3000"]
