.PHONY: build test lint verify stack verify-stack

build:
	uv run source-to-report

test:
	uv run pytest

lint:
	uv run ruff check .

verify:
	uv run python scripts/verify.py

stack:
	uv run phlo services init --force --no-dev
	uv run phlo services start --build
	uv run phlo doctor
	uv run phlo materialize --select "*" --partition 2025-12-31

verify-stack:
	uv run python scripts/verify_stack.py
