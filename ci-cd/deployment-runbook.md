# PRAXIS v2.0 — Deployment Runbook

> Operational guide for deploying, verifying, and rolling back PRAXIS in any environment.
> Last updated: 2026-06-20 (Phase 5 release).

## Table of Contents

1. [Pre-Deployment Checklist](#1-pre-deployment-checklist)
2. [Deployment Steps](#2-deployment-steps)
3. [Post-Deployment Verification](#3-post-deployment-verification)
4. [Rollback Procedure](#4-rollback-procedure)
5. [Monitoring & Logs](#5-monitoring--logs)
6. [Troubleshooting](#6-troubleshooting)
7. [Environment Variables](#7-environment-variables)
8. [Infrastructure Components](#8-infrastructure-components)

---

## 1. Pre-Deployment Checklist

Before deploying, verify:

- [ ] **CI green** — all of `lint`, `test`, `security` jobs passed on the commit being deployed
- [ ] **Coverage threshold met** — `coverage report --fail-under=85` succeeds
- [ ] **Bandit clean** — 0 HIGH / 0 MEDIUM findings
- [ ] **No secrets in git** — `git log -p HEAD~1..HEAD | grep -iE "(api_key|secret|password|token)="` returns nothing
- [ ] **Image built** — `ghcr.io/<owner>/praxis:<sha>` exists (verify via `docker pull`)
- [ ] **Database migrations applied** — Phase 5 has no schema migrations yet, but PostgreSQL must be reachable
- [ ] **Environment variables set** — all vars from `.env.example` populated in target environment
- [ ] **Healthcheck passes locally** — `docker run --rm -p 8000:8000 praxis:<sha>` returns 200 on `/health`
- [ ] **Smoke tests pass locally** — `./ci-cd/smoke-test.sh http://localhost:8000` returns exit 0
- [ ] **Backup taken** (production only) — `pg_dump $POSTGRES_DSN > backups/$(date +%F).sql`
- [ ] **On-call notified** — alert #ops in your team chat

---

## 2. Deployment Steps

### 2.1 Staging (automatic on `main` merge)

The CD pipeline (`.github/workflows/deploy.yml`) automatically:

1. Builds and pushes the image to `ghcr.io/<owner>/praxis:<sha>`.
2. SSHes to the staging host as `$STAGING_USER`.
3. Runs `docker compose pull app && docker compose up -d app` in `/opt/praxis`.
4. Runs the smoke test suite against the staging URL.

No human action required. Verify by visiting `https://staging.praxis.example.com/health`.

### 2.2 Production (manual workflow dispatch)

1. Navigate to **GitHub → Actions → Deploy → Run workflow**.
2. Select `production` environment.
3. Enter the version tag (e.g. `v0.1.0` or a specific git SHA).
4. Click **Run workflow**.
5. Approve the deployment in the GitHub environment protection rules.

The pipeline will:

1. Build & push the image (or reuse an existing one if the SHA was already built).
2. SSH to the production host.
3. Pull the image, restart the `app` service.
4. Run the smoke test suite.

### 2.3 Manual deployment (no GitHub Actions)

For environments without GitHub Actions access:

```bash
# 1. On a build host with Docker
git clone https://github.com/<owner>/praxis.git
cd praxis
git checkout v0.1.0
docker build -t praxis:v0.1.0 .

# 2. Push to a private registry (or save to a tarball)
docker tag praxis:v0.1.0 registry.example.com/praxis:v0.1.0
docker push registry.example.com/praxis:v0.1.0

# 3. On the production host
ssh deploy@praxis.example.com
cd /opt/praxis
export IMAGE_TAG=v0.1.0
docker compose pull app
docker compose up -d app
docker system prune -f
```

---

## 3. Post-Deployment Verification

### 3.1 Health check (manual)

```bash
curl -sf https://praxis.example.com/health | jq .
```

Expected response:

```json
{
  "status": "ok",
  "version": "0.1.0",
  "services": { "api": "ready" }
}
```

### 3.2 Smoke tests (automated)

```bash
./ci-cd/smoke-test.sh https://praxis.example.com
```

This script verifies:

1. `/health` returns 200 with version + services
2. Webhook rejects requests with missing Svix headers (401)
3. Webhook rejects requests with invalid signature (401)
4. Webhook accepts a validly-signed payload and returns a `thread_id` (200)

If any check fails, the script exits non-zero and the deploy should be considered failed.

### 3.3 Container health

```bash
docker compose ps
docker compose logs --tail=100 app
```

Look for:

- `app.startup` log entry with `version=0.1.0`
- `app.shutdown` only on intentional restarts
- No repeated `webhook.invalid_signature` warnings (would indicate a misconfigured signer)
- No `neo4j.connect_failed` / `qdrant.health_check_failed` errors

### 3.4 Service-level checks

```bash
# Neo4j
cypher-shell -a bolt://localhost:7687 -u neo4j -p $NEO4J_PASSWORD "RETURN 1"

# Qdrant
curl -sf -H "api-key: $QDRANT_API_KEY" http://localhost:6333/collections

# PostgreSQL
psql "$POSTGRES_DSN" -c "SELECT 1"
```

---

## 4. Rollback Procedure

If post-deployment verification fails, rollback immediately.

### 4.1 Rollback to previous image

```bash
# SSH to the host
ssh deploy@praxis.example.com
cd /opt/praxis

# List recent images
docker image ls ghcr.io/<owner>/praxis

# Pin to previous known-good tag (e.g. v0.0.9)
export IMAGE_TAG=v0.0.9
docker compose pull app
docker compose up -d app
```

### 4.2 Rollback the database

PRAXIS Phase 5 has no database schema migrations — all persistence is by external services
(Neo4j, Qdrant, PostgreSQL for checkpoints). To roll back checkpoint data:

```bash
# Restore the most recent backup
psql "$POSTGRES_DSN" < backups/2026-06-20.sql
```

Neo4j and Qdrant are append-mostly stores; rolling back their data is not part of standard
rollback and should be done by their own backup mechanisms.

### 4.3 Announce the rollback

Post in `#ops` and update the incident ticket with:

- Previous version
- New version
- Reason for rollback
- Impact assessment
- ETA for resolution

### 4.4 Re-deploy after fix

Once the underlying issue is identified and fixed, re-deploy by running the production
deploy workflow again with the fixed tag.

---

## 5. Monitoring & Logs

### 5.1 Structured logs

PRAXIS emits structured JSON logs via `structlog`. Example:

```json
{
  "event": "webhook.invalid_signature",
  "level": "warning",
  "timestamp": "2026-06-20T07:30:00.000Z",
  "svix_id": "msg_abc123"
}
```

### 5.2 Key event names

| Event | Severity | Meaning |
|-------|----------|---------|
| `app.startup` | info | App booted, version + environment emitted |
| `app.shutdown` | info | App shutting down (intentional) |
| `webhook.graph_invoke` | info | Webhook accepted, graph invoked |
| `webhook.invalid_signature` | warning | Signature verification failed (potential abuse) |
| `webhook.missing_headers` | warning | Svix headers absent |
| `umans.invoke` | info | LLM call completed with elapsed_ms |
| `umans.rate_limit` / `hermes.rate_limit` | warning | 429 received, backing off |
| `neo4j.history_failed` | warning | Neo4j query failed (graceful fallback) |
| `qdrant.upsert_failed` | warning | Qdrant upsert failed (graceful fallback) |
| `embedding_node.failed` | warning | Embedding failed, continuing without it |

### 5.3 Recommended dashboards

- **Request rate:** `count by event where event = "webhook.graph_invoke"`
- **Signature failure rate:** `count by 5m where event = "webhook.invalid_signature"`
- **LLM latency:** `histogram_quantile(0.99, elapsed_ms) by model where event = "umans.invoke"`
- **Embedding success rate:** `sum by 5m (event = "embedding_node.succeeded") / sum by 5m (event = "webhook.graph_invoke")`

### 5.4 Alerting thresholds (recommended)

| Signal | Threshold | Action |
|--------|-----------|--------|
| `/health` returns 5xx | > 1% over 5 min | Page on-call |
| Webhook signature failures | > 100/min | Investigate signer |
| LLM 429 rate | > 5% of calls | Reduce concurrency or contact Umans |
| `neo4j.history_failed` | > 10/min | Check Neo4j health |
| Qdrant upsert failures | > 50/min | Check Qdrant health |

(OWASP A09 — log aggregation is not in the application code by design; this section is the
operational answer to that finding, owned by the SRE/on-call team.)

---

## 6. Troubleshooting

### 6.1 Webhook returns 401 for a sender you trust

- Verify the secret in `.env` matches the secret in the AgentMail dashboard.
- Check for clock drift between the sender and your server (Svix tolerance is 5 min).
- Look for `webhook.invalid_signature` in the logs to confirm the failure mode.

### 6.2 LLM calls timing out

- Check `umans.invoke` log entries for `elapsed_ms` outliers.
- Verify `UMANS_BASE_URL` and `UMANS_API_KEY` in the running container (`docker compose exec app env | grep UMANS`).
- Look for `umans.rate_limit` warnings — if frequent, raise the semaphore limits in the model registry or back off the upstream caller.

### 6.3 Graph state not persisting

- Verify `POSTGRES_DSN` is set and PostgreSQL is reachable.
- Check the checkpointer logs (`praxis.graph.checkpointer`).
- The application falls back to `InMemoryFallback` if PostgreSQL is unreachable — this is intentional for local dev but loses state on restart.

### 6.4 Container won't start

```bash
docker compose logs --tail=200 app
```

Common causes:

- `pydantic_settings` validation error → check `.env` for missing required vars
- Port 8000 already in use → change `PORT` or stop the conflicting process
- Out of memory → increase container memory limit or check for leaks

---

## 7. Environment Variables

All variables are loaded by `praxis.config.Settings`. None are required at code level
(all have defaults), but the following MUST be set in production:

| Variable | Required | Default | Notes |
|----------|----------|---------|-------|
| `ENVIRONMENT` | yes | `development` | Set to `production` for prod deploys |
| `UMANS_API_KEY` | yes | `test-key` | Get from Umans dashboard |
| `UMANS_BASE_URL` | no | `https://api.umans.ai/v1` | Override for self-hosted or staging |
| `AGENTMAIL_API_KEY` | yes | `test-key` | Get from AgentMail dashboard |
| `AGENTMAIL_WEBHOOK_SECRET` | yes | placeholder | `whsec_<base64>` from AgentMail |
| `NEO4J_URI` | no | `bolt://127.0.0.1:7687` | Use service name in compose |
| `NEO4J_USER` / `NEO4J_PASSWORD` | yes | `neo4j` / dev default | Set in secrets store |
| `QDRANT_URL` | no | `http://127.0.0.1:6333` | Use service name in compose |
| `QDRANT_API_KEY` | yes | dev default | Set in secrets store |
| `POSTGRES_DSN` | yes | dev DSN | `postgresql://user:pass@host:5432/praxis` |
| `HERMES_BASE_URL` | no | `http://localhost:8787` | Point to Hermes service |
| `HERMES_API_KEY` | yes | dev key | Set in secrets store |
| `HERMES_TIMEOUT` | no | `30.0` | Seconds |
| `HERMES_MAX_RETRIES` | no | `3` | Exponential backoff |

See `.env.example` for the canonical template.

---

## 8. Infrastructure Components

PRAXIS v2.0 uses four backing services, all defined in `docker-compose.yml`:

| Service | Image | Port(s) | Purpose |
|---------|-------|---------|---------|
| `app` (PRAXIS) | built locally | 8000 | FastAPI + LangGraph |
| `neo4j` | `neo4j:5.20` | 7687 (bolt), 7474 (browser) | Sender history, corrections |
| `qdrant` | `qdrant/qdrant:latest` | 6333 (REST), 6334 (gRPC) | Sender embeddings |
| `postgres` | `postgres:16-alpine` | 5432 | LangGraph checkpointing |

All services have health checks and persistent volumes. In production, replace `neo4j:5.20`
with a managed Neo4j cluster (e.g. Neo4j Aura) and Qdrant with Qdrant Cloud for
high availability.

---

**Last reviewed:** 2026-06-20
**Sign-off:** Phase 5 gate passed (CHECKPOINT::RELEASE_VERIFIED)
