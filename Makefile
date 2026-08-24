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

.PHONY: secrets check-agents check-ai doctor env-attest info help install hooks-install run format lint test check-version check check-fast test-tui ci ci-entrypoint sync test-qbit-matrix test-qbit-version capture-qbit-fixtures docker-matrix-doctor check-docs check-dist check-image build worktree-new worktree-clean clean demo-up demo-tui demo-transfer demo-reset demo-record demo-down demo-doctor

DEMO_COMPOSE := docker compose -f demo/compose.yml --project-name qbit-ops-demo
DEMO_ENV_FILE := $(CURDIR)/demo/qbit-ops.env

.sync-stamp: pyproject.toml poetry.lock
	@poetry install --sync --extras "tui mcp" --no-interaction
	@touch .sync-stamp

sync: .sync-stamp ## dev: Sync the virtualenv when pyproject.toml or poetry.lock changes

doctor: ## diag: Check required local tools
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

env-attest: ## diag: Prove this checkout's commands exercise its own src/, not another worktree's
	@$(PY) python scripts/env_attest.py --root .

info: ## diag: Show project and environment information
	@printf 'Project: %s\n' "$(PROJECT_NAME)"
	@printf 'Version: %s\n' "$$(poetry version -s 2>/dev/null || echo unknown)"
	@printf 'Profile: %s\n' "$(PROFILE)"
	@printf 'Stack: %s\n' "$(STACK)"
	@printf 'Python: %s\n' "$$(python3 --version 2>&1)"
	@printf 'Branch: %s\n' "$$(git branch --show-current 2>/dev/null || true)"

# One help section per category tag. Every documented target carries
# exactly one of dev/qa/use/diag, enforced by tests/test_makefile_help.py
# so a new target cannot silently vanish from `make help` by forgetting
# its tag.
define help_section
@echo "$(1):"
@grep -hE '^[a-zA-Z0-9_-]+:.*?## $(2): ' $(MAKEFILE_LIST) \
	| sort -t: -k1,1 \
	| awk 'BEGIN {FS = ":.*?## $(2): "}; {printf "  \033[36m%-24s\033[0m %s\n", $$1, $$2}'
@echo ""
endef

help: ## diag: Show this help
	$(call help_section,Development,dev)
	$(call help_section,Quality,qa)
	$(call help_section,Local usage,use)
	$(call help_section,Diagnostics,diag)

install: doctor ## dev: Install dependencies and configure Git hooks
	@poetry install --extras "tui mcp"
	@touch .sync-stamp
	@$(PY) pre-commit install --hook-type commit-msg

hooks-install: ## dev: Reinstall the Conventional Commits hook
	@$(PY) pre-commit install --hook-type commit-msg

run: ## use: Run the application
	@$(PY) python -m qbit_ops.cli.app

format: ## qa: Format and fix Python files
	@$(PY) ruff check --fix demo scripts src tests
	@$(PY) black demo scripts src tests

lint: ## qa: Check Python style and types without modifying files
	@$(PY) ruff check demo scripts src tests
	@$(PY) black --check demo scripts src tests
	@$(PY) pyright

# Hermetic suites run under xdist; the Docker matrix targets below stay
# serial on purpose -- they drive one shared disposable container.
PYTEST_PARALLEL := -n auto

test: ## qa: Run Python tests
	@$(PY) pytest $(PYTEST_PARALLEL) -m "not network and not image"

check-version: ## qa: Verify pyproject.toml and the Release Please manifest agree
	@python3 scripts/check_version_sync.py

check-docs: ## qa: Verify every Markdown link and repo-anchored path reference resolves
	@python3 scripts/check_doc_links.py

# Prefer a local gitleaks; fall back to the official image so the target
# works on a machine that has Docker and nothing else. Neither present
# is an error, never a skip: a secret scanner that quietly does nothing
# reports "clean" for the wrong reason.
GITLEAKS_IMAGE := zricethezav/gitleaks:latest

# `detect` rather than the newer `dir`/`git` subcommands: those do not
# exist before gitleaks 8.18, and Ubuntu still ships 8.16. `detect` is
# understood by every 8.x, verified on 8.16 and 8.30 against this same
# config -- so the scope stays a qbit-ops word and gitleaks' own verb
# churn stops being the caller's problem.
GITLEAKS_SCOPE ?= dir
GITLEAKS_SOURCE_FLAGS = $(if $(filter dir,$(GITLEAKS_SCOPE)),--no-git,)

