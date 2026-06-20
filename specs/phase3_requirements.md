# PRAXIS v2.0 — Phase 3 Requirements

> Phase 3: Hermes integration, Qdrant memory, correction learning
> Scope: REQ-301 through REQ-308

## Phase 3 Goals

1. Integrate Hermes agent platform for prompt templating and structured output
2. Add Qdrant vector store for sender embedding history and correction memory
3. Implement real correction learning node (replace stub)
4. Add VIP/priority fast-path routing to reduce latency for high-priority emails
5. Create prompt template library with versioned YAML/Jinja2 templates
6. Establish Phase 3 quality gate with screenshots and test evidence

---

## REQ-301: Hermes Client Wrapper

**Priority:** P8  
**Status:** `todo`  
**Owner:** `hermes`  
**Acceptance Criteria:**
- Create `src/praxis/services/hermes_client.py` with `HermesClient` class
- Async client using `httpx.AsyncClient` for HTTP requests to Hermes platform
- Methods:
  - `async call_prompt(template_id: str, context: dict) → dict` — render template and call LLM
  - `async get_template(template_id: str) → str` — retrieve template by ID
  - `async stream_prompt(template_id: str, context: dict) → AsyncIterator[str]` — stream LLM output
  - `async get_structured_output(template_id: str, context: dict, schema: type) → dict` — structured output with validation
- Configurable via `HERMES_API_KEY`, `HERMES_BASE_URL` in `Settings`
- Retry with exponential backoff (configurable retries, base delay, max delay)
- Timeout: 30s default, configurable per-call
- Unit tests with `httpx.MockTransport` — 100% coverage on critical paths
- Passes Bandit security scan (no SSRF via template injection)

**Implementation Notes:**
- Template format: YAML with `template`, `variables`, `system_prompt`, `user_prompt` sections
- Template ID schema: `{module}_{type}_{version}` (e.g., `email_triage_v1`)
- Structured output uses Pydantic schema for validation
- Error handling: `HermesAPIError`, `HermesTimeoutError`, `HermesRateLimitError`
- Logging: `structlog` with correlation ID from Hermes response

---

## REQ-302: Qdrant Async Client

**Priority:** P8  
**Status:** `todo`  
**Owner:** `hermes`  
**Acceptance Criteria:**
- Create `src/praxis/services/qdrant_client.py` with `QdrantSenderClient` class
- Async wrapper around `qdrant-client` `AsyncQdrantClient`
- Methods:
  - `async upsert_sender(email: InboundEmail, embedding: list[float]) → bool` — store sender embedding
  - `async get_similar_senders(query_embedding: list[float], limit: int = 10) → list[dict]` — find similar senders
  - `async get_sender_history(email: str, limit: int = 5) → list[EmailTriage]` — retrieve past triage results
  - `async delete_sender(email: str) → bool` — remove sender record
  - `async health_check() → bool` — verify Qdrant connection
- Configurable via `QDRANT_URL`, `QDRANT_COLLECTION` in `Settings`
- Auto-create collection if not exists (configurable dimension, distance metric)
- Unit tests with `pytest-mock` — mock `AsyncQdrantClient`
- Coverage: ≥90% on critical paths

**Implementation Notes:**
- Collection name: `praxis_senders`
- Vector dimension: 1536 (OpenAI `text-embedding-3-small`) or configurable
- Distance metric: `COSINE`
- Payload schema: `{"email": str, "metadata": dict, "created_at": str}`
- Embedding dimension: configurable via `EMBEDDING_DIM` in settings
- Fallback: if Qdrant unavailable, skip vector search (graceful degradation)

---

## REQ-303: Correction Node

