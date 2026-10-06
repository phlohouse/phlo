# Repository guidance

Run `make setup` before repository-wide checks. Run `make check` for the
baseline and use the [verification matrix](docs/contributing/verification-matrix.md)
to select focused and broader checks for each changed surface.

Use the root [Makefile](Makefile) for canonical commands. Python type-check
scope and rule settings live in [pyproject.toml](pyproject.toml); complexity
thresholds and exceptions are documented in [CONTRIBUTING.md](CONTRIBUTING.md).
The verification matrix describes checks that `make check` does not cover.
