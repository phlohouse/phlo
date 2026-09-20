FROM python:3.12-alpine AS phlo-build-context

WORKDIR /opt/phlo-build-context

COPY . .
RUN set -eux; \
    if [ -f entrypoint.sh ]; then entrypoint=entrypoint.sh; else entrypoint=phlo-api/entrypoint.sh; fi; \
    install -m 0755 "$entrypoint" /opt/phlo-build-context/phlo-api-entrypoint.sh; \
    mkdir -p /opt/phlo-build-context/wheelhouse

FROM python:3.12-alpine AS runtime

WORKDIR /app

ARG PHLO_VERSION=
ARG PHLO_API_VERSION=
ARG PHLO_WHEELHOUSE=

# Alpine packages stay unpinned: the branch archive drops superseded versions,
# so an exact version becomes a hard build failure for consumers running
# `phlo services start --build` weeks after the phlo release. The floating base
# tag and `apk upgrade` below already resolve the current branch versions.
# hadolint ignore=DL3018
RUN apk upgrade --no-cache \
    && apk add --no-cache ca-certificates \
    && apk add --no-cache --virtual .build-deps \
        gcc \
        musl-dev \
    && pip install --no-cache-dir \
        "setuptools==83.0.0" \
        "wheel==0.47.0" \
        "uv==0.12.5"

COPY --from=phlo-build-context /opt/phlo-build-context/wheelhouse /opt/phlo-wheelhouse

# Phlo currently pins the unpublished observe packages by URL. uv requires
# transitive URL dependencies to be repeated as direct installation inputs.
RUN set -eux; \
    PHLO_REQUIREMENT="phlo"; \
    if [ -n "$PHLO_VERSION" ]; then PHLO_REQUIREMENT="phlo==$PHLO_VERSION"; fi; \
    PHLO_API_REQUIREMENT="phlo-api"; \
    if [ -n "$PHLO_API_VERSION" ]; then PHLO_API_REQUIREMENT="phlo-api==$PHLO_API_VERSION"; fi; \
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
    fi && apk del .build-deps

# Do not retain uv's downloaded archives in the runtime image; they are not needed to run Phlo.
RUN rm -rf /root/.cache/uv

COPY --from=phlo-build-context /opt/phlo-build-context/phlo-api-entrypoint.sh /usr/local/bin/phlo-api-entrypoint.sh

RUN addgroup -S phlo \
    && adduser -S -G phlo -h /app -H phlo \
    && chown -R phlo:phlo /app

USER phlo

EXPOSE 4000

ENTRYPOINT ["/usr/local/bin/phlo-api-entrypoint.sh"]
CMD ["python", "-m", "uvicorn", "phlo_api.main:app", "--host", "0.0.0.0", "--port", "4000"]
