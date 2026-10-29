# poc-backend — run `make` or `make help` to list targets.
.DEFAULT_GOAL := help
.PHONY: help install env init-db superadmin dev api worker \
        db-up db-down db-logs test test-file test-k lint lint-fix format check clean

PORT ?= 8000
COMPOSE_TEST := docker compose -f compose.test.yml

help: ## List targets
	@grep -hE '^[a-zA-Z0-9_-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*## "}{printf "  \033[36m%-12s\033[0m %s\n",$$1,$$2}'

## --- Setup ---
install: ## Install deps incl. dev group (uv sync)
	uv sync

env: ## Create .env from .env.example (won't overwrite)
	@test -f .env && echo ".env already exists" || (cp .env.example .env && echo "Created .env — fill it in")

init-db: ## Create MongoDB indexes (idempotent)
	uv run python -m app.cli init-db

superadmin: ## Create a Super Admin: make superadmin EMAIL=you@x.com NAME="Your Name"
	@test -n "$(EMAIL)" -a -n "$(NAME)" || (echo 'Usage: make superadmin EMAIL=you@x.com NAME="Your Name"' && exit 1)
	uv run python -m app.cli create-superadmin --email "$(EMAIL)" --name "$(NAME)"

## --- Run ---
api: ## Run the API with reload (PORT=8000)
	uv run uvicorn app.main:app --reload --port $(PORT)

worker: ## Run the agent worker (needed for agent runs)
	uv run python -m app.worker

dev: ## Run API + worker together (Ctrl+C stops both)
	@trap 'kill 0' INT TERM EXIT; \
	uv run python -m app.worker & \
	uv run uvicorn app.main:app --reload --port $(PORT)

## --- Test database (local Docker MongoDB on :27018) ---
db-up: ## Start the throwaway test MongoDB
	$(COMPOSE_TEST) up -d --wait

db-down: ## Stop and remove the test MongoDB
	$(COMPOSE_TEST) down

db-logs: ## Tail test MongoDB logs
	$(COMPOSE_TEST) logs -f

## --- Quality ---
test: db-up ## Run the full test suite (starts test DB first)
	uv run pytest

test-file: db-up ## Run one test file: make test-file F=tests/test_x.py
	@test -n "$(F)" || (echo "Usage: make test-file F=tests/test_x.py" && exit 1)
	uv run pytest $(F) -v

test-k: db-up ## Run tests matching a name: make test-k K=login
	@test -n "$(K)" || (echo "Usage: make test-k K=expr" && exit 1)
	uv run pytest -k "$(K)" -v

lint: ## Ruff lint
	uv run ruff check .

lint-fix: ## Ruff lint with autofix
	uv run ruff check . --fix

format: ## Ruff format
	uv run ruff format .

check: lint test ## Lint + tests (pre-push check)

clean: ## Remove caches
	rm -rf .pytest_cache .ruff_cache
	find . -name __pycache__ -type d -not -path './.venv/*' -prune -exec rm -rf {} +
