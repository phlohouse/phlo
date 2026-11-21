# phlo-clickstack

ClickStack observability service for Phlo.

## Overview

`phlo-clickstack` packages the official ClickStack all-in-one image so Phlo can
query ClickHouse-backed observability data and link to the HyperDX UI.

## Installation

```bash
uv add phlo-clickstack
# or
phlo plugin install clickstack
```

## Profile

Part of the `observability` profile.

## Usage

```bash
phlo services start --service clickstack
phlo clickstack query "SELECT count() FROM default.otel_logs"
```

The bundled all-in-one image exposes the HyperDX UI and ClickHouse query ports.
Use Alloy or another collector for OTLP ingest; this service does not publish
OTLP ports by default because the image does not listen on those ports.
