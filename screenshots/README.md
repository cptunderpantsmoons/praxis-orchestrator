# Phase 3 Evidence (REQ-308)

Generated on 2026-06-20 against commit closing Phase 3.

## Quality Gate Results

| Gate | Target | Actual | Status |
|------|--------|--------|--------|
| Tests passing | 100% | 150/150 | ✅ |
| Coverage (overall) | ≥ 85% | **87%** | ✅ |
| Coverage (critical paths) | 100% | 100% | ✅ |
| Ruff lint | 0 errors | 0 | ✅ |
| Bandit security findings | 0 | 0 | ✅ |

## Phase 3 Requirements Status

| REQ | Description | Status |
|-----|-------------|--------|
| REQ-301 | Hermes client wrapper | ✅ PASS |
| REQ-302 | Qdrant async client | ✅ PASS |
| REQ-303 | Correction node (real) | ✅ PASS |
| REQ-304 | Top-N corrections in ReAct prompt | ✅ PASS |
| REQ-305 | VIP / priority fast-path routing | ✅ PASS |
| REQ-306 | Embed sender + persist to Qdrant | ✅ PASS |
| REQ-307 | Hermes prompt template library | ✅ PASS |
| REQ-308 | Phase 3 quality gate evidence | ✅ PASS |

## Files in this directory

| File | Purpose |
|------|---------|
| `bandit_report.json` | Bandit security scan (JSON) — 0 issues |
| `coverage_html/index.html` | HTML coverage report |
| `screenshot_triage.png` | Triage node structured output (REQ-203) |
| `screenshot_react.png` | ReAct tool call (REQ-205) |
| `screenshot_correction.png` | Correction node output (REQ-303) |
| `screenshot_quality_gate.png` | Phase 3 quality-gate status table |
| `screenshot_test_results.png` | pytest run summary |
| `screenshot_coverage_detail.png` | Per-file coverage breakdown |
| `phase3_evidence.tar.gz` | Compressed archive of the above (re-built each gate) |
| `README.md` | This file |

## How to regenerate

```bash
# Full test + coverage run
pytest --cov=src/praxis --cov-report=html:screenshots/coverage_html --cov-report=term -q

# Bandit report
bandit -r src/praxis -f json -o screenshots/bandit_report.json

# Evidence screenshots
python scripts/generate_phase3_screenshots.py

# Tarball
tar czf screenshots/phase3_evidence.tar.gz -C screenshots/ \
    --exclude=phase3_evidence.tar.gz .
```

## REQ-306 implementation notes

REQ-306 was the only unimplemented Phase 3 requirement. The implementation
introduced:

- `umans-embed-small` model in the registry (family="embed", concurrency=8)
- `UmansConcurrencyRouter.embed()` + `_call_embed()` posting to `/embeddings`
- `praxis.services.embedding_service.EmbeddingService`
- `praxis.graph.nodes.embedding_node` with graceful degradation
- `QdrantSenderClient.upsert_sender()` now tracks `first_seen`,
  `last_seen`, `total_emails`, and `sender_domain`
- New graph edge: `context_loading → embedding → react`
  (HIGH-priority fast-path `triage → react` still skips embedding, per REQ-305)
- 17 new tests in `tests/test_embedding.py` (all pass)

## Test files

```
tests/test_agentmail_client.py    8 tests
tests/test_chat_wrappers.py        8 tests
tests/test_checkpointing.py        8 tests
tests/test_context_loading.py      2 tests
tests/test_correction_node.py      5 tests
tests/test_dummy_tools.py         12 tests
tests/test_embedding.py           17 tests   (new for REQ-306)
tests/test_graph_build.py          5 tests
tests/test_graph_state.py          2 tests
tests/test_health.py               2 tests
tests/test_hermes_client.py       10 tests
tests/test_nodes.py                7 tests
tests/test_qdrant_client.py        9 tests
tests/test_react_node.py           3 tests
tests/test_semaphore_enforcement.py 15 tests
tests/test_stub_tools.py          14 tests
tests/test_template_loader.py      9 tests
tests/test_triage_node.py          3 tests
tests/test_webhooks.py            11 tests
                                ────────
                                 150 tests passing
```
