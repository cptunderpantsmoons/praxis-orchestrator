# PRAXIS v2.0 — Development Makefile
# Common commands for building, testing, and running the application.

.PHONY: help install dev lint format test test-cov security up down clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

install: ## Install dependencies (production)
	uv sync --no-dev

dev: ## Install dependencies (development)
	uv sync

lint: ## Run ruff linter
	uv run ruff check src/ tests/

format: ## Run ruff formatter
	uv run ruff format src/ tests/

test: ## Run test suite
	uv run pytest

test-cov: ## Run tests with coverage report
	uv run pytest --cov=src/praxis --cov-report=term-missing

security: ## Run bandit security scanner
	uv run bandit -r src/ -q

up: ## Start infrastructure (Neo4j, Qdrant, PostgreSQL)
	docker compose up -d

down: ## Stop infrastructure and purge volumes
	docker compose down -v

clean: ## Remove build artifacts and caches
	rm -rf .venv dist build *.egg-info .pytest_cache .ruff_cache
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