**Priority:** P8  
**Status:** `todo`  
**Owner:** `hermes`  
**Acceptance Criteria:**
- Implement `correction_node` in `src/praxis/graph/nodes.py`
- Accepts: `correction_message: str` (user feedback like "This is spam, not general inquiry")
- Updates `memory_context.corrections` with structured correction
- Uses Neo4j for persistent correction storage (via existing `Neo4jClient`)
- Stores: `sender`, `original_triage`, `corrected_triage`, `timestamp`, `confidence`
- Returns updated `AgentState` with refreshed `memory_context`
- Handles edge cases: empty message, invalid JSON, duplicate corrections
- Unit tests: 5 tests covering happy path, error cases, Neo4j interaction
- Coverage: ≥90% on `correction_node`

**Implementation Notes:**
- Correction format: JSON with `correction_type` (e.g., "intent_correction", "priority_upgrade")
- Neo4j query: `MATCH (s:Sender {email: $sender}) CREATE (s)-[:HAS_CORRECTION]->(c:Correction {correction: $correction_json, ...})`
- Deduplication: check for similar corrections within last 24h for same sender
- Confidence decay: older corrections have lower weight in ReAct prompt

---

## REQ-304: Top-N Corrections in ReAct Prompt

**Priority:** P8  
**Status:** `todo`  
**Owner:** `hermes`  
**Acceptance Criteria:**
- Modify `react_node` to prepend top 3 corrections to system prompt
- Query corrections from `memory_context.corrections` (in-memory) and Neo4j (persistent)
- Rank by: sender match (exact > domain > fuzzy), recency, confidence
- Format corrections as structured messages:
  ```
  [CORRECTION] Sender: user@example.com  
  [CORRECTION] Original: intent=GENERAL_INQUIRY, corrected: intent=SPAM
  ```
- Respect max corrections limit (default: 3)
- Ensure prompt stays within token budget (truncate if needed)
- Unit tests: 4 tests covering ranking, formatting, token budget enforcement
- Coverage: ≥90% on correction formatting logic

**Implementation Notes:**
- Correction ranking formula: `score = (sender_match_weight * 0.5) + (recency_weight * 0.3) + (confidence_weight * 0.2)`
- Sender match weights: exact=1.0, domain=0.7, fuzzy=0.4
- Recency: exponential decay `e^(-hours_since_correction / 168)` (1 week half-life)
- Token budget: 500 tokens max for corrections section
- Fallback: if Neo4j unavailable, use in-memory corrections only

---

## REQ-305: VIP/Priority Fast-Path Routing

