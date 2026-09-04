# ADR 0035: Package-Level Integration Tests

## Status

**Proposed**

## Context

Phlo packages are split across service plugins and library integrations. The recent plugin refactor
left integration test coverage uneven: some packages had no integration tests, while others were
tested only when specific services happened to be running. This made it difficult to verify that
all packages work after refactors, and caused integration runs to skip large portions of the
codebase without a clear signal.

We need consistent package-level integration tests that:

- Exercise each package at least once during integration runs.
- Validate service-backed packages via health endpoints when services are running.
- Allow local runs to skip cleanly when a dependency is not available.

## Decision

Add package-level integration tests across all packages, with the following rules:

1. **Service packages** (those with `service.yaml`) validate a health endpoint when the service is
   reachable. If the service is not reachable, the test skips with a clear message.
2. **Library packages** add lightweight integration smoke tests that validate imports and
   basic initialization paths without external services.
3. **Environment overrides** follow a consistent pattern:
   - Prefer `<SERVICE>_URL` when set.
   - Otherwise, use `<SERVICE>_HOST` (default `localhost`) and `<SERVICE>_PORT` (service default).
4. Integration tests remain **inside each package** to keep ownership aligned with plugin scope.

## Implementation

- Add `pytest.mark.integration` tests under `packages/<package>/tests/` for each package.
- Service health checks use lightweight HTTP probes (or TCP connect for PostgreSQL).
- Library packages use minimal smoke tests (e.g., import and constructor validation).

This standardizes integration coverage without imposing a full-stack requirement on every local run.
