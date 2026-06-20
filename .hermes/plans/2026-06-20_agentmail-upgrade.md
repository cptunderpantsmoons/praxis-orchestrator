# AgentMail Integration Upgrade — Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.
> Subagent-driven-development is OFF for this user (max_spawn_depth=1, no `delegate_task` here);
> implement tasks directly with the TDD loop.

**Goal:** Replace the hand-rolled `AgentMailClient` (which only covers send/reply/inbox CRUD) with a thin async wrapper over the official `agentmail` Python SDK, exposing the full API surface (inboxes, messages, threads, webhooks, drafts, labels, send, account), and fix the webhook handler to parse the real AgentMail payload shape (`event_type`, nested `message` + `thread` objects).

**Architecture:**
- Keep `AgentMailClient` as the **async facade** (PRAXIS is fully async — `httpx.AsyncClient`)
- Internally call the **official `agentmail` SDK** (sync `httpx.Client`) via `asyncio.to_thread` — this gets us the full resource namespace (`inboxes`, `messages`, `threads`, `webhooks`, `drafts`, `labels`, `send`, `account`) with zero API drift
- Expose the full method set as async methods on `AgentMailClient` (no behavioural change for callers)
- Replace the custom `svix.py` HMAC verification (still keep it for inbound webhook auth) but the *payload parsing* in `webhooks/email.py` must be updated to handle the real AgentMail event shape: `{type, event_type, event_id, message: {...}, thread: {...}}` and the 10 event types (`message.received`, `message.sent`, `message.delivered`, `message.bounced`, `message.complained`, `message.rejected`, etc.)
- The `langchain-agentmail` v0.1.0 package in this environment calls a non-existent `/send` endpoint — it is an early/draft version. Do not use it; the underlying `agentmail` SDK is what we want.

**Tech Stack:** `agentmail` (Python SDK, sync) + `asyncio.to_thread` for async bridging, Pydantic v2 models for the resource types, `httpx.AsyncClient` retained for the parts we keep (Svix signature verification).

---

## Task 1: Verify the official `agentmail` SDK API and identify all resource methods

**Objective:** Get an authoritative list of every method the SDK exposes, so the wrapper covers them all.

**Files:**
- Inspect only: `agentmail` package, `agentmail.resources.{inboxes,messages,threads,webhooks,drafts,labels,send,account}`

**Steps:**
1. `venv/bin/python -c "import agentmail; help(agentmail.AgentMail)"`
2. For each resource (`inboxes`, `messages`, `threads`, `webhooks`, `drafts`, `labels`, `send`, `account`), enumerate public methods.
3. Note the parameter types (most take `inbox_id: str`, `message_id: str`, etc.) and return types (Pydantic models like `Inbox`, `Message`, `Thread`).
4. Record in a comment block in the new wrapper.

**Acceptance:** A complete inventory of methods. This is informational; the next task uses it.

---

## Task 2: Add `agentmail` to project dependencies

**Objective:** Make the official SDK a runtime dependency.

**Files:**
- Modify: `pyproject.toml` (add `agentmail>=0.1.0` to dependencies)
- Verify: `uv pip install -e .` succeeds

**Steps:**
1. Add `agentmail>=0.1.0` to `[project].dependencies` in `pyproject.toml`.
2. Run `VIRTUAL_ENV=/workspace/Latest/venv uv pip install -e .` to install.
3. Confirm `venv/bin/python -c "import agentmail; print(agentmail.__version__)"` prints `0.1.0`.

**Acceptance:** `import agentmail` works in the venv.

---

## Task 3: Write the test for the new async wrapper

**Objective:** TDD — define the expected behaviour first.

**Files:**
- Create: `tests/test_agentmail_v2.py`

