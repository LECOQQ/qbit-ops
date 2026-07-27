# Purpose: Provide the complete workflow for the generated project.
# Scope: Standalone root Makefile of the generated composition.
# Composition: Rendered from starter-kit Makefile fragments.

SHELL := /bin/bash
.SHELLFLAGS := -eo pipefail -c
MAKEFLAGS += --no-print-directory
.DEFAULT_GOAL := help

PROJECT_NAME := qbit-ops
PROFILE := poc
STACK := python-cli

PY := poetry run

.PHONY: doctor info help install hooks-install run format lint test check-version check check-fast test-tui ci ci-entrypoint sync test-qbit-matrix test-qbit-version capture-qbit-fixtures docker-matrix-doctor

.sync-stamp: pyproject.toml poetry.lock
	@poetry install --sync --extras tui --no-interaction
	@touch .sync-stamp

sync: .sync-stamp ## Sync the virtualenv when pyproject.toml or poetry.lock changes

doctor: ## Check required local tools
	@missing=0; \
	for command in git make python3 poetry; do \
		if command -v "$$command" >/dev/null 2>&1; then \
			printf '[OK] %s\n' "$$command"; \
		else \
			printf '[MISSING] %s\n' "$$command" >&2; \
			missing=1; \
		fi; \
	done; \
	exit "$$missing"

info: ## Show project and environment information
	@printf 'Project: %s\n' "$(PROJECT_NAME)"
	@printf 'Version: %s\n' "$$(poetry version -s 2>/dev/null || echo unknown)"
	@printf 'Profile: %s\n' "$(PROFILE)"
	@printf 'Stack: %s\n' "$(STACK)"
	@printf 'Python: %s\n' "$$(python3 --version 2>&1)"
	@printf 'Branch: %s\n' "$$(git branch --show-current 2>/dev/null || true)"

help: ## Show available commands
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' Makefile \
	| awk 'BEGIN {FS = ":.*?## "}; {printf "%-20s %s\n", $$1, $$2}'

install: doctor ## Install dependencies and configure Git hooks
	@poetry install --extras tui
	@touch .sync-stamp
	@$(PY) pre-commit install --hook-type commit-msg

hooks-install: ## Reinstall the Conventional Commits hook
	@$(PY) pre-commit install --hook-type commit-msg

run: ## Run the application
	@$(PY) python -m qbit_ops.main

format: ## Format and fix Python files
	@$(PY) ruff check --fix src tests
	@$(PY) black src tests

lint: ## Check Python style and types without modifying files
	@$(PY) ruff check src tests
	@$(PY) black --check src tests
	@$(PY) pyright

test: ## Run Python tests
	@$(PY) pytest

check-version: ## Verify pyproject.toml and the Release Please manifest agree
	@python3 scripts/check_version_sync.py

check: sync lint test check-version ## Run all required quality checks (full TUI suite, no Docker) -- the push/PR gate

check-fast: sync ## Fast local checkpoint: lint/types/version + hermetic non-TUI, non-Docker tests (see docs/TESTING.md; not a substitute for `make check`)
	@$(PY) ruff check src tests
	@$(PY) black --check src tests
	@$(PY) pyright
	@python3 scripts/check_version_sync.py
	@$(PY) pytest -m "not tui and not docker"

test-tui: sync ## Run the complete TUI suite (mutation lifecycle, concurrency, security, audit) -- never touches qBittorrent or Docker
	@$(PY) pytest tests/test_tui_app.py tests/test_tui_bulk_mutation_audit.py tests/test_tui_cli.py tests/test_tui_security.py tests/test_tui_state.py

ci: ## Run CI checks (install, lint, tests, CLI entrypoint)
	@poetry install --extras tui --no-interaction --no-ansi
	@$(MAKE) check
	@$(MAKE) ci-entrypoint

ci-entrypoint: ## Verify the CLI entrypoint responds
	@$(PY) qbit-ops --help

