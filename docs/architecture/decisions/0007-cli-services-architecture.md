# 7. Refactor `phlo services` into testable, composable units

Date: 2025-12-14

## Status

Accepted

## Context

The `phlo services` CLI interacts with Docker Compose and local project state.
A monolithic implementation makes it hard to:

- Test behavior without invoking Docker.
- Provide consistent error handling and argument validation.
- Extend commands safely as the service catalog grows.

## Decision

Refactor services CLI logic into focused modules (command building, container selection, service
selection, command runner), and keep the Click/Typer command layer thin.

## Consequences

- Core logic becomes unit-testable and easier to reason about.
- Error messages are more consistent and actionable.
- Some internal module boundaries are introduced and must be maintained to avoid regressions.
