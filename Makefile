.RECIPEPREFIX := >
.PHONY: up down lint test shell clean install install-full tui

# Boot infrastructure and start FastAPI dev server with hot-reload
up:
>docker compose up -d neo4j qdrant postgres
>uv run uvicorn praxis.main:app --reload --host 127.0.0.1 --port 8000

# Tear down containers and purge named volumes
down:
>docker compose down -v

# Enforce formatting and catch static analysis violations
lint:
>uv run ruff check .
>uv run ruff format .

# Execute the full test suite with verbose async reporting
test:
>uv run pytest -v

# Spawn an interactive REPL with all project dependencies loaded
shell:
>uv run python

# Remove virtual environments and compiled artifacts
clean:
>rm -rf .venv dist build *.egg-info .ruff_cache .pytest_cache
>find . -type d -name "__pycache__" -exec rm -rf {} +

# Install core dependencies (Phase 1)
install:
>uv sync

# Install all dependencies including AI and infrastructure extras
install-full:
>uv sync --all-extras

# Launch the Textual TUI for managing the PRAXIS backend
tui:
>uv run praxis tui