secrets: ## qa: Scan for committed credentials with gitleaks (GITLEAKS_SCOPE=dir|git)
	@case "$(GITLEAKS_SCOPE)" in \
		dir|git) ;; \
		*) printf "Unknown GITLEAKS_SCOPE '%s'. Use 'dir' (working tree) or 'git' (history).\\n" "$(GITLEAKS_SCOPE)" >&2; exit 2 ;; \
	esac
	@if command -v gitleaks >/dev/null 2>&1; then \
		gitleaks detect --source . $(GITLEAKS_SOURCE_FLAGS) \
			-c .gitleaks.toml --no-banner --redact; \
	elif command -v docker >/dev/null 2>&1; then \
		docker run --rm -v "$(CURDIR):/repo" -w /repo $(GITLEAKS_IMAGE) \
			detect --source . $(GITLEAKS_SOURCE_FLAGS) \
			-c /repo/.gitleaks.toml --no-banner --redact; \
	else \
		printf 'MISSING: gitleaks. Install it (https://github.com/gitleaks/gitleaks) or provide Docker.\n' >&2; \
		exit 1; \
	fi

check-ai: ## qa: Enforce AI hygiene -- provenance, generated artefacts, house style
	@python3 scripts/check_ai_hygiene.py

check-agents: ## qa: Run the agent control-plane's own invariants (no-op without .agents/)
	@if [ -d .agents ]; then $(MAKE) --no-print-directory -C .agents check; \
	else printf 'SKIP: no .agents/ in this checkout.\n'; fi

build: sync ## dev: Build the wheel and sdist into dist/
	@rm -rf dist
	@poetry build

