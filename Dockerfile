# PRAXIS v2.0 — Application Dockerfile
# Multi-stage build for production-ready image.
# Hardened with: non-root user, healthcheck, minimal attack surface.

# ── Stage 1: Build dependencies ──────────────────────────────────
FROM python:3.12-slim AS builder

# Install uv for fast dependency resolution
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Install build dependencies (only needed in builder stage)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency files first (better layer caching)
COPY pyproject.toml uv.lock ./

# Install ALL dependencies (base + ai + infra + email) into a virtual environment
# We need langgraph, neo4j, qdrant-client, asyncpg, langgraph-checkpoint-postgres,
# agentmail which are in the [ai], [infra], and [email] optional-dependency groups.
# Step 1: sync the base deps from uv.lock (respects --frozen for reproducibility)
# Step 2: pip-install the optional extras on top
RUN uv sync --frozen --no-dev --no-install-project && \
    uv pip install --python /app/.venv/bin/python \
        "langgraph>=0.0.50" \
        "langchain-openai>=0.1.0" \
        "neo4j>=5.20.0" \
        "qdrant-client>=1.8.0" \
        "asyncpg>=0.29.0" \
        "psycopg[binary]>=3.1.0" \
        "langgraph-checkpoint-postgres>=2.0.0" \
        "agentmail>=0.1.0"

# ── Stage 2: Runtime ─────────────────────────────────────────────
FROM python:3.12-slim AS runtime

# OCI labels
LABEL org.opencontainers.image.title="PRAXIS" \
      org.opencontainers.image.description="Native asynchronous enterprise email agent" \
      org.opencontainers.image.source="https://github.com/praxis/praxis" \
      org.opencontainers.image.licenses="MIT"

# Install only the runtime libs we need (curl for HEALTHCHECK)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Create a non-root user for the application
RUN groupadd --system --gid 1000 praxis \
    && useradd --system --uid 1000 --gid praxis --home-dir /app --shell /usr/sbin/nologin praxis

WORKDIR /app

# Copy the virtual environment from builder (read-only, owned by root)
COPY --from=builder --chown=root:root /app/.venv /app/.venv

# Copy application source (read-only, owned by root, app can read)
COPY --chown=root:root src/ /app/src/
COPY --chown=root:root pyproject.toml /app/

# Ensure /app is readable by the praxis user (no write access needed at runtime)
RUN chown -R praxis:praxis /app && chmod -R 555 /app

# Set path to use the venv and find the source
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH="/app/src:$PYTHONPATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    ENVIRONMENT=production

# Drop privileges
USER praxis

# Expose the FastAPI port
EXPOSE 8000

# Container-level healthcheck (used by Docker / Kubernetes)
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

# Run the application with uvicorn
# --host 0.0.0.0: bind to all interfaces (required for Docker networking)
# --port 8000: FastAPI default
# --workers 1: single worker (concurrency handled by asyncio.Semaphore)
# --proxy-headers: respect X-Forwarded-* from reverse proxy (e.g. nginx, ALB)
CMD ["uvicorn", "praxis.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--workers", "1", \
     "--proxy-headers", \
     "--log-level", "info"]
