# PRAXIS v2.0 — Phase 1 Requirements

## Phase 1: Foundation & Infrastructure

### Objective
Establish the deployment environment, secure inference pipeline with concurrency router, and implement the base API structure with AgentMail webhook ingestion.

---

## Requirements

### REQ-001: FastAPI Application Skeleton
**Description:** The application must boot successfully and expose a `/health` endpoint returning JSON with status, version, and sub-service states.
**Acceptance Criteria:**
- [x] `GET /health` returns 200 OK
- [x] Response includes `status: "ok"`, `version`, and `services` dict
- [x] App starts without errors via `uvicorn praxis.main:app`
**Test:** `tests/test_health.py` (4 tests)
**Status:** ✅ PASS

### REQ-002: UmansConcurrencyRouter
**Description:** An async concurrency router that enforces per-model-family semaphore limits using `asyncio.Semaphore`.
**Acceptance Criteria:**
- [x] Kimi (`umans-coder`): max 4 concurrent calls
- [x] GLM (`umans-glm-5.2`): max 4 concurrent calls
- [x] Qwen (`umans-flash`): max 8 concurrent calls
- [x] Different families are independent (4+8=12 can run simultaneously)
- [x] All requests eventually complete (no drops)
- [x] `active_counts` property tracks in-flight requests
**Test:** `tests/test_concurrency_router.py` (9 tests)
**Status:** ✅ PASS

### REQ-003: LangChain-Compatible Chat Model Wrappers
**Description:** Chat model wrappers that inherit from `langchain_core.BaseChatModel` and route all inference through the `UmansConcurrencyRouter`.
**Acceptance Criteria:**
- [x] `UmansChatModel` implements `_agenerate` (async) and raises `NotImplementedError` for sync `_generate`
- [x] Factory functions: `create_kimi_model`, `create_qwen_model`, `create_glm_model`
- [x] LangChain messages correctly converted to OpenAI-compatible dicts (human→user, ai→assistant, system→system)
- [x] Temperature and max_tokens passed through to API
- [x] Returns `AIMessage` with content from API response
**Test:** `tests/test_models.py` (7 model wrapper tests)
**Status:** ✅ PASS

### REQ-004: AgentMail Webhook with Svix Verification
**Description:** `POST /webhook/email` endpoint that verifies Svix-compatible HMAC-SHA256 signatures before processing inbound email payloads.
**Acceptance Criteria:**
- [x] Valid signature + valid payload → 200 OK with event ID
- [x] Missing Svix headers → 401 Unauthorized
- [x] Invalid signature → 401 Unauthorized
- [x] Expired timestamp (>5 min) → 401 Unauthorized
- [x] Tampered payload → 401 Unauthorized
- [x] Supports optional `html_body` and `attachments` fields
- [x] Multiple signatures in header (one valid) → verification passes
**Test:** `tests/test_webhook.py` (12 tests)
**Status:** ✅ PASS

### REQ-005: Infrastructure as Code
**Description:** `docker-compose.yml` that provisions local instances of Neo4j, Qdrant, and PostgreSQL with health checks.
**Acceptance Criteria:**
- [x] Neo4j 5.20 with APOC plugin, Bolt port 7687, browser 7474
- [x] Qdrant 1.9 with API key auth, REST port 6333, gRPC 6334
- [x] PostgreSQL 16 Alpine with health check
- [x] All services have health checks and persistent volumes
**Artifact:** `docker-compose.yml`
**Status:** ✅ PASS

### REQ-006: Environment Configuration
**Description:** All environment variables documented in `.env.example` with descriptions and default values.
**Acceptance Criteria:**
- [x] All settings in `.env.example` match `Settings` class in `config.py`
- [x] Secrets use placeholder values
- [x] Concurrency limits documented
**Artifact:** `.env.example`
**Status:** ✅ PASS

---

## Quality Gate 1 Results

| Criterion | Threshold | Actual | Result |
|-----------|-----------|--------|--------|
| Tests passing | 100% | 40/40 | ✅ PASS |
| Coverage (overall) | ≥ 85% | 97% | ✅ PASS |
| Coverage (critical paths) | 100% | 100% | ✅ PASS |
| Lint errors | 0 | 0 | ✅ PASS |
| Security findings | 0 | 0 | ✅ PASS |
| App boots | Yes | Yes | ✅ PASS |
| Health check | 200 OK | 200 OK | ✅ PASS |
| Webhook verification | Pass | Pass | ✅ PASS |
| Concurrency enforcement | Pass | Pass | ✅ PASS |

**Gate Result: PASS — Phase 1 Complete**
