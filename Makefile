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

.PHONY: doctor info help install hooks-install run format lint test check-version check ci ci-entrypoint sync

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

check: sync lint test check-version ## Run all required quality checks

ci: ## Run CI checks (install, lint, tests, CLI entrypoint)
	@poetry install --extras tui --no-interaction --no-ansi
	@$(MAKE) check
	@$(MAKE) ci-entrypoint

ci-entrypoint: ## Verify the CLI entrypoint responds
	@$(PY) qbit-ops --help
