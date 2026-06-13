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

.PHONY: doctor info help install hooks-install run format lint test check ci ci-entrypoint version

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
	@printf 'Version: %s\n' "$$(tr -d '[:space:]' < VERSION)"
	@printf 'Profile: %s\n' "$(PROFILE)"
	@printf 'Stack: %s\n' "$(STACK)"
	@printf 'Python: %s\n' "$$(python3 --version 2>&1)"
	@printf 'Branch: %s\n' "$$(git branch --show-current 2>/dev/null || true)"

help: ## Show available commands
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' Makefile \
	| awk 'BEGIN {FS = ":.*?## "}; {printf "%-20s %s\n", $$1, $$2}'

install: doctor ## Install dependencies and configure Git hooks
	@poetry install
	@$(PY) pre-commit install --hook-type commit-msg

hooks-install: ## Reinstall the Conventional Commits hook
	@$(PY) pre-commit install --hook-type commit-msg

run: ## Run the application
	@$(PY) python -m app.main

format: ## Format and fix Python files
	@$(PY) ruff check --fix app tests tooling
	@$(PY) black app tests tooling

lint: ## Check Python style and types without modifying files
	@$(PY) ruff check app tests tooling
	@$(PY) black --check app tests tooling
	@$(PY) pyright

test: ## Run Python tests
	@$(PY) pytest

check: lint test ## Run all required quality checks

ci: ## Run CI checks (install, lint, tests, CLI entrypoint)
	@poetry install --no-interaction --no-ansi
	@$(MAKE) check
	@$(MAKE) ci-entrypoint

ci-entrypoint: ## Verify the CLI entrypoint responds
	@$(PY) qbit-ops --help

version: ## Set the POC release version with VERSION=x.y.z
	@test -n "$(VERSION)" || { \
		printf 'ERROR: VERSION is required (example: make version VERSION=0.1.0)\n' >&2; \
		exit 1; \
	}
	@python3 tooling/python/set_project_version.py "$(VERSION)"
