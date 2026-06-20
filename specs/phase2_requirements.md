# Phase 2: Core Intelligence & State Machine — Requirements

> Source: PRAXIS v2.0 DRD, Section 3.2 / 4.
> Scope: Define and implement the LangGraph state machine, deterministic triage, Neo4j context loading, ReAct orchestration, and checkpointing.

## Phase 2 Objective

Build the LangGraph state machine and implement the deterministic intelligence layer that triages inbound emails, loads sender context, and runs a ReAct reasoning loop.

## Requirements

### REQ-201: AgentState Schema
Define a `praxis.graph.state.AgentState` TypedDict that serves as the immutable single-source-of-truth for the email processing graph.

| Field | Type | Description |
|-------|------|-------------|
| `email_content` | `InboundEmail` | Parsed AgentMail payload with metadata |
| `triage_result` | `EmailTriage \| None` | Output of the Triage Node |
| `memory_context` | `MemoryContext` | Sender history + relevant corrections |
| `tool_outputs` | `dict[str, ToolOutput]` | Aggregated outputs from ReAct tools |
| `final_response` | `str \| None` | Draft response or action summary |
| `metadata` | `AgentMetadata` | Trace & timing metadata |
| `messages` | `Annotated[list[AnyMessage], add_messages]` | LangGraph conversation history |

- **Acceptance:** Schema serializes/deserializes through LangGraph checkpointing without data loss.

### REQ-202: EmailTriage Structured Output
Define `praxis.models.schemas.EmailTriage` Pydantic model with:
- `priority`: `PriorityEnum` (`high` | `normal` | `low`)
- `intent`: `IntentEnum` (`invoice_processing`, `schedule_meeting`, `general_inquiry`, `correction`, `research_request`, `spam`)
- `sentiment`: `SentimentEnum` (`positive`, `neutral`, `negative`)
- `is_spam`: `bool`
- `sender_vip`: `bool`
- `confidence`: `float` (0.0–1.0)

- **Acceptance:** Triage Node returns valid `EmailTriage` JSON for all test cases; invalid JSON triggers retry.

### REQ-203: Triage Node
Implement `praxis.graph.nodes.triage_node` using `umans-qwen-72b` with JSON-mode structured output.
- System prompt instructs model to classify email into `EmailTriage` fields.
- Must use `UmansConcurrencyRouter` via LangChain wrapper.
- Conditional edges:
  - `is_spam == true` → Discard Node
  - `priority == "high"` → High-priority path (Context Loading → ReAct)
  - `intent == "correction"` → Correction Node (Phase 4 prep; stub now)
  - otherwise → Context Loading Node

- **Acceptance:** Inbound email triggers Triage node and correctly outputs structured JSON object.

### REQ-204: Context Loading Node
Implement `praxis.graph.nodes.context_loading_node`. It queries Neo4j for sender history:
- Fetch last 5 `Thread` nodes for `Person.email == sender_email`.
- Fetch top 5 relevant `Correction` nodes by intent category (Phase 4; returns empty now if none).
- Inject results into `memory_context`.

- **Acceptance:** Node returns updated state with `memory_context` populated; test uses mocked Neo4j driver.

### REQ-205: ReAct Orchestrator Node
Implement `praxis.graph.nodes.react_node` using `umans-coder` / `umans-kimi-k2.7` as LangGraph agent.
- Bind Umans model to LangGraph agent using `create_react_agent`.
- Register stubbed tool registry with at least two tools:
  - `dummy_search(query: str) -> SearchResult`
  - `dummy_calculator(expression: str) -> CalculationResult`
- Tool outputs must be strictly typed Pydantic models (no raw strings).

- **Acceptance:** ReAct node successfully invokes a dummy tool and returns structured response.

### REQ-206: Webhook Graph Trigger
Update `praxis.webhooks.email` handler:
- After Svix verification and payload normalization into `InboundEmail`, initialize new `AgentState`.
- Compile the graph once at startup and store in app state.
- Invoke compiled graph with the initial state and config (incl. `thread_id`).
- Return `200 OK` with `thread_id` and `status: accepted` immediately (Phase 5 will move to `202 Accepted` + background worker, but Phase 2 returns synchronously for QG).

- **Acceptance:** Webhook handler parses AgentMail payload, runs graph, and returns identifiable response.

### REQ-207: LangGraph Checkpointing
Implement state persistence using `langgraph-checkpoint-postgres` or `AsyncPostgresSaver`.
- PostgreSQL connection configured via `DATABASE_URL`.
- Checkpointer connects during application lifespan and closes on shutdown.
- Graph compiled with `checkpointer`.
- Test verifies state can be persisted and retrieved via `thread_id`.

- **Acceptance:** Graph state is correctly persisted and retrievable via checkpointing.

### REQ-208: Graph Definition
Create `praxis.graph.build.build_graph()` that wires:
- Nodes: triage, context_loading, react, discard, correction_stub
- Conditional edges from triage to discard / react / correction_stub
- Entry point: triage

## Quality Gate 2 (Definition of Done)

| Gate | Method | Pass Condition |
|------|--------|---------------|
| QG2.1 Triage Output Structured | `tests/test_triage_node.py` | 100% of test emails produce valid `EmailTriage`; conditional edge routing matches expected path |
| QG2.2 ReAct Invokes Dummy Tool | `tests/test_react_node.py` | ReAct loop invokes `dummy_search` or `dummy_calculator` and returns typed Pydantic output |
| QG2.3 Checkpoint Persist/Retrieve | `tests/test_checkpointing.py` | After graph interrupt & resume, state fields recover identical values |
| QG2.4 Static Analysis | `ruff check src/ tests/` | 0 lint errors |
| QG2.5 Test Coverage | `pytest --cov=praxis` | ≥ 85% overall, 100% on critical paths |

## Phase 2 Skill Allocation
- Primary: `test-driven-development`
- Support: `simplify-code`, `systematic-debugging`
