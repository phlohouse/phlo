# ADR 0034: Migrate to ty

## Status

**Accepted**

## Context

When this decision was proposed, Phlo used basedpyright for Python type
checking. The project already used Astral's Ruff and uv, and ty offered a
type-checking tool from the same toolchain. The migration also required
replacing basedpyright's configuration and mapping its disabled rules to ty.

## Decision

Phlo adopted ty as its Python type checker. The migration removed basedpyright,
added ty configuration, updated CI, and scoped checks across the core and
workspace packages.

## Consequences

- Developers use `uv run --locked ty check` for Python type checks.
- `make check` runs ty with the repository's warning policy and configured
  source scope.
- The project no longer maintains a basedpyright configuration or runs both
  checkers in parallel.

The original proposal compared checker speed, diagnostics, gradual typing, and
editor support. Those were reasons to evaluate ty, not measured guarantees for
this repository.

## Verification

The current commands are defined in the root [Makefile](../../../Makefile) and
the type-check configuration lives in [pyproject.toml](../../../pyproject.toml).
Run `make typecheck-python` or `make check` to verify the current setup.

## Related

- [ty documentation](https://docs.astral.sh/ty/)
- [ADR 0006: Public API and Structured Logging](0006-public-api-and-structured-logging.md)
