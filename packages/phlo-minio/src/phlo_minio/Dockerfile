FROM golang:1.26.9-alpine@sha256:cdfd4fe2da6b225d8b40c6b7a105736e548e83ff56d5d8f9394446eeb5eb84e0 AS build

SHELL ["/bin/ash", "-o", "pipefail", "-c"]
WORKDIR /src
ENV CGO_ENABLED=0

# Source archives are immutable inputs, not downloads of a floating binary.
RUN wget -q -O minio.tar.gz https://codeload.github.com/minio/minio/tar.gz/refs/tags/RELEASE.2025-10-15T17-29-55Z \
    && echo 'be6d0bd3696c3a13a35f02d3a0280b64319c67918b4501c5c3d87f96d000085c  minio.tar.gz' | sha256sum -c - \
    && wget -q -O mc.tar.gz https://codeload.github.com/minio/mc/tar.gz/refs/tags/RELEASE.2025-08-13T08-35-41Z \
    && echo '29db22500374169a43951c7cef09daf19e7291ea5ba00ac10f321371b0a35b32  mc.tar.gz' | sha256sum -c - \
    && mkdir minio mc /out /licenses /sources \
    && tar -xzf minio.tar.gz -C minio --strip-components=1 \
    && tar -xzf mc.tar.gz -C mc --strip-components=1

WORKDIR /src/minio
# Pinned dependency fixes for the archived upstream release. The resulting
# go.mod, go.sum, and vendor tree are included in the corresponding source.
# go get ./... resolves local packages against that module graph, not @latest.
# hadolint ignore=DL3062
RUN go get github.com/apache/thrift@v0.24.0 github.com/buger/jsonparser@v1.1.2 \
        github.com/go-jose/go-jose/v4@v4.1.4 github.com/prometheus/prometheus@v0.311.3 \
        github.com/rabbitmq/amqp091-go@v1.13.0 go.opentelemetry.io/otel/sdk@v1.44.0 \
        golang.org/x/crypto@v0.55.0 golang.org/x/net@v0.58.0 \
        golang.org/x/text@v0.41.0 google.golang.org/grpc@v1.83.2 \
    && go get ./... \
    && go mod vendor \
    && go build -mod=vendor -trimpath -tags kqueue -ldflags '-s -w -X github.com/minio/minio/cmd.CopyrightYear=2025 -X github.com/minio/minio/cmd.Version=2025-10-15T17:29:55Z -X github.com/minio/minio/cmd.ReleaseTag=RELEASE.2025-10-15T17-29-55Z -X github.com/minio/minio/cmd.CommitID=9e49d5e7a648f00e26f2246f4dc28e6b07f8c84a -X github.com/minio/minio/cmd.ShortCommitID=9e49d5e7a648' -o /out/minio . \
    && cp LICENSE /licenses/minio-LICENSE \
    && cp CREDITS /licenses/minio-CREDITS \
    && tar -czf /sources/minio.tar.gz .

WORKDIR /src/mc
# hadolint ignore=DL3062
RUN go get github.com/prometheus/prometheus@v0.311.3 \
        golang.org/x/crypto@v0.55.0 golang.org/x/net@v0.58.0 \
        golang.org/x/text@v0.41.0 google.golang.org/grpc@v1.83.2 \
    && go get ./... \
    && go mod vendor \
    && go build -mod=vendor -trimpath -ldflags '-s -w -X github.com/minio/mc/cmd.CopyrightYear=2025 -X github.com/minio/mc/cmd.Version=2025-08-13T08:35:41Z -X github.com/minio/mc/cmd.ReleaseTag=RELEASE.2025-08-13T08-35-41Z -X github.com/minio/mc/cmd.CommitID=7394ce0dd2a80935aded936b09fa12cbb3cb8096 -X github.com/minio/mc/cmd.ShortCommitID=7394ce0dd2a8' -o /out/mc . \
    && cp LICENSE /licenses/mc-LICENSE \
    && cp NOTICE /licenses/mc-NOTICE \
    && cp CREDITS /licenses/mc-CREDITS \
    && tar -czf /sources/mc.tar.gz .

COPY . /context
RUN if [ -f /context/Dockerfile ]; then \
        cp /context/Dockerfile /sources/Dockerfile; \
    else \
        cp /context/src/phlo_minio/Dockerfile /sources/Dockerfile; \
    fi \
    && printf '%s\n' \
        'Phlo modified the MinIO and mc dependency manifests and vendored dependencies on 2026-10-08.' \
        'These are Phlo builds, not official MinIO binaries. See Dockerfile for the dependency changes.' \
        > /sources/PHLO-NOTICE

FROM alpine:3.24.1@sha256:28bd5fe8b56d1bd048e5babf5b10710ebe0bae67db86916198a6eec434943f8b AS runtime

LABEL org.opencontainers.image.source="https://github.com/phlohouse/phlo" \
    org.opencontainers.image.licenses="AGPL-3.0-or-later" \
    org.opencontainers.image.title="Phlo MinIO community server and client"

# hadolint ignore=DL3018
RUN apk upgrade --no-cache \
    && apk add --no-cache ca-certificates curl \
    && adduser -D -u 1001 -G root -h /home/minio minio \
    && mkdir -p /bitnami/minio/data \
    && chown -R 1001:0 /bitnami/minio /home/minio

COPY --from=build /out/ /usr/local/bin/
COPY --from=build /licenses/ /usr/share/licenses/minio/
# Include corresponding source with vendored dependencies alongside the binaries.
COPY --from=build /sources/ /usr/share/minio-source/

ENV HOME=/home/minio
USER 1001:0
EXPOSE 9000 9001
ENTRYPOINT ["minio"]
CMD ["server", "/bitnami/minio/data", "--console-address", ":9001"]
