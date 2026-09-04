# Root Makefile — maintainer dev-tooling interface only (check, lint, test,
# docs). Project lifecycle (services up/down/logs) lives in the `phlo` CLI:
# `phlo services init|start|stop|logs`. There is no root compose.yaml, so
# Compose targets were removed rather than left to run against nothing.
SHELL := /bin/bash
OBSERVATORY_DIR ?= packages/phlo-observatory/src/phlo_observatory
NPM_OBSERVATORY := npm --prefix $(OBSERVATORY_DIR)
TY_CHECK_SCOPE := src/phlo $(wildcard packages/*/src)
CHECK_CMD := scripts/run-parallel \
	"support manifest" "python3 scripts/validate_support_manifest.py" \
	"version drift" "python3 scripts/check_version_drift.py" \
	"py lint" "uv run --locked ruff check ." \
	"py format" "uv run --locked ruff format --check ." \
	"py typecheck" "uv run --locked ty check --error-on-warning $(TY_CHECK_SCOPE)" \
	"py test" "uv run --locked pytest -m 'not integration'" \
	"ts lint" "$(NPM_OBSERVATORY) run lint" \
	"ts format" "$(NPM_OBSERVATORY) run format -- --check ." \
	"ts typecheck" "$(NPM_OBSERVATORY) exec tsc -- -p $(OBSERVATORY_DIR)/tsconfig.json --noEmit"
CORE_REGRESSION_TEST_PATHS ?= tests
CORE_REGRESSION_PYTEST_ARGS ?= --tb=short
QUICKSTART_SMOKE_PYTEST_ARGS ?= --tb=short
LANE ?= all
PYMDX_DOCS_DIR ?= docs-site
PYMDX_DOCS_PORT ?= 3000

.PHONY: setup install test \
	dagster superset hub minio pgweb trino nessie grafana prometheus api hasura openmetadata catalog docs-open \
	check lint lint-sql lint-python format-python typecheck-python \
	dependency-refresh dependency-refresh-check \
	validate-support-manifest \
	lint-ts format-ts typecheck-ts test-core-regression test-quickstart-smoke fix-sql \
	prek-install prek-run prek-validate zizmor actionlint docs-generate docs-dev docs-build docs-serve docs-clean

setup: venv install

venv:
	uv venv

install:
	uv sync --locked

test:
	uv run --locked pytest

test-core-regression:
	uv run --locked pytest $(CORE_REGRESSION_TEST_PATHS) -m core_regression $(CORE_REGRESSION_PYTEST_ARGS)

test-quickstart-smoke:
	uv run --locked pytest tests/cli/test_quickstart_smoke.py $(QUICKSTART_SMOKE_PYTEST_ARGS)

dagster:
	@open http://localhost:$${DAGSTER_PORT:-10006}

superset:
	@open http://localhost:$${SUPERSET_PORT:-10007}

hub:
	@open http://localhost:$${APP_PORT:-10009}

minio:
	@open http://localhost:$${MINIO_CONSOLE_PORT:-10002}

pgweb:
	@open http://localhost:$${PGWEB_PORT:-10008}

trino:
	@open http://localhost:$${TRINO_PORT:-10005}

nessie:
	@echo "Nessie REST API: http://localhost:$${NESSIE_PORT:-10003}/api/v1"

grafana:
	@open http://localhost:$${GRAFANA_PORT:-10016}

prometheus:
	@open http://localhost:$${PROMETHEUS_PORT:-10013}

api:
	@open http://localhost:$${API_PORT:-10010}/docs

hasura:
	@open http://localhost:$${HASURA_PORT:-10011}/console

docs: docs-serve

docs-open:
	@open http://localhost:$(PYMDX_DOCS_PORT)

openmetadata:
	@open http://localhost:$${OPENMETADATA_PORT:-10020}

catalog: openmetadata

docs-generate:
	uv run --locked pymdx generate src/phlo --docs docs --output $(PYMDX_DOCS_DIR)

docs-dev: docs-generate
	uv run --locked pymdx dev $(PYMDX_DOCS_DIR) --port $(PYMDX_DOCS_PORT)

docs-build: docs-generate
	uv run --locked pymdx build $(PYMDX_DOCS_DIR)

docs-serve: docs-dev

docs-clean:
	uv run --locked pymdx clean $(PYMDX_DOCS_DIR)

# Linting targets
lint: lint-python lint-sql

lint-python:
	uv run --locked ruff check .

format-python:
	uv run --locked ruff format --check .

typecheck-python:
	uv run --locked ty check --error-on-warning $(TY_CHECK_SCOPE)

dependency-refresh:
	python3 scripts/dependency_refresh_plan.py --lane $(LANE)

dependency-refresh-check:
	python3 scripts/dependency_refresh_plan.py --check

validate-support-manifest:
	python3 scripts/validate_support_manifest.py

lint-ts:
	$(NPM_OBSERVATORY) run lint

format-ts:
	$(NPM_OBSERVATORY) run format -- --check .

typecheck-ts:
	$(NPM_OBSERVATORY) exec tsc -- -p $(OBSERVATORY_DIR)/tsconfig.json --noEmit

check:
	@$(CHECK_CMD)

lint-sql:
	uv run --locked sqlfluff lint workflows/transforms/dbt

fix-sql:
	uv run --locked sqlfluff fix workflows/transforms/dbt

prek-install:
	uvx prek install
	uvx prek install-hooks

prek-run:
	uvx prek run --all-files

prek-validate:
	uvx prek validate-config

zizmor:
	uvx zizmor --no-online-audits --no-progress --min-severity low .github/workflows

actionlint:
	docker run --rm -v "$(PWD):/repo" -w /repo rhysd/actionlint:1.7.7 -color