The test should verify:
- `AgentMailClient` has methods: `create_inbox`, `list_inboxes`, `get_inbox`, `delete_inbox`, `list_messages`, `get_message`, `send_message`, `reply_to_message`, `forward_message`, `search_messages`, `list_threads`, `get_thread`, `create_draft`, `list_webhooks`, `create_webhook`, `delete_webhook`, `list_labels`, `create_label`, `apply_label`
- All methods are coroutine functions
- Methods bridge to the sync SDK via `asyncio.to_thread` (test by mocking `asyncio.to_thread`)
- API key loaded from settings (or `AGENTMAIL_API_KEY` env)
- Returns Pydantic models (not raw dicts) — check `isinstance(result, Message)` etc.

**Run:** `pytest tests/test_agentmail_v2.py -v` → must FAIL (no such class yet).

---

## Task 4: Implement the async wrapper

**Objective:** Wrap the official `agentmail` SDK as an async class.

**Files:**
- Create: `src/praxis/services/agentmail_v2.py`
- Modify: `src/praxis/services/agentmail_client.py` to re-export from the new module (backwards-compat) OR keep separate and migrate callers incrementally

The new class:
- `__init__(self, api_key: str | None = None, base_url: str | None = None)` — creates the sync SDK client + stores credentials
- Each method is `async def`, calls `await asyncio.to_thread(self._client.<resource>.<method>, **kwargs)`, returns the Pydantic model
- For methods that need to accept both `str` and `list[str]` (e.g. `to` on send), do the conversion before calling the SDK
- Lazy-init the SDK client (defer to first use) so unit tests without a real API key don't fail at construction

**Run:** `pytest tests/test_agentmail_v2.py -v` → must PASS.

---

## Task 5: Update the existing `AgentMailClient` to delegate to the new wrapper

**Objective:** Backwards compatibility — existing tests still pass.

**Files:**
- Modify: `src/praxis/services/agentmail_client.py` (or delete and re-export)

Option A (cleaner): keep `agentmail_client.py` as a thin re-export shim that imports from `agentmail_v2.py` and re-exports `AgentMailClient`, `get_agentmail_client`. The existing test file `test_agentmail_client.py` should still pass without changes.

Option B (full migration): rename `agentmail_v2.py` to `agentmail_client.py` overwriting the old one; update the few callers. Risk: the existing `test_agentmail_client.py` mocks the HTTP transport with specific URL paths; the new wrapper would not have those URL paths. Choose Option A.

**Run:** `pytest tests/test_agentmail_client.py -v` → must PASS unchanged.

---

## Task 6: Write the test for the new webhook payload parser

**Objective:** TDD — define the expected behaviour for parsing real AgentMail events.

**Files:**
- Create: `tests/test_webhook_payload.py`

