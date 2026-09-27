SHELL := /bin/bash
.DEFAULT_GOAL := help
UV ?= uv
NPM ?= npm
PYTHON ?= .venv/bin/python
BASE ?= dagent
# Coverage debt is frozen at initialization, not at the moving PR merge base.
COVERAGE_BASE ?= $(shell $(PYTHON) -c 'from pathlib import Path; from scripts.engineering.config import load_constraints; print(load_constraints(Path.cwd()).baseline_rev)')
COVERAGE_FLOORS ?= .engineering/coverage-floors.json
export PATH := $(CURDIR)/.venv/bin:$(CURDIR)/.run/tools:$(PATH)
export PLAYWRIGHT_BROWSERS_PATH ?= $(CURDIR)/.run/playwright

.PHONY: help setup check lint format-check format typecheck test test-python test-frontend coverage guardrails docs-check security secrets-check diff-check change-scope test-guardrails test-ci-contracts test-logging test-integration build build-frontend dev dev-stop dev-status logs seed db-reset smoke verify-ui verify-real-model
.PHONY: setup-browser setup-security-tools

help:
	@printf '%s\n' 'Setup: make setup (requires Node/npm and uv)' 'Check: make check; make test; make coverage' 'Runtime: make dev; make dev-status; make dev-stop' 'Proof: make smoke; make verify-ui; make verify-real-model' 'Scope: make change-scope BASE=dagent'

setup:
	$(UV) sync --locked --python 3.12 --group dev --extra browser
	$(PYTHON) -m xagent.providers.pdf_parser.prepare_deepdoc_assets --tokenizers-only
	$(NPM) ci --prefix frontend
	$(MAKE) setup-browser
	$(MAKE) setup-security-tools
	$(PYTHON) -m pre_commit install

setup-browser:
	$(PYTHON) -m playwright install --with-deps chromium

# Semgrep's dependencies conflict with the application; keep its pinned CLI isolated.
setup-security-tools:
	UV_TOOL_DIR="$(CURDIR)/.run/tool-envs" UV_TOOL_BIN_DIR="$(CURDIR)/.run/tools" $(UV) tool install --python 3.12 semgrep==1.178.0

check: lint format-check typecheck guardrails docs-check security test-guardrails test-ci-contracts test-logging

lint:
	$(PYTHON) -m ruff check src tests scripts/engineering
	$(NPM) run --prefix frontend lint

format-check:
	$(PYTHON) -m ruff format --check src tests scripts/engineering

# Explicit formatting operation; never format unrelated files by default.
format:
	test -n "$(FILES)" || { echo 'Supply intended files: make format FILES="path ..."' >&2; exit 2; }
	$(PYTHON) -m ruff format $(FILES)

typecheck:
	$(PYTHON) -m mypy --package xagent
	$(NPM) run --prefix frontend type-check

# The fast suite retains normal skip reporting. It is not the real-model proof.
# Both coverage tools include unexecuted production source in their reports.
test: test-python test-frontend

test-python:
	mkdir -p .run
	$(PYTHON) -m pytest tests -m 'not slow and not postgresql and not requires_network and not real_rag and not e2e' -n 4 --dist=loadscope --timeout=90 --cov=src/xagent --cov=scripts/engineering --cov-report=json:.run/coverage-python.json --junitxml=.run/python-tests.xml

test-frontend:
	cd frontend && $(NPM) run test:run -- --coverage --coverage.all --coverage.include='src/**/*.{ts,tsx}' --coverage.reporter=json-summary --coverage.reportsDirectory=../.run/coverage-frontend
	$(NPM) run --prefix frontend test:widget:coverage

coverage:
	$(PYTHON) scripts/engineering/check.py --base "$(COVERAGE_BASE)" coverage --artifact-base "$(BASE)" --measured-floors "$(COVERAGE_FLOORS)" --pytest-json .run/coverage-python.json --vitest-summary .run/coverage-frontend/coverage-summary.json

.PHONY: coverage-python coverage-frontend
coverage-python:
	$(PYTHON) scripts/engineering/check.py --base "$(COVERAGE_BASE)" coverage --artifact-base "$(BASE)" --measured-floors "$(COVERAGE_FLOORS)" --scope python --pytest-json .run/coverage-python.json

coverage-frontend:
	$(PYTHON) scripts/engineering/check.py --base "$(COVERAGE_BASE)" coverage --artifact-base "$(BASE)" --measured-floors "$(COVERAGE_FLOORS)" --scope frontend --vitest-summary .run/coverage-frontend/coverage-summary.json

guardrails:
	$(PYTHON) scripts/engineering/check.py --base "$(BASE)" guardrails

.PHONY: baseline-capture
baseline-capture:
	test -n "$(REFERENCE)" || { echo 'Supply an explicitly approved pristine REFERENCE SHA' >&2; exit 2; }
	$(PYTHON) scripts/engineering/check.py baseline --write --reference "$(REFERENCE)"

docs-check:
	$(PYTHON) scripts/engineering/check.py docs

security:
	$(PYTHON) scripts/engineering/check.py --base "$(BASE)" security

secrets-check:
	$(PYTHON) scripts/engineering/check.py --base "$(BASE)" security --secrets-only

diff-check:
	$(PYTHON) scripts/engineering/check.py --base "$(BASE)" diff

change-scope:
	git diff --name-status "$(BASE)" --
	git ls-files --others --exclude-standard

test-guardrails:
	bash scripts/test-guardrails.sh

test-ci-contracts:
	$(PYTHON) -m pytest tests/test_ci_summary_contract.py tests/migrations/test_migration_workflow_contract.py -q --timeout=60
	$(NPM) run --prefix frontend test:ci-manifest

test-logging:
	$(PYTHON) -m pytest tests/web/test_logging_config.py -q --timeout=30

test-integration:
	$(PYTHON) -m pytest tests/migrations/test_migration_integration.py tests/core/test_sqlite_pragmas.py tests/core/test_sqlite_parent_directory.py -m 'not postgresql' -q --timeout=120

build-frontend:
	cd frontend && NEXT_TELEMETRY_DISABLED=1 $(NPM) run build

build: build-frontend
	test ! -L src/xagent/web/frontend_dist
	rm -rf -- src/xagent/web/frontend_dist
	mkdir -p src/xagent/web/frontend_dist
	cp -R frontend/out/. src/xagent/web/frontend_dist/
	$(UV) build

dev:
	$(PYTHON) scripts/engineering/runtime.py start

dev-stop:
	$(PYTHON) scripts/engineering/runtime.py stop

dev-status:
	$(PYTHON) scripts/engineering/runtime.py status

logs:
	$(PYTHON) scripts/engineering/runtime.py logs

seed:
	$(PYTHON) scripts/engineering/runtime.py seed

db-reset:
	$(PYTHON) scripts/engineering/runtime.py reset

smoke:
	$(PYTHON) scripts/engineering/runtime.py smoke

verify-ui:
	$(PYTHON) scripts/engineering/runtime.py ui

verify-real-model:
	$(PYTHON) scripts/engineering/runtime.py real-model
