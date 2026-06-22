# PRAXIS v2.0

Native asynchronous enterprise email agent built on LangGraph, Umans API, and Hermes.

## Architecture

- **Orchestration:** LangGraph (Native Python)
- **Inference Engine:** Umans API (Direct HTTP via LangChain-compatible clients)
- **Concurrency Control:** Native `asyncio.Semaphore` Router (4 Kimi/GLM, 8 Qwen, 8 embed)
- **Memory & Learning:** Hermes + Neo4j (Graph) + Qdrant (Vector)
- **Research:** Local Deep Research (LDR)
- **Email I/O:** AgentMail (Phase 1) → Postfix/IMAP (Phase 5)
- **Checkpointing:** LangGraph + PostgreSQL (with in-memory fallback for tests)

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
- `AGENTMAIL_WEBHOOK_SECRET` — Webhook signing secret (`whsec_<base64>`)
- `NEO4J_PASSWORD` — Neo4j database password
- `QDRANT_API_KEY` — Qdrant API key
- `POSTGRES_DSN` — PostgreSQL DSN for LangGraph checkpointing
- `HERMES_API_KEY` — Hermes platform API key

See `.env.example` for the canonical template and `ci-cd/deployment-runbook.md` §7 for the full list.

## Project Structure

```
praxis/
├── src/praxis/                    # Application source (3,826 LOC, 17.1% comments)
│   ├── main.py                    # FastAPI application entry point + lifespan
│   ├── config.py                  # Pydantic settings
│   ├── chat/                      # LangChain-compatible model wrappers
│   ├── graph/                     # LangGraph state, nodes, build, checkpointer
│   ├── models/                    # Pydantic schemas + Umans model registry
│   ├── router/                    # UmansConcurrencyRouter
│   ├── services/                  # hermes_client, qdrant_client, neo4j_client,
│   │                              #   embedding_service, agentmail_client,
│   │                              #   template_loader
│   ├── tools/                     # stub_tools (REQ-205), email_tools
│   ├── templates/                 # YAML prompt templates (REQ-307)
│   └── webhooks/                  # Svix verification + email webhook
├── tests/                         # 153 tests, 88% coverage
├── ci-cd/                         # Deployment runbook + smoke test
├── .github/workflows/             # CI (lint+test+security+docker) + CD (deploy)
├── docker-compose.yml             # Neo4j + Qdrant + PostgreSQL
├── Dockerfile                     # Multi-stage production image (non-root, healthcheck)
├── pyproject.toml                 # Project config (ruff, pytest, deps)
└── .env.example                   # Environment variable template
```

## Umans Concurrency Router

The router enforces per-model-family concurrency limits using native `asyncio.Semaphore`:

| Model | Family | Limit |
|-------|--------|------:|
| `umans-coder` (Kimi) | kimi | 4 |
| `umans-glm-5.2` | glm | 4 |
| `umans-flash` (Qwen) | qwen | 8 |
| `umans-embed-small` | embed | 8 |

## Deployment

### Production image

```bash
docker build -t praxis:0.1.0 .
docker run --rm -p 8000:8000 --env-file .env praxis:0.1.0
```

The image is hardened:
- Multi-stage build (`uv sync --no-dev` for a minimal venv)
- Non-root user (`praxis`, UID 1000)
- `HEALTHCHECK` directive hits `/health` every 30s
- OCI labels for source / license / description

### CI/CD

- **CI** (`.github/workflows/ci.yml`): runs on every push and PR. Stages: lint, typecheck, test (with PostgreSQL + Neo4j services), security (Bandit + pip-audit), Docker build + smoke test.
- **CD** (`.github/workflows/deploy.yml`): automatic deploy to staging on `main` push; manual deploy to production via `workflow_dispatch` with version tag.

### Smoke tests

```bash
# Local — requires the app running on :8000
./ci-cd/smoke-test.sh

# Sandbox (no real LLM API) — accept 5xx from valid-signature test
SMOKE_ALLOW_GRAPH_FAILURE=1 ./ci-cd/smoke-test.sh http://localhost:8000

# Production
./ci-cd/smoke-test.sh https://praxis.example.com
```

