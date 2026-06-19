# PRAXIS v2.0 — Application Dockerfile
# Multi-stage build for production-ready image.

FROM python:3.12-slim AS builder

# Install uv for fast dependency resolution
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Copy dependency files first (better layer caching)
COPY pyproject.toml uv.lock ./

# Install dependencies into a virtual environment
RUN uv sync --frozen --no-dev --no-install-project

# ── Runtime stage ──────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

WORKDIR /app

# Copy the virtual environment from builder
COPY --from=builder /app/.venv /app/.venv

# Copy application source
COPY src/ /app/src/
COPY pyproject.toml /app/

# Set path to use the venv
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONPATH="/app/src:$PYTHONPATH"

# Expose the FastAPI port
EXPOSE 8000

# Run the application with uvicorn
CMD ["uvicorn", "praxis.main:app", "--host", "0.0.0.0", "--port", "8000"]