docker-matrix-doctor: ## Check Docker is available for the qBittorrent version matrix
	@if ! command -v docker >/dev/null 2>&1; then \
		printf '[MISSING] docker CLI not found\n' >&2; exit 1; \
	fi; \
	if ! docker version >/dev/null 2>&1; then \
		printf '[MISSING] docker daemon not reachable\n' >&2; exit 1; \
	fi; \
	printf '[OK] docker\n'

test-qbit-matrix: docker-matrix-doctor ## Run the full Docker qBittorrent version matrix (requires Docker, not part of `make check`; never writes captured fixtures -- see `capture-qbit-fixtures`)
	@printf 'Running the full qBittorrent Docker matrix against disposable containers on a dedicated Docker network.\n'
	@printf 'No repository .env, no ~/.config/qbit-ops/.env, no real qBittorrent host is used.\n'
	@QBIT_OPS_DOCKER_MATRIX=1 $(PY) pytest tests/integration -m "docker and not capture" -q; \
	status=$$?; \
	leaked=$$(docker ps -aq --filter "label=qbit-ops.harness" 2>/dev/null | wc -l); \
	if [ "$$leaked" -ne 0 ]; then \
		printf '[LEAK] %s disposable container(s) still present after teardown\n' "$$leaked" >&2; \
		exit 1; \
	fi; \
	exit $$status

test-qbit-version: docker-matrix-doctor ## Run the Docker matrix against one entry: make test-qbit-version QBIT_MATRIX_ID=<id> (never writes captured fixtures)
	@if [ -z "$(QBIT_MATRIX_ID)" ]; then \
		printf 'Usage: make test-qbit-version QBIT_MATRIX_ID=<id>\n' >&2; \
		printf 'Known ids:\n' >&2; \
		grep '^id = ' tests/integration/qbittorrent-matrix.toml >&2 || true; \
		exit 1; \
	fi
	@printf 'Running the qBittorrent Docker matrix entry %s against a disposable container.\n' "$(QBIT_MATRIX_ID)"
	@QBIT_OPS_DOCKER_MATRIX=1 QBIT_MATRIX_ID=$(QBIT_MATRIX_ID) $(PY) pytest tests/integration -m "docker and not capture" -q; \
	status=$$?; \
	leaked=$$(docker ps -aq --filter "label=qbit-ops.harness" 2>/dev/null | wc -l); \
	if [ "$$leaked" -ne 0 ]; then \
		printf '[LEAK] %s disposable container(s) still present after teardown\n' "$$leaked" >&2; \
		exit 1; \
	fi; \
	exit $$status

capture-qbit-fixtures: docker-matrix-doctor ## Capture authentic payload fixtures for one matrix entry: make capture-qbit-fixtures QBIT_MATRIX_ID=<id> (the only target that writes committed fixtures)
	@if [ -z "$(QBIT_MATRIX_ID)" ]; then \
		printf 'Usage: make capture-qbit-fixtures QBIT_MATRIX_ID=<id>\n' >&2; \
		printf 'Known ids:\n' >&2; \
		grep '^id = ' tests/integration/qbittorrent-matrix.toml >&2 || true; \
		exit 1; \
	fi
	@printf 'Capturing payload fixtures for %s from a disposable container.\n' "$(QBIT_MATRIX_ID)"
	@printf 'Fixtures land under tests/compatibility/fixtures/captured-container/%s/ -- review before committing.\n' "$(QBIT_MATRIX_ID)"
	@QBIT_OPS_DOCKER_MATRIX=1 QBIT_MATRIX_ID=$(QBIT_MATRIX_ID) $(PY) pytest tests/integration -m capture -q; \
	status=$$?; \
	leaked=$$(docker ps -aq --filter "label=qbit-ops.harness" 2>/dev/null | wc -l); \
	if [ "$$leaked" -ne 0 ]; then \
		printf '[LEAK] %s disposable container(s) still present after teardown\n' "$$leaked" >&2; \
		exit 1; \
	fi; \
	exit $$status
