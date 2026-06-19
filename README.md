# PRAXIS v2.0

Native asynchronous enterprise email agent built on LangGraph, Umans API, and Hermes.

## Architecture

- **Orchestration:** LangGraph (Native Python)
- **Inference Engine:** Umans API (Direct HTTP via LangChain-compatible clients)
- **Concurrency Control:** Native `asyncio.Semaphore` Router (4 Kimi/GLM, 8 Qwen)
- **Memory & Learning:** Hermes + Neo4j (Graph) + Qdrant (Vector)
- **Research:** Local Deep Research (LDR)
- **Email I/O:** AgentMail (Phase 1) → Postfix/IMAP (Phase 5)

## Quick Start

```bash
# Install dependencies
make dev

# Start infrastructure (Neo4j, Qdrant, PostgreSQL)
make up

# Run the development server
uv run uvicorn praxis.main:app --reload

# Run tests
make test

# Lint
make lint
```

## Configuration

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
```

Key environment variables:
- `UMANS_API_KEY` — API key for Umans inference
- `SVIX_WEBHOOK_SECRET` — Webhook signing secret (whsec_...)
- `NEO4J_PASSWORD` — Neo4j database password
- `QDRANT_API_KEY` — Qdrant API key

## Project Structure

```
praxis/
├── src/praxis/
│   ├── main.py              # FastAPI application entry point
│   ├── config.py            # Pydantic settings
│   ├── router/              # Umans concurrency router + model wrappers
│   │   ├── concurrency.py   # UmansConcurrencyRouter (asyncio.Semaphore)
│   │   └── models.py        # LangChain-compatible chat model wrappers
│   ├── webhooks/            # AgentMail webhook handlers
│   │   ├── svix.py          # Svix-compatible signature verification
│   │   └── email.py         # POST /webhook/email endpoint
│   └── models/              # Pydantic schemas
│       └── schemas.py       # HealthResponse, InboundEmailPayload, etc.
├── tests/                   # pytest test suite
├── docker-compose.yml       # Neo4j + Qdrant + PostgreSQL
├── Dockerfile               # Multi-stage production image
├── pyproject.toml           # Project config (ruff, pytest, deps)
└── .env.example             # Environment variable template
```

## Umans Concurrency Router

The router enforces per-model-family concurrency limits using native `asyncio.Semaphore`:

| Model | Family | Limit |
|-------|--------|-------|
| `umans-coder` | Kimi | 4 |
| `umans-glm-5.2` | GLM | 4 |
| `umans-flash` | Qwen | 8 |

## Quality Gate 1

- [x] FastAPI app boots and passes `/health` check
- [x] Webhook endpoint accepts mock AgentMail payload, verifies Svix signature
- [x] Concurrency Router enforces semaphore limits under concurrent load
