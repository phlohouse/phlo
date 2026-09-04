# 12. Make Observatory Data Explorer branch-aware via path-based routing

Date: 2025-12-14

## Status

Accepted

## Context

Observatory’s Data Explorer must support browsing non-main branches (e.g., feature/dev branches) for
Iceberg/Nessie-backed data. Hardcoding `branch=main` breaks workflows and makes shareable URLs
misleading.

This decision is tracked and implemented by bead `phlo-nwk.1.2` (closed).

## Decision

- Represent the selected branch in the Data Explorer URL via a path segment (not UI-only state),
  enabling stable, shareable links.
- Treat the branch as an end-to-end parameter:
  - UI reads/writes the branch from the route.
  - Server endpoints accept an explicit branch and pass it through to the Trino/Nessie query layer.
- Default to `main` only when no branch is present in the route (back/forward compatible behavior
  for existing links).

## Consequences

- Branch selection becomes durable (reload-safe) and shareable.
- Server APIs become explicit about branch context, reducing accidental cross-branch reads.
- Route structure becomes part of the public UX contract and must remain stable.