**Priority:** P6  
**Status:** `todo`  
**Owner:** `hermes`  
**Acceptance Criteria:**
- When `triage.priority == Priority.HIGH`, skip embedding lookup and route directly to ReAct node
- Add routing logic in `build_graph` to conditionally skip `context_loading_node`
- Add `should_skip_context` flag to `AgentState` for routing decisions
- Preserve full context for HIGH priority (don't skip embedding for context, just skip similarity search)
- Benchmark: HIGH priority emails should complete 20% faster than full path
- Unit tests: 3 tests covering routing paths (HIGH→fast, NORMAL→full, LOW→full)
- Coverage: ≥95% on routing logic (critical path)

**Implementation Notes:**
- Fast path: `triage → react` (skip context loading)
- Full path: `triage → context_loading → react`
- Configurable via `FAST_PATH_THRESHOLD` in settings (default: Priority.HIGH)
- Logging: `structlog` with `event="fast_path_enabled"` or `event="full_context_used"`
- Metrics: record latency per path in `AgentMetadata.fast_path_latency_ms`

---

## REQ-306: Embed Sender and Persist to Qdrant

**Priority:** P7  
**Status:** `todo`  
**Owner:** `hermes`  
**Acceptance Criteria:**
- Add `embedding_node` to graph (or extend `context_loading_node`)
- Call embedding endpoint (OpenAI `text-embedding-3-small` or configurable)
- Persist sender email + embedding to Qdrant via `QdrantSenderClient.upsert_sender()`
- Store additional metadata: `sender_domain`, `first_seen`, `last_seen`, `total_emails`
- Handle embedding failures gracefully (fallback: skip vector store, log warning)
- Unit tests: 3 tests covering embedding call, Qdrant upsert, error handling
- Coverage: ≥90% on embedding logic

**Implementation Notes:**
- Embedding model: `text-embedding-3-small` (1536 dimensions)
- Embedding call: `POST /v1/embeddings` with `input=sender_email, model=text-embedding-3-small`
- Metadata enrichment: extract domain from email, track first/last seen timestamps
- Rate limiting: respect OpenAI rate limits (exponential backoff on 429)
- Batch upsert: if multiple senders in same email, batch to Qdrant

---

## REQ-307: Hermes Prompt Template Library

**Priority:** P5  
**Status:** `todo`  
**Owner:** `hermes`  
**Acceptance Criteria:**
- Create `src/praxis/hermes/templates/` with versioned YAML templates
- Templates:
  - `triage_system_v1.yaml` — system prompt for triage node
  - `react_system_v1.yaml` — system prompt for ReAct node
  - `correction_system_v1.yaml` — system prompt for correction node
- Template format: YAML with `template`, `variables`, `system_prompt`, `user_prompt` sections
- Jinja2 rendering with variable substitution from `AgentState`
- Version management: bump version on template changes, maintain backward compatibility
- Unit tests: 4 tests covering template rendering, variable substitution, versioning
- Coverage: 100% on template rendering logic

**Implementation Notes:**
- Template directory structure:
  ```
  templates/
    triage_system_v1.yaml
    react_system_v1.yaml
    correction_system_v1.yaml
  ```
- Variable injection: `{sender}`, `{email_body}`, `{corrections}`, `{context}`
- Rendering: `jinja2.Template(template_str).render(**variables_dict)`
- Version bump: increment `_v` suffix on changes (e.g., `v1` → `v2`)
- Backward compatibility: keep old versions, deprecate with `deprecated: true` flag

---

## REQ-308: Phase 3 Quality Gate Evidence

**Priority:** P7  
**Status:** `todo`  
**Owner:** `hermes`  
**Acceptance Criteria:**
- Run full test suite: `pytest tests/ --cov=src/praxis --cov-report=term-missing`
- Coverage target: ≥85% overall, ≥90% on critical paths (routing, correction, embedding)
- Run linting: `ruff check src/ tests/` — zero errors
- Run security: `bandit -r src/` — zero findings
- Generate screenshots/ directory with:
  - `screenshot_triage.png` — mock triage response
  - `screenshot_react.png` — mock ReAct tool call
  - `screenshot_correction.png` — correction node output
- Capture using Playwright (headless browser) or `matplotlib` for console output
- Document coverage in `screenshots/README.md`
- All quality gates pass before marking Phase 3 complete

**Implementation Notes:**
- Screenshot tool: `playwright` for browser automation or `pytest-playwright` plugin
- Coverage report: HTML format (`--cov-report=html`) for easy viewing
- Evidence package: `screenshots/phase3_evidence.tar.gz` (compressed archive)
- Validation: script `scripts/validate_phase3.sh` to run all gates and produce summary

---

## Phase 3 Dependencies

| Task | Depends On |
|------|------------|
| P3-B (Hermes client) | None |
| P3-C (Qdrant client) | None |
| P3-D (Embed sender) | P3-C, P3-B (for embedding API) |
| P3-E (Correction node) | P3-B (Hermes client for structured output) |
| P3-F (Top-N corrections) | P3-E, P3-C (Qdrant for correction storage) |
| P3-G (Fast-path routing) | P3-B (Hermes client for routing decision) |
| P3-H (Templates) | P3-B (Hermes client for template management) |
| P3-I (Quality gate) | All P3-A through P3-H |

## Phase 3 Success Criteria

- ✅ All 8 requirements implemented and tested
- ✅ Tests: 100% pass rate (59 existing + 17 new Phase 3 tests)
- ✅ Coverage: ≥85% overall, ≥90% on critical paths
- ✅ Lint: 0 errors (ruff)
- ✅ Security: 0 findings (Bandit)
- ✅ Screenshots: 3+ evidence images
- ✅ Quality gate evidence package generated
