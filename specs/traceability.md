| REQ ID | Requirement | Source File | Test File | Status |
|--------|------------|-------------|-----------|--------|
| REQ-001 | FastAPI health check | `src/praxis/main.py` | `tests/test_health.py` | ✅ PASS |
| REQ-002 | UmansConcurrencyRouter | `src/praxis/router/__init__.py` | `tests/test_semaphore_enforcement.py` | ✅ PASS |
| REQ-003 | LangChain chat model wrappers | `src/praxis/chat/wrappers.py` | `tests/test_chat_wrappers.py` | ✅ PASS |
| REQ-004 | AgentMail webhook Svix verification | `src/praxis/webhooks/email.py`, `src/praxis/webhooks/svix.py` | `tests/test_webhooks.py` | ✅ PASS |
| REQ-005 | Infrastructure as Code | `docker-compose.yml` | — | ✅ PASS |
| REQ-006 | Environment configuration | `src/praxis/config.py`, `.env.example` | `tests/test_semaphore_enforcement.py` | ✅ PASS |
| REQ-201 | AgentState schema | `src/praxis/graph/state.py` | `tests/test_graph_state.py` | 🔨 ACTIVE |
| REQ-202 | EmailTriage structured output | `src/praxis/models/schemas.py` | `tests/test_triage_node.py` | 🔨 ACTIVE |
| REQ-203 | Triage Node | `src/praxis/graph/nodes.py` | `tests/test_triage_node.py` | 🔨 ACTIVE |
| REQ-204 | Context Loading Node | `src/praxis/graph/nodes.py` | `tests/test_context_loading.py` | 🔨 ACTIVE |
| REQ-205 | ReAct Orchestrator Node | `src/praxis/graph/nodes.py` | `tests/test_react_node.py` | 🔨 ACTIVE |
| REQ-206 | Webhook graph trigger | `src/praxis/webhooks/email.py`, `src/praxis/main.py` | `tests/test_webhooks.py` | 🔨 ACTIVE |
| REQ-207 | LangGraph checkpointing | `src/praxis/graph/checkpointer.py` | `tests/test_checkpointing.py` | 🔨 ACTIVE |
| REQ-208 | Graph definition | `src/praxis/graph/build.py` | `tests/test_graph_build.py` | 🔨 ACTIVE |