Test cases:
- Parse `message.received` event with nested `message` object → extract `sender`, `subject`, `body`, `message_id`, `thread_id`
- Parse `message.sent` event → extract delivery info, no `message.body` (use `send.recipients`)
- Parse `message.delivered` event → extract `delivery.recipients`
- Parse `message.bounced` event → extract `bounce.type`, `bounce.diagnostic`
- Parse `message.complained` event → extract `complaint.type`
- Parse `message.rejected` event → extract `reject.reason`
- Parse `message.received.spam` event → flag as spam, still process sender info
- Parse `message.received.blocked` event → drop, log
- Parse `message.received.unauthenticated` event → flag
- Parse `domain.verified` event → not an email, route to inbox management
- Unknown event type → log warning, return 200 OK
- Missing required fields → log warning, return 200 OK (don't fail the webhook)

**Run:** `pytest tests/test_webhook_payload.py -v` → must FAIL (new parser doesn't exist yet).

---

## Task 7: Implement the new webhook payload parser

**Objective:** Parse real AgentMail event shapes.

**Files:**
- Create: `src/praxis/webhooks/payloads.py`

A function `parse_agentmail_event(payload: dict) -> ParsedEvent | None` that:
- Returns `None` for unknown / non-actionable events (caller should 200 OK and log)
- Returns a typed `ParsedEvent` for actionable events
- Has a method `to_inbound_email()` that produces the `InboundEmail` for `message.received*` events

**Run:** `pytest tests/test_webhook_payload.py -v` → must PASS.

---

## Task 8: Update the webhook handler to use the new parser

**Objective:** Keep Svix signature verification, route events through the new parser, dispatch to the graph only for `message.received*` events.

**Files:**
- Modify: `src/praxis/webhooks/email.py`

Logic:
1. Verify Svix signature (unchanged).
2. Parse the event with `parse_agentmail_event`.
3. If `is None` (unknown / non-email event) → 200 OK with `{"status": "ignored", "reason": "event_type not handled"}`.
4. If event is `message.received*` → build `InboundEmail`, invoke the graph, return `{"status": "accepted", ...}`.
5. If event is `message.delivered` / `message.sent` → log it (sent success metric) and return 200 OK.
6. If event is `message.bounced` / `message.complained` / `message.rejected` → log warning and return 200 OK (do not retry).

**Run:** `pytest tests/test_webhooks.py -v` → must PASS.

---

## Task 9: Update the smoke test to assert new event-type behavior

**Objective:** Cover at least one new event-type path in the smoke test.

**Files:**
- Modify: `ci-cd/smoke-test.sh`

Add a 5th test: send a `message.delivered` webhook with a valid signature, expect 200 OK with `{"status": "ignored"}` or `{"status": "logged"}`.

**Run:** `SMOKE_ALLOW_GRAPH_FAILURE=1 ./ci-cd/smoke-test.sh http://127.0.0.1:8002` → must PASS.

---

## Task 10: Final verification

**Objective:** Confirm the upgrade is clean.

**Steps:**
1. `pytest --cov=src/praxis --cov-report=term -q` → all tests pass, coverage ≥ 88%
2. `ruff check src/ tests/` → 0 errors
3. `bandit -r src/praxis -q` → 0 findings
4. Re-run smoke test → 4/4 pass (or 5/5 with the new test)
5. Update `project_manifest.json` to note the AgentMail upgrade

**Acceptance:** Gate PASS, ready to commit.

---

## Files likely to change

| File | Type | Change |
|------|------|--------|
| `pyproject.toml` | modify | add `agentmail>=0.1.0` dep |
| `src/praxis/services/agentmail_v2.py` | create | new async wrapper over official SDK |
| `src/praxis/services/agentmail_client.py` | modify | thin re-export shim (or keep as-is) |
| `src/praxis/webhooks/payloads.py` | create | new event payload parser |
| `src/praxis/webhooks/email.py` | modify | use new parser, dispatch by event type |
| `tests/test_agentmail_v2.py` | create | tests for the new wrapper |
| `tests/test_webhook_payload.py` | create | tests for the new parser |
| `ci-cd/smoke-test.sh` | modify | add event-type smoke test |
| `project_manifest.json` | modify | log the upgrade |

## Risks / open questions

- **The `langchain-agentmail` package** in this environment (v0.1.0) is a simplified version that calls a non-existent `/send` endpoint. We will NOT use it. The underlying `agentmail` SDK is the source of truth. This may be a packaging/versioning issue with the PyPI index, but it doesn't affect the plan.
- **The official SDK is sync.** Wrapping it in `asyncio.to_thread` is correct, but adds thread-pool overhead. For a high-throughput email agent this could become a bottleneck. Mitigation: use `asyncio.to_thread` only for SDK calls, and the rest of the request path stays async.
- **The `agentmail` SDK's Pydantic models are tied to SDK v0.1.0.** Future versions may have different field names. Mitigation: import models from the SDK rather than redefining them.
- **Webhooks signed with the same Svix secret.** The AgentMail docs say "Svix-compatible" — the current `svix.py` should work as-is. Verify in Task 8.

## Handoff

After all tasks pass, commit with `feat(services): upgrade AgentMail integration to official SDK with full event-type routing` and update the audit log.