The smoke test verifies:
1. `/health` returns 200 with version + services
2. Webhook rejects missing Svix headers (401)
3. Webhook rejects invalid signatures (401)
4. Webhook accepts a valid signature and invokes the graph

### Deployment runbook

See [`ci-cd/deployment-runbook.md`](ci-cd/deployment-runbook.md) for the full operational guide:
- Pre-deployment checklist
- Staging + production deploy steps
- Post-deployment verification
- Rollback procedure
- Monitoring & alerting thresholds
- Troubleshooting

## Quality Gates

| Phase | Tests | Coverage | Lint | Security | Status |
|-------|------:|---------:|-----:|---------:|--------|
| Phase 1 — Foundation | 40/40 | 97% | 0 | 0 | ✅ |
| Phase 2 — Intelligence | 133/133 | 87% | 0 | 0 | ✅ |
| Phase 3 — Testing | 150/150 | 87% | 0 | 0 | ✅ |
| Phase 4 — Review | 150/150 | 87% | 0 | 0 | ✅ (2 LOW non-blocking) |
| Phase 5 — Deployment | 153/153 | 88% | 0 | 0 | ✅ |

Phase 4 surfaced 2 LOW non-blocking findings, both fixed in Phase 5 with regression tests:
- `HermesClient._sleep` no longer creates a stray `httpx.AsyncClient().aclose()` per retry tick
- `HermesClient.get_template` URL-encodes `template_id` and validates the response is a string

## Security Posture

- **Webhook auth:** HMAC-SHA256 with `hmac.compare_digest` (constant-time), 5-min timestamp tolerance
- **No hardcoded secrets** in source; only placeholder defaults in `config.py`
- **Jinja2 autoescape=True** for templates (Bandit B701 satisfied)
- **Parameterized Cypher** queries (no string formatting in Neo4j)
- **No `eval`/`exec`/`os.system`/`shell=True` anywhere** (Bandit confirms)
- **SSRF-safe:** no user-controlled URLs are fetched
- **OWASP Top 10 (2021):** 9/10 PASS, 1/10 PARTIAL (A09 log aggregation — deferred to ops via the runbook)

## v0.2.0 — Email Bug Fix, Features, and TUI

### Email reply bug fix
The ReAct loop now uses LangChain native tool-calling, eliminating the
text-protocol parser that leaked raw LLM reasoning into email replies.
A dedup guard prevents duplicate sends, and a safe fallback reply is
sent if the model fails to produce a final answer within 3 iterations.

Roll back: set `TOOL_PROTOCOL=legacy` in `.env`.

### Per-sender reply style
Praxis adapts tone, signature, language, and greeting per sender, stored
in Neo4j. The agent can update styles from a user's correction via the
`update_sender_style` tool. Disable with `SENDER_STYLE_ENABLED=false`.

### Metrics and observability
In-process counters, gauges, and histograms for model calls, tool calls,
router concurrency, and email flow. Exported via `/admin/metrics` (JSON)
and `/admin/metrics/prom` (Prometheus text).

### Textual TUI
Run `make tui` or `uv run praxis tui` to launch the terminal UI:
- Dashboard: live system status, router concurrency, service health, metrics.
- Settings: edit and persist app settings (masked secrets).
- Logs: audit log viewer with filters.
- Admin: failed-events table with retry actions.

Requires `PRAXIS_ADMIN_TOKEN` env var (>= 32 chars in production).

### Admin API
All `/admin/*` endpoints require a bearer token (`PRAXIS_ADMIN_TOKEN`):
- `GET /admin/system` — health snapshot (router, checkpointer, services)
- `GET /admin/settings` — masked settings view
- `POST /admin/settings` — update settings (allowlist-enforced, `***` = no change)
- `GET /admin/logs/audit` — audit log entries (filterable: since, level, event, limit)
- `GET /admin/logs/tail` — SSE log stream
- `GET /admin/metrics` — JSON metrics snapshot
- `GET /admin/metrics/prom` — Prometheus text export
- `POST /admin/metrics/reset` — zero all counters/gauges/histograms
- `GET /admin/failed-events` — dead-letter log
- `POST /admin/retry-failed` — replay a failed webhook

## License

MIT
