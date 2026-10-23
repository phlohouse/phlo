# Native transform releases target GNU libc, matching the Dagster runtime.
FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1 AS phlo-build-context

WORKDIR /opt/phlo-build-context

ARG PHLO_TRANSFORM_BINARY=
ARG PHLO_TRANSFORM_SHA256=

COPY . .
RUN set -eux; \
    if [ -f entrypoint.sh ]; then entrypoint=entrypoint.sh; else entrypoint=phlo-api/entrypoint.sh; fi; \
    install -m 0755 "$entrypoint" /opt/phlo-build-context/phlo-api-entrypoint.sh; \
    mkdir -p /opt/phlo-build-context/wheelhouse /opt/phlo-native; \
    if [ -n "$PHLO_TRANSFORM_BINARY" ]; then \
        test -n "$PHLO_TRANSFORM_SHA256"; \
        printf '%s  %s\n' "$PHLO_TRANSFORM_SHA256" "$PHLO_TRANSFORM_BINARY" | sha256sum -c -; \
        install -m 0755 "$PHLO_TRANSFORM_BINARY" /opt/phlo-native/phlo-transform; \
    fi

FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1 AS runtime

WORKDIR /app

ARG PHLO_VERSION=
ARG PHLO_API_VERSION=
ARG PHLO_WHEELHOUSE=
ARG PHLO_API_EXTRAS=

ENV PHLO_API_EXTRAS=${PHLO_API_EXTRAS}

RUN python -c 'import os; extras = set(filter(None, os.environ["PHLO_API_EXTRAS"].split(","))); unknown = extras - {"dagster", "dlt", "iceberg", "lineage", "transform", "trino"}; assert not unknown, f"Unsupported API provider extras: {sorted(unknown)}"'

# Match the existing GNU-native provider runtime rather than relying on musl shims.
# hadolint ignore=DL3008
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates gcc libc6-dev \
    && pip install --no-cache-dir \
        "setuptools==83.0.0" \
        "wheel==0.47.0" \
        "uv==0.13.4"

COPY --from=phlo-build-context /opt/phlo-build-context/wheelhouse /opt/phlo-wheelhouse

# Phlo currently pins the unpublished observe packages by URL. uv requires
# transitive URL dependencies to be repeated as direct installation inputs.
# Keep the if block outside an && chain, which would disable set -e inside it.
RUN set -eux; \
    PHLO_REQUIREMENT="phlo"; \
    if [ -n "$PHLO_VERSION" ]; then PHLO_REQUIREMENT="phlo==$PHLO_VERSION"; fi; \
    PHLO_API_REQUIREMENT="phlo-api"; \
    if [ -n "${PHLO_API_EXTRAS:-}" ]; then PHLO_API_REQUIREMENT="phlo-api[$PHLO_API_EXTRAS]"; fi; \
    if [ -n "$PHLO_API_VERSION" ]; then PHLO_API_REQUIREMENT="$PHLO_API_REQUIREMENT==$PHLO_API_VERSION"; fi; \
    if [ -n "$PHLO_WHEELHOUSE" ]; then \
        test -d /opt/phlo-wheelhouse; \
        uv pip install --system --no-index --no-deps --reinstall --find-links /opt/phlo-wheelhouse "$PHLO_REQUIREMENT" "$PHLO_API_REQUIREMENT"; \
        python -c 'import importlib.metadata as md; print("\n".join(r.split(";", 1)[0].strip() for r in (md.metadata("phlo").get_all("Requires-Dist") or []) if " @ " in r))' > /tmp/phlo-url-requirements.txt; \
        uv pip install --system --prerelease explicit --find-links /opt/phlo-wheelhouse --requirement /tmp/phlo-url-requirements.txt "$PHLO_REQUIREMENT" "$PHLO_API_REQUIREMENT"; \
        uv pip install --system --no-index --no-deps --reinstall --find-links /opt/phlo-wheelhouse "$PHLO_REQUIREMENT" "$PHLO_API_REQUIREMENT"; \
    elif [ -n "$PHLO_VERSION" ]; then \
        uv pip install --system --no-deps --prerelease explicit "$PHLO_REQUIREMENT"; \
        python -c 'import importlib.metadata as md; print("\n".join(r.split(";", 1)[0].strip() for r in (md.metadata("phlo").get_all("Requires-Dist") or []) if " @ " in r))' > /tmp/phlo-url-requirements.txt; \
        uv pip install --system --prerelease explicit --requirement /tmp/phlo-url-requirements.txt "$PHLO_REQUIREMENT" "$PHLO_API_REQUIREMENT"; \
    else \
        uv pip install --system --no-deps phlo; \
        python -c 'import importlib.metadata as md; print("\n".join(r.split(";", 1)[0].strip() for r in (md.metadata("phlo").get_all("Requires-Dist") or []) if " @ " in r))' > /tmp/phlo-url-requirements.txt; \
        uv pip install --system --requirement /tmp/phlo-url-requirements.txt phlo "$PHLO_API_REQUIREMENT"; \
    fi

# Do not retain uv's downloaded archives in the runtime image; they are not needed to run Phlo.
RUN apt-get purge -y --auto-remove gcc libc6-dev \
    && rm -rf /var/lib/apt/lists/* /root/.cache/uv

COPY --from=phlo-build-context /opt/phlo-build-context/phlo-api-entrypoint.sh /usr/local/bin/phlo-api-entrypoint.sh
COPY --from=phlo-build-context /opt/phlo-native/ /usr/local/bin/

# Reject an unusable approved binary in the final runtime, not on the first save.
RUN if [ -f /usr/local/bin/phlo-transform ]; then phlo-transform --version; fi

RUN groupadd --system phlo \
    && useradd --system --gid phlo --home-dir /app --no-create-home phlo \
    && chown -R phlo:phlo /app

USER phlo

EXPOSE 4000

ENTRYPOINT ["/usr/local/bin/phlo-api-entrypoint.sh"]
CMD ["python", "-m", "uvicorn", "phlo_api.main:app", "--host", "0.0.0.0", "--port", "4000"]