check-dist: build ## qa: Validate the built artifacts and smoke-test the installed entrypoint (needs network)
	@$(PY) twine check --strict dist/*
	@$(PY) pytest tests/test_distribution.py

check-image: sync ## qa: Build the container image locally and verify its entrypoint and OCI labels (needs Docker)
	@$(PY) pytest -m image tests/test_docker_distribution.py

check: sync lint test check-version check-docs check-ai check-agents ## qa: Run all required quality checks (full TUI suite, no Docker) -- the push/PR gate

check-fast: sync ## qa: Fast local checkpoint: lint/types/version + hermetic non-TUI, non-Docker tests (not a substitute for `make check`)
	@$(PY) ruff check demo scripts src tests
	@$(PY) black --check demo scripts src tests
	@$(PY) pyright
	@python3 scripts/check_version_sync.py
	@python3 scripts/check_doc_links.py
	@python3 scripts/check_ai_hygiene.py
	@$(PY) pytest $(PYTEST_PARALLEL) -m "not tui and not docker and not network and not image"

test-tui: sync ## qa: Run the complete TUI suite (mutation lifecycle, concurrency, security, audit) -- never touches qBittorrent or Docker
	@$(PY) pytest $(PYTEST_PARALLEL) tests/test_tui_app.py tests/test_tui_architecture.py tests/test_tui_bulk_mutation_audit.py tests/test_tui_cli.py tests/test_tui_dots.py tests/test_tui_gallery.py tests/test_tui_overview_model.py tests/test_tui_overview_windows.py tests/test_profile_tui_table.py tests/test_tui_security.py tests/test_tui_setup.py tests/test_tui_state.py tests/test_tui_table_performance.py tests/test_tui_value_form.py

ci: ## qa: Run CI checks (install, lint, tests, CLI entrypoint)
	@poetry install --extras "tui mcp" --no-interaction --no-ansi
	@$(MAKE) check
	@$(MAKE) ci-entrypoint

ci-entrypoint: ## qa: Verify the CLI entrypoint responds
	@$(PY) qbit-ops --help

worktree-new: ## dev: Create a branch, worktree and venv for a feature: make worktree-new FEATURE=<slug>
	@test -n "$(FEATURE)" || { printf 'Usage: make worktree-new FEATURE=<slug>\n' >&2; exit 1; }
	@python3 scripts/worktree_new.py "$(FEATURE)"

worktree-clean: ## dev: Remove a feature worktree and its merged branch: make worktree-clean FEATURE=<slug>
	@test -n "$(FEATURE)" || { printf 'Usage: make worktree-clean FEATURE=<slug>\n' >&2; exit 1; }
	@python3 scripts/worktree_clean.py "$(FEATURE)"

clean: ## dev: Remove locally generated, reproducible artifacts (safe, idempotent -- never touches .venv, .git, .env, or committed fixtures)
	@find demo scripts src tests -type d -name "__pycache__" -exec rm -rf {} +
	@find demo scripts src tests -type f \( -name "*.pyc" -o -name "*.pyo" \) -delete
	@rm -rf .pytest_cache .ruff_cache .mypy_cache
	@if [ -d .pyright ]; then rm -rf .pyright; fi
	@rm -f .coverage
	@rm -f .coverage.*
	@rm -rf htmlcov
	@rm -rf build dist
	@find . -maxdepth 2 -type d -name "*.egg-info" \
		-not -path "./.venv/*" -not -path "./venv/*" -exec rm -rf {} +
	@printf 'Cleaned local caches and build artifacts.\n'

docker-matrix-doctor: ## diag: Check Docker is available for the qBittorrent version matrix
	@if ! command -v docker >/dev/null 2>&1; then \
		printf '[MISSING] docker CLI not found\n' >&2; exit 1; \
	fi; \
	if ! docker version >/dev/null 2>&1; then \
		printf '[MISSING] docker daemon not reachable\n' >&2; exit 1; \
	fi; \
	printf '[OK] docker\n'

demo-doctor: ## diag: Check required local tools for the demo (docker, compose, poetry)
	@missing=0; \
	for command in docker poetry; do \
		if command -v "$$command" >/dev/null 2>&1; then \
			printf '[OK] %s\n' "$$command"; \
		else \
			printf '[MISSING] %s\n' "$$command" >&2; missing=1; \
		fi; \
	done; \
	if ! docker compose version >/dev/null 2>&1; then \
		printf '[MISSING] docker compose plugin\n' >&2; missing=1; \
	else \
		printf '[OK] docker compose\n'; \
	fi; \
	exit "$$missing"

demo-up: demo-doctor sync ## use: Generate demo fixtures, start the disposable qBittorrent instances, seed and pair them
	@$(PY) python demo/generate_fixtures.py
	@DEMO_UID=$$(id -u) DEMO_GID=$$(id -g) $(DEMO_COMPOSE) up -d
	@$(PY) python demo/seed_instance.py
	@$(PY) python demo/arm_transfer.py
	@printf '\nNext: make demo-tui | make demo-record | make demo-down\n'

demo-tui: demo-transfer ## use: Launch the qbit-ops TUI against the demo instance only
	@QBIT_OPS_ENV_FILE="$(DEMO_ENV_FILE)" $(PY) qbit-ops tui

demo-transfer: demo-doctor sync ## use: Restart the live seeder -> leecher transfer so the Overview graph is fresh
	@$(PY) python demo/arm_transfer.py

demo-reset: demo-down ## use: Destroy and recreate the demo instance from scratch
	@rm -rf demo/generated
	@$(MAKE) demo-up

demo-record: demo-transfer ## use: Record demo/tui.tape with VHS (requires VHS installed separately)
	@if ! command -v vhs >/dev/null 2>&1; then \
		printf '[MISSING] vhs not found -- install from https://github.com/charmbracelet/vhs\n' >&2; \
		exit 1; \
	fi
	@mkdir -p demo/output
	@vhs demo/tui.tape

demo-down: ## use: Stop and remove the demo containers, network, and all qBittorrent state
	@$(DEMO_COMPOSE) down -v --remove-orphans
	@rm -rf demo/generated/config demo/generated/downloads demo/generated/leecher-config demo/generated/leecher-downloads

test-qbit-matrix: docker-matrix-doctor ## qa: Run the full Docker qBittorrent version matrix (requires Docker, not part of `make check`; never writes captured fixtures -- see `capture-qbit-fixtures`)
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

test-qbit-version: docker-matrix-doctor ## qa: Run the Docker matrix against one entry: make test-qbit-version QBIT_MATRIX_ID=<id> (never writes captured fixtures)
	@if [ -z "$(QBIT_MATRIX_ID)" ]; then \
		printf 'Usage: make test-qbit-version QBIT_MATRIX_ID=<id>\n' >&2; \
		printf 'Known ids:\n' >&2; \
		grep '^id = ' src/qbit_core/data/qbittorrent-matrix.toml >&2 || true; \
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

capture-qbit-fixtures: docker-matrix-doctor ## qa: Capture authentic payload fixtures for one matrix entry: make capture-qbit-fixtures QBIT_MATRIX_ID=<id> (the only target that writes committed fixtures)
	@if [ -z "$(QBIT_MATRIX_ID)" ]; then \
		printf 'Usage: make capture-qbit-fixtures QBIT_MATRIX_ID=<id>\n' >&2; \
		printf 'Known ids:\n' >&2; \
		grep '^id = ' src/qbit_core/data/qbittorrent-matrix.toml >&2 || true; \
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
