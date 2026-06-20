# Research Results: ## Executive Summary: Architectural Verdict

After rigorous evaluation, **the Direct API / Python SDK approach is significantly superior to the CLI subprocess approach.** 

While the CLI approach offers isolation, it introduces severe architectural debt for an enterprise system:
1.  **Latency & Overhead:** Spawning a new Python process for every memory lookup or research query adds 200-500ms of overhead per call. At scale, this destroys throughput.
2.  **State Management:** Passing complex JSON objects via `stdout` is brittle and limits the depth of context you can pass to Hermes or LDR.
3.  **Concurrency Control:** Managing `asyncio.Semaphores` across process boundaries is vastly more complex than managing them natively within a single async runtime.

**Directive:** We will architect PRAXIS v2.0 as a native, asynchronous Python application. LDR and Hermes will be integrated as local Python services/libraries or invoked via their native HTTP/gRPC APIs, communicating directly with the Umans inference endpoints.

Below is the comprehensive, start-to-finish Development Requirements Document (DRD) for building PRAXIS from scratch.

---

# PRAXIS v2.0: Development Requirements Document (DRD)

**Product Owner:** Audit Intellect  
**Document Status:** Active  
**Last Updated:** June 2026  

---

## 0. What PRAXIS Is and Isn't

**Critical Architectural Clarification:**

PRAXIS is an **autonomous email agent with its own dedicated mailbox**, not a middleware that accesses user mailboxes.

### What PRAXIS IS:
- An AI agent with its **own email address(es)** provisioned via AgentMail.to (Phase 1) or self-hosted (Phase 5+)
- Recipients send emails **directly to the agent's inbox** (e.g., `agent@company.agentmail.to`)
- The agent **receives inbound emails via webhooks** pushed to its API endpoint when mail arrives in its mailbox
- The agent **sends replies and new emails from its own mailbox** using the AgentMail API or SMTP
- The agent **learns and adapts** based on corrections and feedback received in its own inbox
- The agent **maintains its own context** across conversations using Neo4j graph memory and Qdrant vector embeddings

### What PRAXIS IS NOT:
- NOT a plugin that connects to user email accounts (Gmail, Outlook, etc.)
- NOT an IMAP client that polls user inboxes
- NOT a middleware that forwards or proxies user emails
- NOT a shared mailbox system where multiple users access the same inbox
- NOT an email client that users interact with directly (users send emails TO the agent, not THROUGH it)

### Architecture Implications:
```
┌─────────────────┐
│ External User   │
│ (alice@corp.com)│
└────────┬────────┘
         │ sends email TO agent
         ▼
┌─────────────────────────────────┐
│ AgentMail.to (Agent's Inbox)    │
│ agent@company.agentmail.to      │
└────────┬────────────────────────┘
         │ webhook POST to /webhook/email
         ▼
┌─────────────────────────────────┐
│ PRAXIS Agent                    │
│ - Triage (umans-flash)          │
│ - Context Load (Neo4j/Qdrant)   │
│ - ReAct (umans-coder)           │
│ - Tools (LDR, Hermes, AP, ACCU) │
└────────┬────────────────────────┘
         │ sends reply FROM agent's mailbox
         ▼
┌─────────────────────────────────┐
│ AgentMail API / SMTP            │
│ - Sends reply to alice@corp.com │
└─────────────────────────────────┘
```

This distinction is fundamental to understanding the entire system architecture, especially the email I/O layer described in Section 2.3.

---

## 0.5 Operating Models

PRAXIS supports two distinct deployment models. The **hosted model is the default for production** at this time.

### Model A: Hosted by Audit Intellect (Default Production)

**Description:** PRAXIS runs on infrastructure managed and maintained by Audit Intellect. Users provision agent instances through Audit Intellect's service, and Audit Intellect manages the underlying infrastructure.

**Architecture:**
- Centralized deployment on Audit Intellect's cloud infrastructure (e.g., AWS, GCP, Azure, or dedicated servers)
- Multi-tenant or single-tenant configurations per customer
- Audit Intellect manages: Umans API credentials, Neo4j/Qdrant instances, Postfix/IMAP infrastructure, monitoring, backups
- Users interact with PRAXIS via email only (no direct infrastructure access)
- Agent inboxes provisioned via AgentMail.to (Phase 1) or Audit Intellect's own domain (Phase 5+)

**Advantages:**
- Full control over infrastructure, updates, and security patches
- Centralized monitoring and observability
- Easier to enforce SLAs and performance guarantees
- Simplified user onboarding (just provision an agent inbox)
- Can optimize resource allocation across tenants

**Considerations:**
- Audit Intellect bears operational costs (compute, storage, API calls)
- Must handle scaling, high availability, and disaster recovery
- Users depend on Audit Intellect's infrastructure uptime
- Compliance and data residency requirements apply to Audit Intellect's infrastructure

**Typical Use Case:** SaaS offering where customers pay for agent services without managing infrastructure.

---

### Model B: Self-Hosted (User-Managed)

**Description:** Users deploy and operate their own PRAXIS instance on their own infrastructure. Audit Intellect provides the software, documentation, and support, but users manage the runtime environment.

**Architecture:**
- Distributed deployment on user's infrastructure (on-premises, private cloud, or user's cloud account)
- Single-tenant per deployment
- Users manage: Umans API credentials (or use their own), Neo4j/Qdrant instances, email infrastructure, monitoring
- Audit Intellect provides: Docker images, Helm charts, Terraform modules, documentation, support contracts
- Users have full access to logs, metrics, and configuration

**Advantages:**
- Users have full control over their data and infrastructure
- Easier to meet strict compliance requirements (HIPAA, SOC 2, GDPR data residency)
- Users can integrate with their existing email infrastructure (Exchange, Google Workspace)
- No dependency on Audit Intellect's infrastructure uptime
- Users can customize and extend the system

**Considerations:**
- Users bear operational burden (updates, scaling, monitoring, backups)
- Harder to enforce version consistency across deployments
- Support complexity increases (different environments, configurations)
- Users need DevOps expertise to maintain the system
- Harder to roll out new features quickly

**Typical Use Case:** Enterprise customers with strict compliance requirements, existing email infrastructure, or data sovereignty concerns.

---

### Model Selection Matrix

| Factor | Hosted by Audit Intellect (Default) | Self-Hosted |
|--------|------------------|-------------|
| **Infrastructure Ownership** | Audit Intellect | User |
| **Operational Responsibility** | Audit Intellect | User |
| **Data Residency** | Audit Intellect's infrastructure | User's infrastructure |
| **Compliance Complexity** | Managed by Audit Intellect | User manages |
| **Customization** | Limited (config only) | Full (code + config) |
| **Update Cadence** | Audit Intellect controls | User controls |
| **Cost Model** | Subscription/usage-based | License + support contract |
| **Support Complexity** | Lower (controlled env) | Higher (varied envs) |
| **Scalability** | Audit Intellect optimizes | User optimizes |
| **Email Infrastructure** | AgentMail.to or Audit Intellect's SMTP | User's SMTP/IMAP |
| **Umans API** | Audit Intellect's credentials | User's credentials |

---

### Current Production Strategy

**Default Model: Hosted by Audit Intellect**

At this stage, PRAXIS production deployments use the **hosted model** as the default. This approach:
- Allows rapid iteration and feature rollout
- Ensures consistent performance and reliability
- Simplifies support and troubleshooting
- Enables centralized observability and analytics
- Reduces friction for early adopters

**Self-hosted deployments** are available for enterprise customers who require:
- Strict data residency requirements
- Integration with existing email infrastructure
- Custom compliance frameworks
- Air-gapped or restricted network environments

The architecture described in this DRD supports both models. Key abstractions (EmailIOAdapter, UmansConcurrencyRouter, LangGraph state machine) are designed to be deployment-agnostic, allowing the same codebase to run in either model with configuration changes.

---

## 1. System Architecture Overview

*   **Orchestration:** LangGraph (Native Python)
*   **Inference Engine:** Umans API (Direct HTTP integration via LangChain-compatible clients)
*   **Concurrency Control:** Native `asyncio.Semaphore` Router
*   **Memory & Learning:** Hermes (Self-hosted Python service) + Neo4j (Graph) + Qdrant (Vector)
*   **Research:** Local Deep Research (Self-hosted Python service)
*   **Email I/O:** AgentMail (Phase 1) $\rightarrow$ Postfix/IMAP (Phase 2)

---

## 2. Phase 1: Foundation & Infrastructure (Weeks 1-2)

**Objective:** Establish the deployment environment, secure the inference pipeline, and implement basic email I/O.

### Tasks
1.  **Repository Initialization:**
    *   Initialize Python 3.12 project using `uv` or `poetry`.
    *   Configure `ruff` for linting and `pytest` for testing.
2.  **Umans Inference Router:**
    *   Implement `UmansConcurrencyRouter` using native `asyncio.Semaphore` (Limits: 4 for Kimi/GLM, 8 for Qwen).
    *   Create LangChain-compatible chat model wrappers for `umans-coder`, `umans-flash`, and `umans-glm-5.2`.
3.  **AgentMail Integration:**
    *   Implement FastAPI `/webhook/email` endpoint.
    *   Implement Svix-compatible webhook signature verification.
    *   Implement outbound email sending via `langchain-agentmail` (to be replaced in Phase 5).
4.  **Infrastructure as Code:**
    *   Draft `docker-compose.yml` for the core application, Neo4j, and Qdrant.

### Quality Gate 1 (Definition of Done)
*   [ ] FastAPI app boots and passes `/health` check.
*   [ ] Webhook endpoint accepts a mock AgentMail payload, verifies the signature, and returns `200 OK`.
*   [ ] `UmansConcurrencyRouter` successfully routes a test prompt to `umans-flash` and `umans-coder` while enforcing semaphore limits (verified via concurrent test suite).

---

## 3. Phase 2: Core Intelligence & Triage (Weeks 3-4)

**Objective:** Build the LangGraph state machine and implement the deterministic intelligence layer.

### Tasks
1.  **LangGraph State Definition:**
    *   Define `AgentState` TypedDict (including `email_content`, `triage_result`, `memory_context`, `tool_outputs`, `final_response`).
2.  **Triage Node (Powered by `umans-flash`):**
    *   Implement Pydantic models for `EmailTriage` (Priority, Spam, Sentiment, VIP Status, Intent).
    *   Build the Triage node using structured output (JSON mode).
    *   Implement conditional edges: Spam $\rightarrow$ Discard; Critical $\rightarrow$ High Priority Queue.
3.  **Context Loading Node:**
    *   Implement logic to query Neo4j for sender history.
    *   Inject retrieved context into the state before the ReAct loop.
4.  **ReAct Orchestrator Node (Powered by `umans-coder`):**
    *   Bind the Umans Kimi model to the LangGraph agent.
    *   Register the initial tool registry (stubbed).

### Quality Gate 2 (Definition of Done)
*   [ ] An inbound email triggers the Triage node and correctly outputs a structured JSON object (e.g., `{"priority": "high", "intent": "invoice_processing"}`).
*   [ ] The ReAct node successfully invokes a dummy tool and returns a structured response.
*   [ ] Graph state is correctly persisted and retrievable via LangGraph Checkpointer (PostgreSQL).

---

## 4. Phase 3: Tooling & Execution Layer (Weeks 5-6)

**Objective:** Integrate the Deep Research and Hermes layers natively, and finalize domain-specific tools.

### Tasks
1.  **Local Deep Research (LDR) Integration:**
    *   Deploy LDR as a local Python service (or import its core library).
    *   Configure LDR to use `umans-flash` for web search and `umans-kimi-k2.7` for synthesis via the Umans API.
    *   Wrap LDR as an asynchronous LangGraph tool.
2.  **Hermes Agent Integration:**
    *   Deploy Hermes as a local Python service.
    *   Configure Hermes to use `umans-glm-5.2` for state/memory and `umans-coder` for learning.
    *   Implement asynchronous wrappers for `hermes_recall`, `hermes_store`, and `hermes_learn`.
3.  **Domain Tools Implementation:**
    *   Implement ACCU modeling tools (local calculation + external pipeline wrapper).
    *   Implement Accounts Payable tools (upgrade from regex to LLM-based extraction using `umans-flash`).

### Quality Gate 3 (Definition of Done)
*   [ ] The ReAct agent can successfully invoke the LDR tool, wait for the asynchronous completion, and summarize the research report.
*   [ ] The agent can invoke `hermes_recall` to fetch context and `hermes_store` to save a fact.
*   [ ] All tools return strictly typed Pydantic models; no raw strings are passed back to the LLM.

---

## 5. Phase 4: Learning & Memory System (Weeks 7-8)

**Objective:** Enable the system to learn from corrections and maintain long-term enterprise context.

### Tasks
1.  **Neo4j Graph Schema Design:**
    *   Define nodes: `Person`, `Organization`, `Project`, `Thread`, `Correction`.
    *   Define relationships: `SENT_EMAIL`, `WORKS_FOR`, `HAS_CORRECTION`.
2.  **Correction Learning Loop:**
    *   Implement a specific tool: `record_correction`.
    *   When a user replies with "Correction: [feedback]", the Orchestrator routes this to `record_correction`.
    *   The tool updates Neo4j and sends a `hermes_learn` signal.
3.  **Context Injection Strategy:**
    *   Update the Context Loading Node to retrieve the top 5 most relevant `Correction` nodes based on the current email's intent and inject them into the ReAct system prompt.

### Quality Gate 4 (Definition of Done)
*   [ ] User sends an email correcting the agent's behavior.
*   [ ] The agent acknowledges the correction, executes the `record_correction` tool, and updates the graph.
*   [ ] In a subsequent, similar email, the agent retrieves the past correction from Neo4j and applies it automatically without being prompted again.

---

## 6. Phase 5: Enterprise Hardening & Migration (Weeks 9-10)

**Objective:** Implement advanced workflow features, security, and prepare for self-hosted email migration.

### Tasks
1.  **Asynchronous Workflow Engine:**
    *   Integrate Temporal.io (or Celery) to decouple the FastAPI webhook from the LangGraph execution.
    *   Implement "Out-of-Hours" logic: If priority != critical and time is outside business hours, schedule the execution via Temporal.
2.  **Auto Follow-Up System:**
    *   Implement a Temporal workflow that monitors sent emails. If no reply is received in $X$ hours, trigger a follow-up draft.
3.  **Security & PII Redaction:**
    *   Implement a pre-processing node using Microsoft Presidio (self-hosted) to mask PII before sending data to the Umans API.
4.  **Email Migration Prep:**
    *   Build the IMAP/SMTP adapter to replace `langchain-agentmail`.
    *   Ensure the webhook adapter can parse standard IMAP fetches into the `InboundEmail` schema.

### Quality Gate 5 (Definition of Done)
*   [ ] Webhook returns `202 Accepted` in < 100ms, while the Temporal worker processes the email asynchronously.
*   [ ] PII (emails, phone numbers, SSNs) in the email body is successfully masked in the logs and LLM prompts.
*   [ ] The system successfully processes 50 concurrent inbound emails without exceeding Umans concurrency limits (verified via load testing).

---

## 7. Immediate Next Steps

To begin this greenfield build from scratch, the immediate actions are:

1.  **Approve the Architecture:** Confirm that we are abandoning the CLI subprocess approach in favor of native Python/HTTP integration.
2.  **Environment Setup:** Provision the Umans API credentials and set up the local Docker environment for Neo4j and Qdrant.
3.  **Scaffold the Router:** Write the `UmansConcurrencyRouter` and the base FastAPI application.

Shall I generate the **Phase 1 boilerplate code** (including the FastAPI app, the Umans Router, and the Docker Compose file) so you can initialize the repository?

- **Generated:** Jun 19, 2026, 08:43 PM
- **Mode:** Detailed Report

---

# Table of Contents

1. **Architectural Verdict & Strategic Direction**
   1.1 Direct API vs. CLI Subprocess Evaluation | _Justify the rejection of CLI subprocesses based on latency overhead, brittle state management, and complex concurrency control._
   1.2 PRAXIS v2.0 Native Runtime Directive | _Define the mandate for a native asynchronous Python application with LDR and Hermes integrated as local services or via native HTTP/gRPC APIs._
2. **System Architecture & Component Integration**
   2.1 Orchestration, Inference, and Concurrency Control | _Detail the LangGraph state machine, Umans API wrappers, and the `UmansConcurrencyRouter` using native `asyncio.Semaphore`._
   2.2 Memory, Learning, and Deep Research Services | _Describe the integration of Hermes, Neo4j, Qdrant, and Local Deep Research as self-hosted Python services or libraries._
   2.3 Email I/O and Webhook Infrastructure | _Outline the AgentMail webhook implementation, Svix signature verification, and the migration path to Postfix/IMAP._
3. **Phased Development Roadmap & Execution Plan**
   3.1 Phase 1: Foundation, Router, and Basic I/O | _Establish repository structure, implement the Umans concurrency router, and secure the webhook endpoint._
   3.2 Phase 2: Core Intelligence, Triage, and ReAct Loop | _Implement the `AgentState` TypedDict, `umans-flash` triage node, Neo4j context loading, and `umans-coder` ReAct orchestrator._
   3.3 Phase 3: Native Tool Integration and Domain Logic | _Deploy LDR and Hermes as local services, configure model routing, and implement ACCU/AP domain tools with strict Pydantic typing._
   3.4 Phase 4: Graph Schema, Correction Learning, and Context Injection | _Design Neo4j correction nodes, implement the `record_correction` tool loop, and automate context injection from graph queries._
   3.5 Phase 5: Enterprise Hardening, Temporal Decoupling, and PII Security | _Integrate Temporal for async workflow decoupling, implement Microsoft Presidio for PII redaction, and prepare IMAP/SMTP adapters._
4. **Quality Gates and Validation Criteria**
   4.1 Phase-Specific Definition of Done Checklists | _Validate health checks, webhook signatures, router semaphore limits, structured JSON outputs, and tool return types per phase._
   4.2 Performance and Load Testing Requirements | _Verify concurrent email processing capacity, enforcement of Umans concurrency limits, and sub-100ms webhook latency._
5. **Immediate Next Steps and Initialization**
   5.1 Architecture Approval and Credential Provisioning | _Confirm the native Python architecture decision and provision Umans API credentials._
   5.2 Repository Scaffolding and Environment Setup | _Initialize the Python project with `uv`/`poetry`, configure `ruff`/`pytest`, and deploy Docker infrastructure for Neo4j and Qdrant._



# Research Summary

This report was researched using an advanced search system.

Research included targeted searches for each section and subsection.


---


# 1. Architectural Verdict & Strategic Direction

## 1.1 Direct API vs. CLI Subprocess Evaluation

_Justify the rejection of CLI subprocesses based on latency overhead, brittle state management, and complex concurrency control._


## 2.1 Direct API vs. CLI Subprocess Evaluation

This subsection provides the technical justification for rejecting the CLI subprocess architecture in favor of the Direct API/Python SDK approach. While CLI subprocesses offer process isolation, this benefit is outweighed by severe performance penalties, state fidelity loss, and concurrency management complexity that render the approach unsuitable for PRAXIS v2.0's enterprise requirements.

### 1. Latency & Overhead Analysis
The CLI approach introduces a non-trivial fixed cost per invocation due to OS-level process creation and Python interpreter initialization. For PRAXIS, where the orchestration loop involves frequent memory lookups, tool calls, and context injections, this overhead accumulates rapidly.

*   **Process Spawning Tax:** Spawning a new Python process involves `fork/exec` syscalls, memory allocation, and interpreter startup. Based on profiling of similar Python-based agent tools, the baseline overhead for process creation and interpreter warm-up ranges from **200ms to 500ms** per call.
*   **Throughput Impact:** In a high-frequency scenario, such as the **Context Loading Node** querying Neo4j for sender history or the **ReAct Orchestrator** making multiple tool calls per turn, this latency dominates execution time.
    *   *Example:* A native memory lookup might take ~5ms. A CLI-subprocess memory lookup takes ~300ms. This **60x degradation** turns the memory layer from a helpful context provider into a blocking bottleneck, destroying the agent's perceived responsiveness.
*   **Serialization Overhead:** Data must be serialized to JSON for `stdin` and deserialized from `stdout`. For complex payloads (e.g., LDR research reports or Hermes graph contexts), this serialization/deserialization cycle adds CPU overhead and further latency, particularly under concurrent load.

### 2. State Management & Fidelity
Passing the `AgentState` and component-specific contexts between the FastAPI orchestrator and CLI subprocesses via JSON requires flattening and serializing Python objects. This introduces brittleness and limits the depth of context available to components like Hermes and LDR.

*   **Type Fidelity Loss:** JSON does not support native Python types such as `datetime`, `UUID`, `set`, or custom Pydantic models. Passing complex state requires custom encoders/decoders, increasing code complexity and the risk of serialization errors.
    *   *Risk:* If a timestamp format changes or a custom object is added to the `AgentState`, the CLI subprocess will fail silently or throw deserialization errors, requiring versioned contracts between the main app and subprocesses.
*   **Context Depth Limitations:** Hermes requires access to rich graph traversal results and vector embeddings. LDR requires detailed search parameters and synthesis instructions.
    *   *Example:* A Neo4j result set containing nested relationships cannot be cleanly represented in JSON without significant flattening. Passing this via CLI forces the loss of relationship structure, requiring the CLI tool to reconstruct context heuristically, which is error-prone.
    *   *Constraint:* Circular references in the `AgentState` (e.g., a tool output referencing back to the parent state) break JSON serialization entirely, making deep state inspection impossible via CLI.

### 3. Concurrency Control Complexity
PRAXIS v2.0 relies on precise concurrency management via `asyncio.Semaphore` to protect inference endpoints (e.g., `UmansConcurrencyRouter`). Managing these semaphores across process boundaries is architecturally prohibitive.

*   **Semaphore Granularity:** A native `asyncio.Semaphore` ensures that only $N$ coroutines execute a task concurrently within the event loop.
    *   *CLI Failure Mode:* If each CLI invocation spawns a subprocess, the subprocess acquires its own independent semaphore or lacks synchronization entirely. To enforce limits, the orchestrator would need to implement a distributed lock (e.g., Redis-based or file-based locks) to coordinate subprocess spawning. This adds infrastructure dependency and race conditions.
*   **Async/Sync Boundary Violations:** CLI subprocesses are synchronous blocking operations. Integrating them requires offloading to a thread pool (`run_in_executor`) to avoid blocking the `asyncio` event loop. This fragments the concurrency model, making it difficult to reason about resource usage and leading to potential thread exhaustion.
*   **Resource Exhaustion:** OS limits on open files and processes (`ulimit`) constrain the number of concurrent subprocesses. Under load spikes (e.g., 50 concurrent emails), the system may hit process limits, causing `OSError: [Errno 24] Too many open files` or `Resource temporarily unavailable`, whereas native async tasks can scale efficiently within the single process memory space.

### Comparative Evaluation Matrix

| Metric | CLI Subprocess Approach | Direct API / Python SDK Approach | Impact on PRAXIS v2.0 |
| :--- | :--- | :--- | :--- |
| **Invocation Latency** | 200–500ms fixed overhead per call. | <5ms in-memory; ~50ms HTTP/gRPC. | CLI destroys throughput for high-frequency tools (Memory, Triage). |
| **State Transfer** | JSON via `stdin`/`stdout`. Loss of types, circular ref failures. | Object references. Full type safety, Pydantic validation. | CLI prevents passing rich Hermes/LDR contexts; requires brittle JSON contracts. |
| **Concurrency Model** | Requires distributed locks; blocks event loop via thread pools. | Native `asyncio.Semaphore`; co-operative multitasking. | CLI makes `UmansConcurrencyRouter` implementation fragile and resource-heavy. |
| **Debugging** | Requires log aggregation across process boundaries; stack traces split. | Unified stack traces; hot-reload support; native profiling. | CLI increases MTTR for production issues; hinders observability. |
| **Dependency Isolation** | High (process isolation). | Low (shared environment). | PRAXIS mitigates isolation needs via containerization and scoped services, making CLI isolation redundant. |

### Component-Specific Rejection Rationale

*   **Hermes Integration:** Hermes requires bidirectional, low-latency communication for `hermes_recall` and `hermes_store`. CLI subprocesses would serialize graph traversals to JSON, stripping relationship metadata essential for accurate memory retrieval. Direct API integration allows Hermes to return native Graph objects, preserving context depth.
*   **Local Deep Research (LDR):** LDR involves multi-step synthesis. CLI overhead would delay the feedback loop between search and synthesis steps. Direct integration allows LDR to yield control back to the LangGraph state machine without the latency penalty of process spawning, enabling faster research cycles.
*   **Umans Inference Router:** The router must enforce strict concurrency limits per model family. Direct API integration allows the router to manage semaphores natively, ensuring precise control over API rate limits. A CLI approach would require external coordination to prevent exceeding Umans quotas, introducing race conditions.

### Conclusion
The CLI subprocess approach introduces architectural debt that is incompatible with PRAXIS v2.0's requirements for low-latency orchestration, rich state management, and precise concurrency control. The **Direct API / Python SDK approach** eliminates serialization overhead, preserves type fidelity, enables native async concurrency, and simplifies the debugging surface. This evaluation confirms the directive to architect PRAXIS v2.0 as a native asynchronous Python application with LDR and Hermes integrated as local services or via native HTTP/gRPC APIs.



## 1.2 PRAXIS v2.0 Native Runtime Directive

_Define the mandate for a native asynchronous Python application with LDR and Hermes integrated as local services or via native HTTP/gRPC APIs._


## PRAXIS v2.0 Native Runtime Directive

Following the architectural verdict, this subsection establishes the non-negotiable operational and implementation mandates for the native asynchronous runtime. These directives govern how LDR and Hermes are embedded, how data flows across component boundaries, and how the async execution model must be structured to meet enterprise-grade reliability and performance standards.

### 1. Core Execution Environment Mandates
The PRAXIS v2.0 runtime must operate exclusively within a single, highly optimized `asyncio` event loop. All architectural decisions must align with the following baseline requirements:
*   **Python Version & Event Loop:** Python 3.12+ with `uvloop` as the default event loop implementation. `trio` or `anyio` may be used only for isolated worker pools.
*   **I/O Purity:** Strict prohibition of blocking operations. All network calls, database queries, and filesystem operations must use async-native clients (`httpx`, `neo4j.async`, `qdrant-client`, `asyncpg`). Synchronous libraries are permitted only within `run_in_executor()` boundaries with explicit timeout caps.
*   **Dependency Injection:** All LDR and Hermes instances must be injected via FastAPI `Depends()` or LangGraph `StateGraph` context. Hardcoded instantiations across the codebase are prohibited to enable mocking, version swapping, and graceful degradation.

### 2. LDR & Hermes Integration Architecture
LDR and Hermes must be integrated using a **tiered boundary model** that balances performance with operational isolation. The runtime must support both direct library invocation and async service boundaries without forcing architectural coupling.

| Integration Tier | Use Case | Communication Protocol | Lifecycle Management |
|------------------|----------|------------------------|----------------------|
| **Tier 1: Native Library** | Core memory operations, LDR synthesis loops, Hermes state updates | Direct Python function/method calls | Shared event loop; managed via `asyncio.TaskGroup` |
| **Tier 2: Async Service Boundary** | Horizontal scaling, isolated deployments, third-party dependency isolation | gRPC (preferred) or HTTP/REST via `httpx` | Connection pooling; circuit breakers; graceful shutdown hooks |

**Mandate:** All LDR and Hermes calls must be wrapped in structured concurrency constructs (`asyncio.TaskGroup` or `asyncio.gather`). Orphaned background tasks are prohibited. Each call must implement:
*   Explicit `timeout` (default: 15s for LDR synthesis, 8s for Hermes recall)
*   Cancellation propagation via `contextvars`
*   Fallback routing to cached or stubbed responses on timeout/circuit-open

### 3. Data Exchange & Contract Enforcement
To preserve type fidelity and eliminate serialization drift, all inter-component data exchange must adhere to strict contract boundaries:
*   **Pydantic v2 Models:** Every LDR and Hermes payload must be wrapped in a typed `BaseModel`. Raw dictionaries, strings, or JSON blobs crossing service boundaries are prohibited.
*   **Schema Registry:** A centralized `praxis_schemas/` module must house all shared contracts. LDR and Hermes payloads must include versioned `__version__` and `__checksum__` fields for backward compatibility.
*   **Context Propagation:** Trace context, user session IDs, and PII redaction flags must propagate natively via `contextvars` without manual payload injection.
*   **Example Contract:** 
  ```python
  class HermesContextPayload(BaseModel):
    entity_fragments: List[GraphFragment]
    vector_scores: Dict[str, float]
    temporal_decay_weight: float
    schema_version: str = "2.0.1"
  ```

### 4. Resource Lifecycle & Concurrency Governance
The native runtime must enforce deterministic resource management to prevent memory leaks, connection exhaustion, and event loop starvation:
*   **Connection Pooling:** All external services (Umans API, Neo4j, Qdrant, LDR/Hermes gRPC endpoints) must use configured connection pools (`httpx.AsyncClient` with `limits=ConnectionLimits()`, `grpc.aio.insecure_channel` with `keepalive`).
*   **Circuit Breaker Pattern:** Mandatory implementation of `tenacity` or `opentelemetry-instrumentation` circuit breakers for Umans inference calls. Thresholds: 50% error rate over 10s window triggers fallback to cached context or synchronous degradation path.
*   **Graceful Shutdown:** The FastAPI lifespan must register `SIGINT`/`SIGTERM` handlers that:
  1. Stop accepting new webhooks
  2. Drain existing LangGraph checkpoints
  3. Close all LDR/Hermes connections with `await asyncio.wait_for()`
  4. Flush OpenTelemetry exporters
*   **Structured Concurrency:** All Temporal worker threads, email polling loops, and background sync jobs must be parented to the main event loop. Task hierarchies must be visualized via `asyncio.all_tasks()` during health checks.

### 5. Observability & Debugging Protocol
Native async execution requires specialized observability to prevent black-box behavior and enable rapid triage:
*   **OpenTelemetry Instrumentation:** Mandatory tracing for all LDR synthesis cycles, Hermes recall fetches, and Umans inference calls. Spans must include `component.type` (e.g., `ldr.synthesis`, `hermes.recall`), `umans.model`, and `latency.p99`.
*   **Async-Aware Logging:** All loggers must use `loguru` or `structlog` with `asyncio`-compatible handlers. Synchronous `print()` or blocking `logging.info()` calls are prohibited.
*   **Metrics Targets:** 
  *   P50 LDR synthesis latency: < 2.5s
  *   P95 Hermes recall fetch: < 800ms
  *   Umans inference queue wait: < 200ms
  *   Event loop saturation warning: > 85% utilization over 30s window
*   **Debugging Standards:** All async tasks must carry `task_name` and `parent_id` metadata. `asyncio.sleep()` is prohibited; `await asyncio.sleep()` must be used exclusively. Stack traces must include `asyncio.Task.get_name()` and `contextvars` state.

### 6. Directive Enforcement & Compliance
Adherence to this native runtime directive is mandatory for all PRAXIS v2.0 code contributions. Compliance will be verified through:
1. **Static Analysis:** `ruff` rules `ASYNC1` (asyncio misuse) and `RUF015` (blocking I/O) enforced in CI.
2. **Runtime Profiling:** `py-spy` and `asyncio` event loop monitoring integrated into staging deployments.
3. **Contract Validation:** `pydantic` schema validation gates in all LDR/Hermes integration tests.
4. **Architecture Review:** Any deviation requiring synchronous fallbacks must be documented with latency impact analysis and approved by the core architecture team.

**Directive Summary:** PRAXIS v2.0 will operate as a tightly coupled, async-first Python runtime where LDR and Hermes are embedded as native services or invoked via optimized gRPC/HTTP boundaries. All execution paths must preserve type safety, enforce structured concurrency, and maintain enterprise-grade observability. This runtime mandate ensures the system scales predictably under load while eliminating the architectural debt introduced by subprocess orchestration.






# 2. System Architecture & Component Integration

## 2.1 Orchestration, Inference, and Concurrency Control

_Detail the LangGraph state machine, Umans API wrappers, and the `UmansConcurrencyRouter` using native `asyncio.Semaphore`._


## 3. Orchestration, Inference, and Concurrency Control

This subsection details the core architectural triad driving PRAXIS v2.0: the LangGraph orchestration engine, the direct Umans API inference layer, and the native concurrency control router. By leveraging Python's native asynchronous runtime, PRAXIS achieves deterministic state management, high-fidelity inference routing, and precise backpressure handling without the overhead of external process management.

### 3.1 Orchestration: LangGraph State Machine

PRAXIS utilizes LangGraph to define a deterministic, state-driven execution flow. The state machine is built around a strongly typed `AgentState` that persists across node transitions, enabling complex multi-turn reasoning, tool execution, and memory retrieval.

#### `AgentState` Schema
The `AgentState` is implemented as a `TypedDict` to ensure type safety and IDE support, while remaining compatible with LangGraph's serialization requirements. It serves as the single source of truth for every email processing cycle.

| Field | Type | Purpose |
| :--- | :--- | :--- |
| `email_content` | `dict` | Raw inbound email payload (headers, body, attachments metadata). |
| `triage_result` | `EmailTriage` | Structured Pydantic model containing priority, sentiment, intent, and VIP status. |
| `memory_context` | `list[dict]` | Retrieved context from Neo4j/Qdrant (sender history, past corrections, domain facts). |
| `tool_outputs` | `dict[str, Any]` | Aggregated results from LDR, Hermes, and domain-specific tools. |
| `corrections` | `list[str]` | Active correction rules retrieved from the graph for the current context. |
| `final_response` | `str` | Generated draft or action plan ready for user review or outbound routing. |
| `metadata` | `dict` | Execution timing, model versions, and cost tracking per node. |

#### Execution Flow & Persistence
The graph executes through three primary phases, governed by conditional edges and synchronous/asynchronous node definitions:

1.  **Triage Node:** A deterministic entry point powered by `umans-flash`. It enforces JSON-mode structured output to produce the `EmailTriage` model. Conditional edges route the state: `Spam` → discard, `Critical` → high-priority queue, `Standard` → context loading.
2.  **Context Loading Node:** An async node that queries Neo4j for sender history and Qdrant for semantic similarity. Results are injected into `memory_context` and `corrections` before the reasoning phase.
3.  **ReAct Orchestrator Node:** The core reasoning loop bound to `umans-coder`. It iteratively selects tools (LDR, Hermes, ACCU/AP calculators), executes them, and feeds outputs back into the prompt until a final state is reached.

**State Persistence:** LangGraph's PostgreSQL checkpointer is configured to persist `AgentState` snapshots after each node completion. This enables crash recovery, human-in-the-loop interruption, and auditability without manual state serialization.

### 3.2 Inference Engine: Umans API Wrappers

Direct HTTP integration via LangChain-compatible clients ensures low-latency communication with Umans inference endpoints. Wrappers are implemented as thin, async-first adapters that handle authentication, retry logic, and response parsing.

#### Model Routing & Capabilities
PRAXIS routes requests to specific Umans models based on task complexity, latency requirements, and cost constraints.

| Model | Primary Use Case | Integration Pattern | Key Configuration |
| :--- | :--- | :--- | :--- |
| `umans-flash` | Triage, PII extraction, AP regex replacement | LangChain `ChatModel` wrapper | `temperature=0`, `response_format={"type": "json_object"}` |
| `umans-coder` | ReAct reasoning, tool planning, complex synthesis | LangChain `ChatModel` wrapper | `max_tokens=4096`, streaming enabled for tool calls |
| `umans-glm-5.2` | Memory state management, Hermes learning loops | LangChain `ChatModel` wrapper | `temperature=0.3`, structured output for graph updates |

#### Structured Output Enforcement
To guarantee deterministic tool execution, all LLM calls targeting state mutations or tool parameters enforce strict Pydantic validation. The wrapper intercepts raw JSON responses, validates them against predefined models (e.g., `ToolCallSchema`, `CorrectionRecord`), and raises parsing errors immediately if schema drift occurs. This prevents malformed tool invocations from corrupting the graph state.

#### PII Pre-Processing Pipeline
Before any prompt reaches the Umans wrappers, a synchronous pre-processing node runs Microsoft Presidio. Sensitive entities (emails, phone numbers, SSNs, financial IDs) are masked with tokens (e.g., `[EMAIL_PII]`). The masking schema is preserved in the `AgentState` so that downstream tools and final responses can safely unmask or redact data according to enterprise compliance policies.

### 3.3 Concurrency Control: `UmansConcurrencyRouter`

PRAXIS v2.0 implements a native `UmansConcurrencyRouter` to manage API rate limits, prevent backend throttling, and ensure stable throughput under concurrent load. Unlike process-based semaphores, this router operates entirely within the FastAPI `asyncio` event loop.

#### Native Semaphore Implementation
The router instantiates `asyncio.Semaphore` objects scoped to specific model families. Coroutines requesting inference must `await` the semaphore before initiating an HTTP connection. This guarantees that the number of active, in-flight requests never exceeds the configured threshold.

```python
class UmansConcurrencyRouter:
    def __init__(self):
        self.semaphores = {
            "umans-kimi": asyncio.Semaphore(4),
            "umans-glm": asyncio.Semaphore(4),
            "umans-qwen": asyncio.Semaphore(8),
        }
        self.client = httpx.AsyncClient(timeout=30.0)

    async def invoke(self, model_name: str, prompt: str) -> dict:
        async with self.semaphores[model_name]:
            response = await self.client.post(
                f"{UMANS_API_URL}/v1/chat/completions",
                json={"model": model_name, "messages": prompt}
            )
            return response.json()
```

#### Backpressure & Queue Management
When the semaphore limit is reached, incoming coroutines naturally queue within the event loop rather than blocking threads or spawning processes. FastAPI's async request handlers pass execution to the router, which applies backpressure transparently. This ensures that:
*   **No Rate Limit Errors:** Outbound requests are strictly capped below Umans API thresholds.
*   **Predictable Latency:** Queue wait times are linear and observable via `metadata.execution_time`.
*   **Resource Efficiency:** Memory footprint remains flat regardless of concurrency, as coroutines yield control during I/O waits rather than holding OS-level process resources.

#### Verification & Load Testing
Concurrency limits are validated through concurrent test suites that simulate 50+ simultaneous inbound emails. The router's internal metrics are exposed via Prometheus-compatible endpoints, allowing real-time monitoring of semaphore utilization, queue depth, and average wait times per model family.



## 2.2 Memory, Learning, and Deep Research Services

_Describe the integration of Hermes, Neo4j, Qdrant, and Local Deep Research as self-hosted Python services or libraries._


## Memory, Learning, and Deep Research Services

In PRAXIS v2.0, memory, learning, and research capabilities are centralized within the native Python ecosystem. **Hermes** (Memory/Learning), **Local Deep Research (LDR)**, **Neo4j** (Graph), and **Qdrant** (Vector) are integrated as self-hosted Python services or directly imported libraries. This architecture eliminates serialization bottlenecks, enables shared concurrency control, and enforces strict type safety across all memory and research operations.

### 1. Hermes: Stateful Memory & Learning Service

Hermes acts as the enterprise memory core, managing long-term context, sender relationships, and corrective learning. It is deployed as a native Python service that exposes asynchronous tools to the LangGraph orchestrator.

*   **Service Integration:** Hermes is invoked via native async wrappers (`hermes_recall`, `hermes_store`, `hermes_learn`) that bypass HTTP overhead when running as a library or utilize high-performance async HTTP/gRPC when isolated.
*   **Model Routing:**
    *   **State & Memory:** Utilizes `umans-glm-5.2` for semantic recall synthesis and memory consolidation.
    *   **Learning:** Utilizes `umans-coder` to parse unstructured user corrections into structured facts for graph updates.
*   **Shared Concurrency:** Hermes leverages the global `UmansConcurrencyRouter`, ensuring that memory retrieval and learning operations respect the same semaphore limits as the main orchestrator (e.g., 4 concurrent slots for GLM/Coder).
*   **Key Operations:**
    *   `hermes_recall(query, context)`: Retrieves relevant graph paths and vector embeddings, synthesizing a concise context summary for the ReAct loop.
    *   `hermes_store(fact, metadata)`: Persists new facts to Neo4j and Qdrant with timestamped metadata.
    *   `hermes_learn(correction_text)`: Processes user feedback (e.g., "Correction: The client prefers PDF invoices") and triggers graph updates.

### 2. Hybrid Knowledge Architecture: Neo4j & Qdrant

PRAXIS employs a dual-storage strategy to maximize retrieval accuracy. **Neo4j** handles structured relationships and explicit corrections, while **Qdrant** provides semantic vector recall. Hermes orchestrates queries across both stores.

#### Neo4j Graph Schema & Learning Loop
The Neo4j graph captures explicit enterprise relationships and correction history. The **Correction Learning Loop** is a critical workflow where user feedback is formalized into graph nodes, ensuring future interactions adapt automatically.

| Node/Relationship | Attributes | Learning Trigger | Context Injection Strategy |
| :--- | :--- | :--- | :--- |
| **Person** | `name`, `email`, `role`, `preferences` | `hermes_store` or `record_correction` | Injected during Triage; used for sender history lookup. |
| **Organization** | `name`, `industry`, `contract_status` | `hermes_store` | Injected for domain context in ACCU/AP tools. |
| **Thread** | `thread_id`, `subject`, `last_updated` | Email Inbound | Links `Person` to `Organization`; tracks conversation state. |
| **Correction** | `text`, `timestamp`, `related_intent`, `resolved` | User Reply: "Correction: ..." | **Top-5 Retrieval:** Retrieved by intent match and injected into ReAct system prompt. |
| **HAS_CORRECTION** | `applied_at` | Graph Update | Links `Person` or `Project` to specific `Correction` nodes. |

*   **Correction Workflow:** When the Orchestrator detects a correction pattern in a user reply, it routes to the `record_correction` tool. This tool creates a `Correction` node in Neo4j and signals `hermes_learn`. In subsequent emails matching the `related_intent`, the Context Loading Node retrieves the top 5 relevant corrections and injects them into the prompt, enabling zero-shot adaptation.

#### Qdrant Vector Store
*   **Role:** Stores dense vector embeddings for semantic memory retrieval.
*   **Integration:** Hermes generates embeddings via `umans-glm-5.2` and pushes payloads to Qdrant collections.
*   **Recall:** `hermes_recall` performs hybrid search, combining vector similarity scores with Neo4j graph filters (e.g., "Recall memories for `Project: Alpha` with semantic similarity > 0.85").

### 3. Local Deep Research (LDR) Pipeline

LDR provides autonomous web research capabilities, wrapped as an asynchronous LangGraph tool. It is configured to operate as a local Python service or library, communicating directly with Umans inference endpoints.

*   **Research Workflow:**
    1.  **Search Phase:** LDR invokes `umans-flash` to perform web searches and extract URLs based on the research query.
    2.  **Synthesis Phase:** LDR aggregates extracted content and invokes `umans-kimi-k2.7` to synthesize a comprehensive research report.
    3.  **Tool Output:** LDR returns a structured Pydantic model containing the report, key findings, and source citations.
*   **Integration Details:**
    *   **Async Execution:** LDR is non-blocking. The ReAct Orchestrator calls `deep_research(topic, depth)` and yields control to the event loop until LDR completes synthesis.
    *   **Model Routing:** LDR uses dedicated semaphore slots for `umans-flash` (search) and `umans-kimi-k2.7` (synthesis), managed via the shared `UmansConcurrencyRouter`.
    *   **Tool Definition:**
        ```python
        class LDRResult(BaseModel):
            report: str
            key_findings: List[str]
            sources: List[SourceCitation]
            confidence_score: float
        ```

### 4. Integration Architecture & Data Contracts

The native integration approach enables direct object passing and runtime validation, replacing brittle JSON serialization with strict Python type contracts.

#### Component Integration Matrix

| Component | Type | Storage/Backend | Models Used | Integration Method | Key Functions/APIs |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Hermes** | Python Service/Library | Neo4j, Qdrant | `umans-glm-5.2`, `umans-coder` | Native Async Wrappers / HTTP | `recall()`, `store()`, `learn()` |
| **LDR** | Python Service/Library | Web (External) | `umans-flash`, `umans-kimi-k2.7` | Async LangGraph Tool | `deep_research(query, depth)` |
| **Neo4j** | Graph Database | Neo4j Instance | N/A | Bolt Driver / HTTP | Query nodes/rels for context & corrections |
| **Qdrant** | Vector Database | Qdrant Instance | N/A | HTTP/GRPC Client | Vector search & embedding storage |
| **Umans Router** | Concurrency Manager | N/A | All Umans Models | Shared `asyncio.Semaphore` | Global quota enforcement for all services |

#### Data Flow & Validation
*   **Pydantic Enforcement:** All tool outputs from Hermes and LDR are validated against Pydantic models before returning to the Orchestrator. This prevents malformed context from polluting the LLM prompt.
*   **Context Injection:** The Context Loading Node retrieves data from Neo4j and Qdrant via Hermes, formats the result into a structured JSON block, and injects it into the `AgentState`. This ensures the ReAct node receives high-fidelity, typed context without parsing raw strings.
*   **Error Handling:** Native integration allows for immediate exception propagation. If Neo4j is unreachable or Qdrant returns low confidence, Hermes raises a typed error that the Orchestrator can handle gracefully (e.g., falling back to a generic prompt or retrying with reduced scope).

This architecture ensures that memory retrieval and deep research operations are fast, reliable, and fully integrated into the PRAXIS v2.0 async runtime, supporting the enterprise requirements for low-latency context access and robust continuous learning.



## 2.3 Email I/O and Webhook Infrastructure

_Outline the AgentMail webhook implementation, Svix signature verification, and the migration path to Postfix/IMAP._


## Email I/O and Webhook Infrastructure

### 1. Inbound Webhook Architecture & Svix Verification
The inbound email ingestion pipeline relies on a high-throughput FastAPI endpoint (`POST /webhook/email`) designed to absorb external payloads while enforcing strict security and idempotency boundaries.

*   **Svix Signature Verification:** All inbound payloads are verified against Svix-compatible HMAC-SHA256 signatures. The verification pipeline extracts three critical headers:
    *   `svix-id`: Unique event identifier used for deduplication.
    *   `svix-timestamp`: Epoch timestamp to enforce a strict **5-minute drift tolerance**. Requests outside this window are rejected to prevent replay attacks.
    *   `svix-signature`: The HMAC signature computed over the raw payload body and timestamp.
    *   *Verification Logic:* The endpoint computes `hmac.new(SECRET_KEY, f"{svix_id}.{svix_timestamp}".encode(), sha256).hexdigest()` and performs a constant-time comparison against the provided signature. Mismatches trigger an immediate `401 Unauthorized` response without payload processing.
*   **Payload Normalization & Idempotency:** Upon successful verification, the raw JSON is deserialized into the `InboundEmail` schema. The `svix-id` is immediately hashed and stored in a short-lived Redis cache (TTL: 24h). Subsequent requests bearing the same ID return `200 OK` silently without re-entering the execution pipeline, neutralizing duplicate deliveries caused by network retries or webhook retry policies.
*   **Latency SLA:** The endpoint must return `202 Accepted` within **<100ms**. All heavy lifting (LLM routing, memory injection, tool execution) is offloaded to an async message queue (Temporal/Celery) to prevent webhook timeout cascades.

### 2. Outbound Email Routing: AgentMail Abstraction Layer (Phase 1)
During Phase 1, outbound communication is routed through the `langchain-agentmail` SDK, which serves as a managed abstraction layer over traditional SMTP/IMAP protocols.

*   **Managed Protocol Handling:** The AgentMail wrapper handles TLS 1.3 handshakes, MIME multipart construction, SPF/DKIM signing, and delivery receipt polling. This eliminates the need to maintain low-level socket connections or certificate rotation logic during early development.
*   **Rate Limiting & Backpressure:** The outbound client is wrapped in a token-bucket rate limiter aligned with enterprise sending quotas (e.g., 50 emails/min per tenant). Excess requests are queued in memory with a graceful `429 Too Many Requests` fallback that schedules retries via the same async queue used for inbound processing.
*   **Delivery Telemetry:** AgentMail exposes webhook callbacks for bounce, open, and click events. These callbacks are routed to a separate `/webhook/email-events` endpoint, parsed into `DeliveryStatus` models, and forwarded to the Temporal workflow engine for auto-follow-up triggers and PII-safe audit logging.

### 3. Migration Strategy: Self-Hosted Postfix/IMAP Transition (Phase 5)
Phase 5 introduces a decoupled, self-hosted mail stack to eliminate third-party SDK dependencies and enable full control over routing, compliance, and scaling.

*   **Adapter Protocol Design:** A strict `EmailIOAdapter` protocol is defined to abstract all mail operations:
    ```python
    class EmailIOAdapter(Protocol):
        async def send(self, payload: OutboundEmail) -> str: ...
        async def listen(self, callback: Callable[[InboundEmail], None]) -> None: ...
        async def close(self) -> None: ...
    ```
*   **IMAP Polling & RFC822 Normalization:** The inbound path replaces the webhook dependency with an async IMAP connector (`aioimaplib` or `asyncimap`). The adapter:
    1.  Authenticates via OAuth2 or application-specific passwords.
    2.  Polls the `INBOX` and `Archive` folders at configurable intervals (default: 30s).
    3.  Fetches unread messages, parses RFC822 headers/body, and strips/normalizes quoted replies and signatures.
    4.  Transforms raw MIME data into the canonical `InboundEmail` schema, injecting `source: "imap"` and `message_id` from the `Message-ID` header.
*   **Postfix SMTP Relay:** Outbound emails are routed to a local Postfix instance. The FastAPI application pushes messages to a local `smtp://localhost:587` relay. Postfix handles DNS MX lookups, TLS opportunistic encryption, and queue management. This decouples PRAXIS from external SMTP providers while maintaining enterprise deliverability.
*   **Dual-Stack Rollout:** Migration employs a feature-flagged rollout (`use_native_mail`). Both AgentMail and Postfix/IMAP adapters are registered simultaneously. Traffic is split 100% → AgentMail → 100% → Native over 3 sprints. Legacy webhook endpoints are disabled only after IMAP polling stability is verified across all tenant mailboxes.

### 4. Reliability, Observability & Compliance Controls
*   **PII Redaction at Edge:** All email bodies and headers pass through Microsoft Presidio before reaching LLM contexts. Redacted fields are logged with placeholder tokens (e.g., `[EMAIL_REDACTED]`) to maintain audit trails without exposing sensitive data.
*   **Retry & Dead-Letter Strategy:** Failed SMTP deliveries or IMAP sync errors trigger exponential backoff (initial: 2s, max: 4h). After 3 failed attempts, messages are routed to a `mail_dead_letter` queue with full payload preservation for manual review.
*   **Structured Telemetry:** Every I/O event emits metrics to Prometheus: `email_webhook_received_total`, `email_imap_poll_duration_seconds`, `email_smtp_delivery_latency_ms`, and `email_signature_verification_failures`. All logs are trace-correlated via OpenTelemetry context propagation.

### Infrastructure Evolution Matrix
| Dimension | Phase 1: AgentMail + Webhooks | Phase 5: Postfix/IMAP Native |
|-----------|-------------------------------|------------------------------|
| **Inbound Protocol** | HTTP POST webhook (Svix-verified) | Async IMAP polling + RFC822 parsing |
| **Outbound Protocol** | Managed `langchain-agentmail` SDK | Local Postfix SMTP relay + DNS MX routing |
| **Latency Profile** | Sub-100ms acceptance (async offload) | ~200-500ms IMAP sync + immediate SMTP push |
| **Scalability** | Provider-limited quotas & rate limits | Horizontal Postfix queue scaling + IMAP connection pooling |
| **PII/Compliance** | Relies on provider redaction + Presidio | Full in-house control; configurable retention & purge policies |
| **Operational Overhead** | Low (managed service) | Medium (self-hosted stack, certificate rotation, queue tuning) |
| **Migration Trigger** | Base deployment | Enterprise compliance requirement / cost optimization |

---
**Research Note:** The specific `langchain-agentmail` implementation details could not be verified via external search, as the package appears to be an internal or abstracted service name within this architecture. The implementation outline above is derived strictly from the provided DRD specifications, standard enterprise webhook/security patterns (Svix/HMAC), and proven async IMAP/SMTP integration practices. All described mechanisms align with the native async runtime directives and Phased Development Roadmap outlined in the broader report.






# 3. Phased Development Roadmap & Execution Plan

## 3.1 Phase 1: Foundation, Router, and Basic I/O

_Establish repository structure, implement the Umans concurrency router, and secure the webhook endpoint._


---

## 4. Phase 1: Foundation, Router, and Basic I/O

Phase 1 establishes the operational backbone of PRAXIS v2.0. This phase is purely infrastructural: repository scaffolding, concurrency-safe inference routing, webhook ingestion, and the Docker-based deployment topology. No LangGraph state machines or domain logic are implemented at this stage—only the plumbing that enables them.

### 4.1 Repository Initialization and Toolchain

PRAXIS v2.0 is initialized as a Python 3.12 project using `uv` for dependency resolution and virtual environment management, chosen over `poetry` for its significantly faster install times and compatibility with the `uv` ecosystem. The project structure follows a modular layout:

```
praxis/
├── src/
│   ├── praxis/
│   │   ├── __init__.py
│   │   ├── app.py              # FastAPI application
│   │   ├── router/             # Umans concurrency router
│   │   ├── webhooks/           # AgentMail webhook handlers
│   │   └── models/             # Pydantic schemas
│   └── tests/
├── docker-compose.yml
├── pyproject.toml
└── Dockerfile
```

**Configuration and linting** are managed through `ruff`, configured in `pyproject.toml` with the following ruleset:

| Category | Rule | Purpose |
|----------|------|---------|
| `F` | All | Pyflakes: catch unused imports, undefined names |
| `E` | All | pycodestyle errors: enforce PEP 8 compliance |
| `W` | E501 | Line length: 120 character limit |
| `I` | All | isort: deterministic import ordering |
| `UP` | All | pyupgrade: auto-upgrade to Python 3.12+ syntax |

Testing is configured with `pytest` using `pytest-asyncio` for async test fixtures. Test isolation is achieved through a shared `pytest.ini` that enables `asyncio_mode = auto` and configures a temporary PostgreSQL instance via `pytest-postgresql` for Quality Gate 2 checkpointer tests.

### 4.2 Umans Concurrency Router

The `UmansConcurrencyRouter` is the gatekeeper for all inference requests, enforcing per-model semaphore limits to prevent API quota exhaustion and ensure predictable latency. It is implemented as a singleton `asyncio.Semaphore` manager that wraps every outbound request to the Umans API.

#### Semaphore Configuration

Each model family has its own semaphore with a limit tuned to the model's capacity and the enterprise API tier:

| Model | Semaphore Limit | Rationale |
|-------|-----------------|-----------|
| `umans-flash` | 8 | High-throughput model; supports parallel batch requests |
| `umans-coder` | 4 | Computationally intensive; benefits from serialized execution |
| `umans-glm-5.2` | 4 | GLM family; conservative limit to avoid rate-limiting |

#### Router Implementation

The router exposes a single async context manager `acquire(model_name: str)` that yields a semaphore-wrapped request handler:

```python
class UmansConcurrencyRouter:
    _semaphores: dict[str, asyncio.Semaphore] = {
        "umans-flash": asyncio.Semaphore(8),
        "umans-coder": asyncio.Semaphore(4),
        "umans-glm-5.2": asyncio.Semaphore(4),
    }

    @classmethod
    async def invoke(cls, model_name: str, messages: list[dict], **kwargs) -> dict:
        if model_name not in cls._semaphores:
            raise ValueError(f"Unknown model: {model_name}")

        async with cls._semaphores[model_name]:
            response = await cls._call_umans_api(model_name, messages, **kwargs)
            return response
```

The underlying `_call_umans_api` method uses `httpx.AsyncClient` with exponential backoff retry logic (up to 3 retries, base delay 500ms) and automatic JSON response parsing. All responses are logged with timing metadata for the `AgentState.metadata` field.

#### Concurrency Verification

Quality Gate 1 requires a concurrent test suite that verifies semaphore enforcement. The test spawns 16 concurrent tasks targeting `umans-flash` and asserts that no more than 8 execute simultaneously, measured via a thread-safe counter incremented at entry and decremented at exit within the semaphore context.

### 4.3 AgentMail Webhook Integration

Phase 1 implements email ingestion via AgentMail's webhook system, with Svix-compatible signature verification as a security prerequisite. The endpoint is exposed as `POST /webhook/email` on the FastAPI application.

#### Inbound Payload Schema

AgentMail sends webhook payloads in the following structure:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `id` | `str` | Yes | Unique webhook event ID |
| `type` | `str` | Yes | Always `email.received` |
| `timestamp` | `datetime` | Yes | ISO 8601 event timestamp |
| `data` | `dict` | Yes | Nested email payload |
| `data.from` | `str` | Yes | Sender email address |
| `data.to` | `list[str]` | Yes | Recipient addresses |
| `data.subject` | `str` | Yes | Email subject line |
| `data.body` | `str` | Yes | Plain-text body |
| `data.html_body` | `str` | Optional | HTML body (if available) |
| `data.attachments` | `list[dict]` | No | Attachment metadata (filename, size, content_type) |

#### Svix Signature Verification

Svix-compatible verification is implemented by extracting the `Svix-Signature` header and validating the HMAC-SHA256 signature against the stored webhook secret:

```python
async def verify_webhook_signature(payload: bytes, headers: dict, secret: str) -> bool:
    signature = headers.get("Svix-Signature", "")
    timestamp = headers.get("Svix-Timestamp", "")
    
    if not signature or not timestamp:
        return False
    
    message = SvixMessage(id="", timestamp=timestamp, payload=payload, type="")
    verifier = SvixVerifier(secret)
    return verifier.verify(payload, message, signature)
```

The verification function enforces a 5-minute clock skew tolerance to account for minor time drift between AgentMail's servers and the PRAXIS instance. Requests that fail verification return `401 Unauthorized` with no payload details.

#### Outbound Email via `langchain-agentmail`

Phase 1 uses `langchain-agentmail` for outbound email responses. This is explicitly marked as a temporary integration to be replaced in Phase 5 with a self-hosted Postfix/IMAP adapter. The outbound flow is:

1. FastAPI receives the webhook → verifies signature → enqueues the event
2. The `UmansConcurrencyRouter` routes a triage request (not yet implemented) to `umans-flash`
3. A response draft is generated and sent via `langchain-agentmail`'s async `send_email` method
4. The endpoint returns `200 OK` with the event ID

### 4.4 Infrastructure as Code: Docker Compose

The `docker-compose.yml` provisions three services: the PRAXIS application, Neo4j for graph storage, and Qdrant for vector search.

#### Service Definitions

| Service | Image | Ports | Volumes | Environment |
|---------|-------|-------|---------|-------------|
| `praxis-app` | `praxis:latest` | 8000:8000 | `./src:/app/src` (dev bind mount) | `UMANS_API_KEY`, `SVIX_WEBHOOK_SECRET` |
| `neo4j` | `neo4j:5.20` | 7687:7687, 7474:7474 | `neo4j-data:/data` | `NEO4J_AUTH=neo4j/praxis-dev-password` |
| `qdrant` | `qdrant/qdrant:1.9` | 6333:6333 | `qdrant-data:/qdrant/storage` | `QDRANT_SERVICE_API_KEY=praxis-dev-key` |

#### Neo4j Configuration

Neo4j is configured with a dedicated database `praxis_graph` and the following JVM tuning for Phase 1:

```yaml
NEO4J_dbms_memory_pagecache_size: 512M
NEO4J_dbms_memory_heap_max__size: 1G
NEO4J_apoc_export_file_enabled: true
NEO4J_apoc_import_file_enabled: true
NEO4J_apoc_import_file_use__neo4j__config: true
```

The APOC procedures are enabled for later-phase graph queries (Phase 4 correction learning). Neo4j Browser is exposed on port 7474 for ad-hoc schema inspection during development.

#### Qdrant Configuration

Qdrant is initialized with a single collection `email_contexts` configured for cosine similarity distance and a vector dimension of 1536 (compatible with OpenAI-compatible embeddings that Hermes will use in Phase 3). The collection is created via an initialization script that runs on container startup:

```python
# init_qdrant.py
import qdrant_client

client = qdrant_client.QdrantClient(url="http://qdrant:6333", api_key="praxis-dev-key")
client.recreate_collection(
    collection_name="email_contexts",
    vectors_config=qdrant_client.models.VectorParams(
        size=1536,
        distance=qdrant_client.models.Distance.COSINE
    )
)
```

### 4.5 Quality Gate 1: Definition of Done

Phase 1 is considered complete when all three criteria are verified by the automated test suite:

| Criterion | Test Method | Pass Condition |
|-----------|-------------|----------------|
| FastAPI health check | `GET /health` | Returns `{"status": "ok", "version": "2.0.0"}` with HTTP 200 |
| Webhook ingestion | Mock AgentMail payload + valid `Svix-Signature` header | Returns `200 OK` with event ID in response body |
| Router concurrency | 16 concurrent `umans-flash` requests via `pytest-asyncio` | Max concurrent executions ≤ 8; all 16 complete without error |

Upon passing Quality Gate 1, the repository is considered ready for Phase 2 development, which introduces the LangGraph state machine and triage logic.

---

*This subsection covers repository initialization, the `UmansConcurrencyRouter` implementation, AgentMail webhook integration with Svix verification, and the Docker Compose infrastructure. Phase 2 will build on this foundation by implementing the LangGraph state machine and triage node.*



## 3.2 Phase 2: Core Intelligence, Triage, and ReAct Loop

_Implement the `AgentState` TypedDict, `umans-flash` triage node, Neo4j context loading, and `umans-coder` ReAct orchestrator._


## Phase 2: Core Intelligence, Triage, and ReAct Loop

This phase establishes the deterministic intelligence layer of PRAXIS v2.0 by implementing the LangGraph state machine. The focus is on defining the shared state, routing inbound traffic via the `umans-flash` triage node, enriching context from Neo4j, and orchestrating complex tool execution via the `umans-coder` ReAct agent.

### 1. LangGraph State Definition (`AgentState`)

The foundation of the PRAXIS workflow is the `AgentState` TypedDict, which serves as the single source of truth for the execution graph. It ensures type safety across all nodes and enables seamless state persistence via the LangGraph Checkpointer (PostgreSQL).

```python
from typing import TypedDict, Annotated, List
from langgraph.graph.message import add_messages
from pydantic import BaseModel

class AgentState(TypedDict):
    # Core Email Data
    email_content: str
    sender_email: str
    thread_id: str
    
    # Triage Results
    triage_result: Annotated[list, "EmailTriage"]
    
    # Memory Context
    memory_context: str
    
    # Tool Execution
    tool_outputs: List[dict]
    
    # Final Output
    final_response: str
```

*Note: The `add_messages` utility is integrated to maintain conversation history within the ReAct loop, while `AgentState` tracks the structural flow of the workflow.*

### 2. Triage Node: Deterministic Routing with `umans-flash`

The Triage Node is the entry point for all inbound emails. It leverages `umans-flash` for its low-latency and high-throughput capabilities, utilizing structured output (JSON mode) to enforce strict typing.

**Implementation Strategy:**
*   **Model Binding:** Bind `umans-flash` to a LangChain `ChatModel` with `response_format={"type": "json_object"}`.
*   **Pydantic Schema:** Define `EmailTriage` to standardize the output.

```python
from enum import Enum

class Priority(str, Enum):
    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"

class Intent(str, Enum):
    INVOICE_PROCESSING = "invoice_processing"
    SCHEDULE_MEETING = "schedule_meeting"
    GENERAL_INQUIRY = "general_inquiry"
    CORRECTION = "correction"

class EmailTriage(BaseModel):
    priority: Priority = Field(description="Priority level based on sender VIP status and content urgency")
    intent: Intent = Field(description="Primary intent of the email")
    sentiment: str = Field(description="Sentiment analysis (positive, neutral, negative)")
    is_spam: bool = Field(description="True if the email is identified as spam or phishing")
    sender_vip: bool = Field(description="True if the sender is in the VIP list")
```

*   **Conditional Edges:** Implement a `route_triage` function that checks the `triage_result` to determine the next node in the graph.

| Triage Outcome | Condition | Next Node | Action |
| :--- | :--- | :--- | :--- |
| **Discard** | `is_spam == True` | `discard_node` | Log spam event; return `200 OK` to sender. |
| **High Priority** | `priority == "high"` | `high_priority_queue` | Trigger immediate ReAct execution; notify admin. |
| **Standard Processing** | `priority == "normal"` | `context_loading_node` | Proceed to Neo4j lookup and ReAct loop. |
| **Correction Detected** | `intent == "correction"` | `correction_handler` | Route to `record_correction` tool (Phase 4). |

### 3. Context Loading Node: Neo4j Integration

Before the ReAct loop begins, the system must enrich the prompt with relevant historical data. This node queries Neo4j to retrieve sender history and past interactions, ensuring the agent has the necessary background to avoid repetitive questions.

**Implementation Steps:**
1.  **Neo4j Query Builder:** Construct a Cypher query to fetch the top 3 most recent `Thread` nodes associated with the `sender_email` from the `AgentState`.
    ```cypher
    MATCH (p:Person {email: $sender_email})-[r:SENT_EMAIL]->(t:Thread)
    RETURN t.subject AS subject, t.body AS body, t.timestamp AS timestamp
    ORDER BY t.timestamp DESC
    LIMIT 3
    ```
2.  **Context Synthesis:** Transform the query results into a concise string format.
3.  **State Update:** Inject the synthesized context into `AgentState["memory_context"]`.
    *   *Example Output:* `"[Sender History] Subject: Q3 Invoices, Date: 2024-05-01... Subject: Contract Renewal, Date: 2024-04-15..."`

This step is critical for providing the ReAct orchestrator with the necessary background to understand the broader context of the current email and to tailor the response accordingly.

### 4. ReAct Orchestrator Node: `umans-coder` Integration

The ReAct Orchestrator is responsible for executing complex tasks, such as web research or data extraction, using the `umans-coder` model. This node leverages the ReAct (Reason + Act) pattern, allowing the model to interleave thought, action, and observation steps.

**Implementation Strategy:**
*   **Tool Registry:** Register domain-specific tools (e.g., `web_search`, `extract_invoice_data`) with strict Pydantic input/output schemas.
*   **Prompt Engineering:** Construct a system prompt that includes:
    *   The original `email_content`.
    *   The `memory_context` loaded from Neo4j.
    *   The `triage_result` to guide the model's focus (e.g., "Focus on extracting invoice details").
*   **ReAct Loop Execution:**
    1.  **Thought:** The model analyzes the task and decides on an action.
    2.  **Action:** The model calls a registered tool (e.g., `web_search(query="latest ACCU pricing")`).
    3.  **Observation:** The tool returns the result, which is appended to the state.
    4.  **Repeat:** The loop continues until the model determines it has sufficient information to generate a `final_response`.

**Concurrency Handling:**
Since `umans-coder` is used for complex reasoning, it may take longer to respond. The ReAct Orchestrator must integrate with the `UmansConcurrencyRouter` to ensure that concurrent ReAct loops do not exceed the configured semaphore limits (e.g., 4 slots for `umans-coder`). This is achieved by wrapping the model invocation in an async context that acquires a semaphore before making the API call.

```python
import asyncio
from langchain_core.tools import tool

@tool
async def web_search(query: str) -> str:
    """Search the web for relevant information."""
    async with UmansConcurrencyRouter.semaphore:
        # Call LDR or Umans API for search
        return await ldr_search(query)
```

By implementing these components, Phase 2 establishes a robust, type-safe, and deterministic foundation for PRAXIS v2.0, ensuring that all inbound emails are correctly triaged, enriched with context, and processed efficiently by the ReAct orchestrator.



## 3.3 Phase 3: Native Tool Integration and Domain Logic

_Deploy LDR and Hermes as local services, configure model routing, and implement ACCU/AP domain tools with strict Pydantic typing._


### Phase 3: Native Tool Integration and Domain Logic

**Objective:** Deploy Local Deep Research (LDR) and Hermes as native local services, configure granular model routing for inference tasks, and implement domain-specific tools (ACCU/AP) with strict Pydantic enforcement to eliminate hallucination injection.

---

#### 3.1. Local Service Architecture: LDR & Hermes Integration

LDR and Hermes are deployed as independent, self-hosted Python services within the Docker Compose infrastructure. Unlike the CLI subprocess approach, these services communicate with the PRAXIS orchestrator via high-performance HTTP/gRPC protocols, allowing for shared resource pools, native async handling, and direct object serialization.

**Local Deep Research (LDR) Service**
LDR is responsible for multi-hop web research and synthesis. It operates as a stateless FastAPI service that consumes external URLs and returns structured research reports.
*   **Integration Pattern:** PRAXIS invokes LDR via `httpx.AsyncClient` with a strict JSON schema contract.
*   **Concurrency Strategy:** LDR exposes a `/research` endpoint that accepts a `ResearchTask` payload. The service manages its own internal semaphore for outbound web requests to prevent IP rate-limiting, while PRAXIS manages the concurrency for LDR invocation via the `UmansConcurrencyRouter`.
*   **Timeout & Retry:** Configured with a 10-second hard timeout. Retries are capped at 2 attempts with exponential backoff, as research is idempotent by query.

**Hermes Memory Service**
Hermes acts as the persistent memory layer, managing vector and graph state. It is optimized for low-latency recall and write operations.
*   **Integration Pattern:** Hermes exposes three core endpoints: `/recall`, `/store`, and `/learn`. These are wrapped in PRAXIS as LangGraph tools (`hermes_recall_tool`, `hermes_store_tool`, `hermes_learn_tool`).
*   **State Management:** Hermes maintains a connection pool to Neo4j and Qdrant. PRAXIS does not query databases directly; all memory operations are routed through Hermes to ensure consistency and enforce access control policies.
*   **Latency Target:** Memory recall operations must complete in **<150ms** to avoid blocking the ReAct loop.

---

#### 3.2. Model Routing & Inference Configuration

Model routing is decoupled from the orchestrator and delegated to the respective services. This allows each service to optimize its inference pipeline for specific tasks (e.g., speed for search, reasoning for synthesis).

| Service | Component | Model Assignment | Inference Purpose | Routing Strategy |
| :--- | :--- | :--- | :--- | :--- |
| **LDR** | Search Agent | `umans-flash` | High-throughput web search queries. | Parallel execution of search queries; rate-limited by service semaphore. |
| **LDR** | Synthesis Agent | `umans-kimi-k2.7` | Multi-document synthesis and report generation. | Sequential processing; waits for search results before invoking synthesis. |
| **Hermes** | Memory Router | `umans-glm-5.2` | Semantic similarity matching and context retrieval. | Fast inference; optimized for low-latency recall. |
| **Hermes** | Learning Engine | `umans-coder` | Extracting corrections and updating graph schema. | Batch processing; learns are queued and processed asynchronously. |
| **Domain Tools** | Extraction | `umans-flash` | Invoice parsing and metadata extraction. | Synchronous tool call; returns structured Pydantic model. |
| **Domain Tools** | ACCU Logic | *None* | Local calculation via Python `decimal`. | No LLM call; deterministic computation. |

**Routing Implementation Details:**
*   **LDR Routing:** The LDR service initializes LangChain-compatible clients for `umans-flash` and `umans-kimi-k2.7`. The search phase uses `umans-flash` with temperature `0.1` for deterministic query generation. The synthesis phase uses `umans-kimi-k2.7` with temperature `0.3` for creative summarization.
*   **Hermes Routing:** Hermes uses `umans-glm-5.2` for recall prompts, configured with system instructions to prioritize exact entity matches. The learning engine uses `umans-coder` to analyze correction signals and generate Cypher queries for Neo4j updates.

---

#### 3.3. Domain Tooling: ACCU & Accounts Payable

Domain tools are implemented as native Python functions wrapped with Pydantic models. These tools execute deterministic logic and LLM-based extraction, returning strictly typed outputs to the LangGraph state.

**ACCU Modeling Tools**
ACCU tools handle complex financial calculations and pipeline wrappers. To prevent arithmetic hallucinations, the tool architecture enforces a hybrid approach:
*   **Local Calculation Engine:** Core arithmetic is performed using Python's `decimal` module to ensure precision. The tool accepts parameters from the LLM but performs calculations locally.
*   **External Pipeline Wrapper:** For complex scenarios requiring external data, the tool invokes a pre-configured pipeline API. The LLM is only used to format the request and interpret the response metadata.
*   **Tool Definition:**
    ```python
    class AccuCalculationInput(BaseModel):
        scenario: str
        parameters: dict[str, float]
        calculation_type: Literal["npv", "irr", "sensitivity"]

    class AccuResult(BaseModel):
        value: Decimal
        confidence: float
        assumptions: list[str]
        raw_output: str = Field(description="Human-readable summary")
    ```

**Accounts Payable (AP) Tools**
AP tools replace legacy regex parsers with LLM-based extraction to handle unstructured invoice data.
*   **Extraction Model:** Powered by `umans-flash`, the tool extracts vendor details, line items, and totals.
*   **Validation:** Pydantic validators enforce data integrity (e.g., `amount > 0`, `date` format compliance). Invalid extractions trigger a retry with a feedback prompt.
*   **Tool Definition:**
    ```python
    class InvoiceExtraction(BaseModel):
        vendor_name: str
        invoice_number: str
        line_items: list[LineItem]
        total_amount: Decimal
        due_date: date
        extraction_confidence: float
        anomalies: list[str] = Field(default_factory=list)
    ```

---

#### 3.4. Strict Typing & Pydantic Enforcement Strategy

To mitigate the risk of LLM hallucinations corrupting the agent state, Phase 3 implements a **zero-tolerance policy for raw string returns** from tools.

*   **Schema-First Design:** All tool inputs and outputs are defined as Pydantic models. The LangGraph state machine enforces type checking at every node transition.
*   **Validation Layer:** A middleware layer intercepts tool responses. If a response fails Pydantic validation, the tool is retried with a structured error message, or the state is flagged for human review.
*   **State Integrity:** The `AgentState` TypedDict requires all tool outputs to be instances of their respective Pydantic models. Raw strings are automatically rejected, ensuring downstream nodes receive structured, predictable data.

---

#### 3.5. Phase 3 Quality Gates & Validation Metrics

**Quality Gate 3: Definition of Done**
*   [ ] **LDR Integration:** The ReAct agent invokes the LDR tool, waits for asynchronous completion, and receives a `ResearchReport` model with valid synthesis content.
*   [ ] **Hermes Integration:** The agent successfully calls `hermes_recall` to fetch relevant context and `hermes_store` to save a new fact, with both operations returning `200 OK` and valid models.
*   [ ] **Domain Tools:** ACCU calculations return precise `Decimal` results; AP extraction returns valid `InvoiceExtraction` models with high confidence scores.
*   [ ] **Pydantic Enforcement:** Automated tests verify that no raw strings can be injected into the `AgentState` via tool outputs.

**Validation Metrics**
*   **Tool Latency:**
    *   LDR Synthesis: <5s p95.
    *   Hermes Recall: <150ms p95.
    *   ACCU/AP Tools: <500ms p95.
*   **Schema Compliance:** 100% of tool outputs must pass Pydantic validation without fallback to raw strings.
*   **Concurrency Stability:** System must handle 50 concurrent LDR invocations without exceeding Umans rate limits or service timeouts.

---

**Immediate Action Items for Phase 3:**
1.  **Scaffold LDR & Hermes Services:** Initialize the FastAPI applications for LDR and Hermes, including Docker configurations and health check endpoints.
2.  **Implement Pydantic Models:** Define the `ResearchReport`, `MemoryContext`, `AccuResult`, and `InvoiceExtraction` models in the shared schema repository.
3.  **Configure Model Clients:** Set up the LangChain-compatible clients for `umans-flash`, `umans-kimi-k2.7`, `umans-glm-5.2`, and `umans-coder` within the respective services.



## 3.4 Phase 4: Graph Schema, Correction Learning, and Context Injection

_Design Neo4j correction nodes, implement the `record_correction` tool loop, and automate context injection from graph queries._


---

## 6. Phase 4: Learning & Memory System

Phase 4 transforms PRAXIS from a stateless transaction processor into a continuously improving enterprise assistant. This phase establishes the persistent knowledge layer through Neo4j's graph database, implements the correction learning feedback loop, and automates context retrieval so the agent applies historical corrections without explicit prompting. The core insight: corrections are first-class graph entities, not ephemeral conversation fragments.

### 6.1 Neo4j Graph Schema Design

The graph schema models the enterprise communication topology and, critically, the correction history that enables self-improvement. Five node types and three relationship types form the backbone of the memory layer.

#### Node Definitions

| Node Label | Primary Properties | Indexes | Constraints |
|------------|-------------------|---------|-------------|
| `Person` | `email`, `name`, `department`, `is_vip` | `email` (unique), `name` | `email` is unique |
| `Organization` | `name`, `industry`, `ticker` | `name` | `name` is unique |
| `Project` | `name`, `description`, `status` | `name` | `name` is unique |
| `Thread` | `message_id`, `subject`, `created_at` | `message_id` (unique) | `message_id` is unique |
| `Correction` | `id`, `timestamp`, `category`, `confidence` | `category`, `timestamp` | `id` is unique |

#### Relationship Definitions

| Relationship Type | Source → Target | Properties | Purpose |
|-------------------|-----------------|------------|---------|
| `SENT_EMAIL` | `Person` → `Thread` | `sent_at`, `direction` (inbound/outbound) | Tracks communication history |
| `WORKS_FOR` | `Person` → `Organization` | `role`, `since` | Contextual sender affiliation |
| `HAS_CORRECTION` | `Person` → `Correction` | `applied_count`, `last_applied_at` | Links corrections to the person who issued them |
| `APPLIES_TO` | `Correction` → `Project` | `scope` (global/project-specific) | Restricts correction applicability |

#### Schema Initialization

The schema is bootstrapped via Cypher migrations applied on first `UmansConcurrencyRouter` connection:

```cypher
-- Unique constraints (enforced by Neo4j)
CREATE CONSTRAINT person_email_unique FOR (p:Person) REQUIRE p.email IS UNIQUE;
CREATE CONSTRAINT thread_message_id_unique FOR (t:Thread) REQUIRE t.message_id IS UNIQUE;
CREATE CONSTRAINT correction_id_unique FOR (c:Correction) REQUIRE c.id IS UNIQUE;

-- Property indexes for filtering
CREATE INDEX person_name_idx FOR (p:Person) ON (p.name);
CREATE INDEX correction_category_idx FOR (c:Correction) ON (c.category);
CREATE INDEX correction_timestamp_idx FOR (c:Correction) ON (c.timestamp);
```

#### Correction Node Structure

The `Correction` node is the most complex entity, storing both the raw feedback and the structured learning signal:

```
Correction {
    id: str               # UUID v7 (time-ordered)
    timestamp: datetime   # When the correction was issued
    category: str         # "tool_usage", "tone", "format", "priority", "policy"
    confidence: float     # Agent's self-assessed confidence in the correction (0.0-1.0)
    raw_feedback: str     # The original user text (e.g., "Correction: always CC the legal team")
    structured_rule: str  # The normalized rule extracted by Hermes (e.g., "CC legal@corp.com on all invoice emails")
    context_snapshot: dict  # Snapshot of the AgentState at correction time (email intent, tools used)
    applied_count: int    # How many times this correction has been automatically applied
    last_applied_at: datetime | null  # Most recent automatic application
}
```

### 6.2 Correction Learning Loop

The correction learning loop is triggered when a user replies with a correction signal. The Orchestrator detects this pattern, routes to the `record_correction` tool, and signals Hermes to extract and store the learning rule.

#### Correction Signal Detection

The Triage node in Phase 2 is enhanced with a `correction_detected` flag. The detection uses pattern matching on the `email_content` before the full ReAct loop:

```python
CORRECTION_PATTERNS = [
    r"Correction:\s*(.+)",
    r"Actually,\s*(.+)",
    r"Note:\s*(.+)",
    r"Fix:\s*(.+)",
]

def detect_correction(email_content: str) -> tuple[bool, str]:
    for pattern in CORRECTION_PATTERNS:
        match = re.search(pattern, email_content, re.IGNORECASE)
        if match:
            return True, match.group(1).strip()
    return False, ""
```

When `detect_correction()` returns `True`, the Orchestrator bypasses the standard ReAct flow and routes directly to the correction handler node.

#### The `record_correction` Tool

The correction tool is a LangGraph tool that updates Neo4j and emits a learning signal to Hermes:

```python
from pydantic import BaseModel, Field
from datetime import datetime, timezone
from uuid import uuid7

class CorrectionInput(BaseModel):
    raw_feedback: str = Field(..., description="The user's correction text")
    category: str = Field(
        ...,
        description="Correction category: tool_usage, tone, format, priority, or policy",
        pattern="^(tool_usage|tone|format|priority|policy)$"
    )
    sender_email: str = Field(..., description="Email of the person issuing the correction")
    context: dict = Field(..., description="Current AgentState snapshot for context preservation")

class CorrectionOutput(BaseModel):
    correction_id: str
    status: str  # "recorded" or "error"
    rule_extracted: str  # The normalized rule from Hermes
    confidence: float

@tool
async def record_correction(input: CorrectionInput) -> CorrectionOutput:
    """Record a user correction and signal Hermes to extract the learning rule."""
    
    # Generate UUID v7 for time-ordered correction IDs
    correction_id = str(uuid7())
    timestamp = datetime.now(timezone.utc)
    
    # 1. Signal Hermes to extract structured rule from raw feedback
    hermes_response = await hermes_learn_signal(
        raw_feedback=input.raw_feedback,
        context_snapshot=input.context,
        sender_email=input.sender_email
    )
    
    structured_rule = hermes_response.get("extracted_rule", input.raw_feedback)
    confidence = hermes_response.get("confidence", 0.8)
    
    # 2. Update Neo4j graph
    await neo4j_session.run("""
        MERGE (c:Correction {id: $correction_id})
        SET c.timestamp = $timestamp,
            c.category = $category,
            c.confidence = $confidence,
            c.raw_feedback = $raw_feedback,
            c.structured_rule = $structured_rule,
            c.context_snapshot = $context,
            c.applied_count = 0,
            c.last_applied_at = null
        WITH c
        MATCH (p:Person {email: $sender_email})
        MERGE (p)-[r:HAS_CORRECTION]->(c)
        SET r.applied_count = 0,
            r.last_applied_at = null
    """, {
        "correction_id": correction_id,
        "timestamp": timestamp.isoformat(),
        "category": input.category,
        "confidence": confidence,
        "raw_feedback": input.raw_feedback,
        "structured_rule": structured_rule,
        "context": json.dumps(input.context),
        "sender_email": input.sender_email,
    })
    
    return CorrectionOutput(
        correction_id=correction_id,
        status="recorded",
        rule_extracted=structured_rule,
        confidence=confidence
    )
```

#### Hermes Learning Signal

The `hermes_learn` signal is an async call to the Hermes service that triggers structured rule extraction:

```python
async def hermes_learn_signal(raw_feedback: str, context_snapshot: dict, sender_email: str) -> dict:
    """Send correction to Hermes for structured rule extraction."""
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{HERMES_SERVICE_URL}/learn",
            json={
                "raw_feedback": raw_feedback,
                "context_snapshot": context_snapshot,
                "sender_email": sender_email,
                "model": "umans-glm-5.2",  # Hermes uses GLM for memory operations
            },
            timeout=10.0
        )
        response.raise_for_status()
        return response.json()
```

Hermes processes this signal by:
1. Parsing the raw feedback with `umans-glm-5.2` to extract a normalized rule
2. Comparing the rule against existing corrections to detect duplicates
3. Returning the structured rule and a confidence score

### 6.3 Context Injection Strategy

The Context Loading Node is enhanced to retrieve the most relevant `Correction` nodes from Neo4j and inject them into the ReAct system prompt before the Orchestrator begins tool execution.

#### Retrieval Query

The query retrieves the top 5 corrections ranked by a composite relevance score that considers category match, recency, and sender affinity:

```python
CORRECTION_RETRIEVAL_QUERY = """
MATCH (p:Person {email: $sender_email})<-[:HAS_CORRECTION]-(c:Correction)
WHERE c.applied_count < 50  // Deprecate corrections applied too many times
RETURN 
    c.structured_rule AS rule,
    c.category AS category,
    c.applied_count AS applied_count,
    c.confidence AS confidence,
    c.timestamp AS timestamp,
    // Composite relevance score
    (CASE WHEN c.category = $intent_category THEN 2.0 ELSE 1.0 END) *
    (1.0 / (1.0 + c.applied_count * 0.1)) *
    c.confidence *
    exp(-0.00001 * duration.between(c.timestamp, datetime($now)).milliseconds) AS relevance_score
ORDER BY relevance_score DESC
LIMIT 5
"""
```

#### Scoring Components

| Component | Formula | Effect |
|-----------|---------|--------|
| Category Match | `2.0` if match, `1.0` otherwise | Prioritizes corrections relevant to current email intent |
| Decay | `1.0 / (1.0 + applied_count * 0.1)` | Gradually reduces relevance as correction is applied more often |
| Confidence | `c.confidence` (0.0–1.0) | Hermes-assessed confidence in the correction's accuracy |
| Temporal Decay | `exp(-0.00001 * age_in_ms)` | Old corrections lose weight over time |

#### Injection Format

Retrieved corrections are formatted into the system prompt as structured context blocks:

```
--- LEARNING CONTEXT ---
The following corrections have been applied by this sender or similar senders:

[Correction #1] Category: policy | Confidence: 0.92 | Applied 12 times
Rule: Always CC legal@corp.com on emails discussing contract modifications.
Source: Sent by legal@corp.com on 2026-04-15

[Correction #2] Category: tone | Confidence: 0.78 | Applied 3 times
Rule: Use formal greeting ("Dear [Name]") for external vendor communications.
Source: Sent by vendor@partner.com on 2026-05-22
--- END LEARNING CONTEXT ---
```

#### Context Loading Node Implementation

```python
async def context_loading_node(state: AgentState) -> dict:
    """Query Neo4j for relevant corrections and inject into state."""
    sender_email = state["email_content"]["from"]
    intent_category = state["triage_result"]["intent"]
    now = datetime.now(timezone.utc).isoformat()
    
    results = await neo4j_session.run(
        CORRECTION_RETRIEVAL_QUERY,
        sender_email=sender_email,
        intent_category=intent_category,
        now=now
    )
    
    corrections = [
        {
            "rule": record["rule"],
            "category": record["category"],
            "applied_count": record["applied_count"],
            "confidence": record["confidence"],
        }
        for record in results
    ]
    
    # Format corrections into context string
    context_blocks = []
    for i, corr in enumerate(corrections, 1):
        context_blocks.append(
            f"[Correction #{i}] Category: {corr['category']} | "
            f"Confidence: {corr['confidence']:.2f} | Applied {corr['applied_count']} times\n"
            f"Rule: {corr['rule']}"
        )
    
    state["memory_context"]["corrections"] = context_blocks
    
    return state
```

### 6.4 Correction Lifecycle Management

Corrections are not permanent. A background maintenance process handles correction lifecycle:

| Lifecycle Stage | Trigger | Action |
|----------------|---------|--------|
| **Active** | Newly recorded | Stored in Neo4j, available for context injection |
| **Monitored** | `applied_count > 10` | Hermes periodically re-evaluates correction validity |
| **Deprecated** | `applied_count >= 50` | Correction remains in graph but excluded from retrieval |
| **Archived** | `age > 180 days` AND `applied_count >= 30` | Moved to `:CorrectionArchive` label |

The archival Cypher operation:

```cypher
MATCH (c:Correction)
WHERE c.applied_count >= 30
  AND duration.between(c.timestamp, datetime()).days > 180
SET c:CorrectionArchive
REMOVE c:Correction
```

### 6.5 Quality Gate 4 (Definition of Done)

| Test Case | Expected Behavior | Verification Method |
|-----------|-------------------|---------------------|
| **Correction Recording** | User sends "Correction: always CC legal on invoices" → Orchestrator routes to `record_correction` → Neo4j creates `Correction` node → Hermes returns structured rule | Neo4j browser query confirms node creation with correct properties; Hermes API logs show rule extraction |
| **Context Retrieval** | Second email from same sender with invoice intent → Context Loading Node retrieves the stored correction → Correction appears in ReAct system prompt | LangGraph state dump shows `memory_context.corrections` populated; LLM prompt log includes correction block |
| **Automatic Application** | Agent applies the correction automatically in its response without user re-prompting | Final email includes legal@corp.com in CC; no explicit mention of the correction in the agent's response |
| **Category Filtering** | Correction with category "tone" is NOT retrieved when email intent is "invoice_processing" | Neo4j query with `intent_category` filter returns zero results for mismatched categories |
| **Concurrency Safety** | 10 concurrent correction submissions from different senders → All corrections recorded without data loss | Neo4j transaction logs show 10 distinct `Correction` nodes with unique IDs |



## 3.5 Phase 5: Enterprise Hardening, Temporal Decoupling, and PII Security

_Integrate Temporal for async workflow decoupling, implement Microsoft Presidio for PII redaction, and prepare IMAP/SMTP adapters._


## Phase 5: Enterprise Hardening, Temporal Decoupling, and PII Security

**Objective:** Transition PRAXIS v2.0 from a synchronous webhook-driven prototype to an enterprise-grade, async-native system. This phase introduces temporal workflow orchestration for decoupled execution, implements a zero-trust PII redaction pipeline, and prepares the infrastructure for direct IMAP/SMTP email routing.

### 1. Temporal Workflow Decoupling & Scheduling
The FastAPI webhook endpoint must guarantee sub-100ms response times to prevent client timeouts and ensure reliable webhook delivery. Long-running LLM inference, Deep Research synthesis, and Neo4j context enrichment cannot block the HTTP request cycle. Temporal.io provides durable execution, automatic retries, and cron-based scheduling, completely decoupling ingress from processing.

**Architectural Integration:**
- **FastAPI Ingress:** The `/webhook/email` endpoint validates the Svix signature, constructs an `InboundEmail` DTO, and immediately returns `202 Accepted` with a `workflow_id`. The payload is pushed to a Temporal `start_workflow` call.
- **Durable Execution:** The `EmailProcessingWorkflow` orchestrates the LangGraph execution. If the Umans API returns a 429 or 5xx, Temporal handles exponential backoff automatically without re-triggering the webhook.
- **Out-of-Hours Routing:** Business hours are defined as `09:00–17:00 UTC`. Non-critical emails arriving outside this window are scheduled via Temporal `cron_schedule` to execute at `08:00 UTC` the next business day. Critical emails bypass scheduling and execute immediately.

```python
# Temporal Workflow Definition (Python SDK)
from temporalio import workflow
from datetime import timedelta

@workflow.defn
class EmailProcessingWorkflow:
    @workflow.run
    async def run(self, email_payload: dict) -> dict:
        # 1. Pre-process & PII Redaction (Phase 5.2)
        clean_payload = await workflow.execute_activity(
            "redact_pii_activity", email_payload, start_to_close_timeout=timedelta(seconds=5)
        )
        
        # 2. Execute LangGraph ReAct Loop
        result = await workflow.execute_activity(
            "execute_langgraph_agent", clean_payload, start_to_close_timeout=timedelta(minutes=5)
        )
        
        # 3. Store outcome in Neo4j & trigger follow-up if needed
        await workflow.execute_activity("persist_response", result, start_to_close_timeout=timedelta(seconds=10))
        return {"status": "completed", "workflow_id": workflow.info().workflow_id}
```

### 2. PII Security & Redaction Pipeline
Enterprise compliance mandates that all Personally Identifiable Information be masked before leaving the secure boundary. Microsoft Presidio is integrated as a synchronous pre-processing activity within the Temporal workflow to avoid blocking the Python event loop.

**Implementation Strategy:**
- **Async Execution:** Presidio's `AnalyzerEngine` is CPU-bound. It is wrapped in `asyncio.to_thread()` to run in a dedicated thread pool, preserving `asyncio` concurrency.
- **Dual Redaction Strategy:** 
  - *Lookup/Storage:* PII is cryptographically hashed (SHA-256) and stored in Neo4j for relationship mapping without exposing raw data.
  - *LLM Prompting:* PII is replaced with deterministic tokens (e.g., `[EMAIL_REDACTED]`) to prevent LLM memorization while preserving syntactic structure.
- **Custom Recognizers:** Domain-specific patterns (e.g., internal ticket IDs, vendor PO numbers) are injected via `RecognizerRegistry` to prevent false positives on standard email addresses.

| PII Entity Type | Recognizer Source | Redaction Method | Logging/Storage Policy |
| :--- | :--- | :--- | :--- |
| `PERSON` | Presidio Built-in | `[PERSON_REDACTED]` | Hashed for Neo4j `Person` nodes |
| `EMAIL_ADDRESS` | Presidio Built-in | `[EMAIL_REDACTED]` | Hashed; raw never leaves VPC |
| `PHONE_NUMBER` | Presidio Built-in | `[PHONE_REDACTED]` | Masked in logs; stored as format metadata |
| `US_SSN` | Presidio Built-in | `[SSN_REDACTED]` | Blocked if detected; alert sent to admin |
| `VENDOR_PO` | Custom Regex | `[PO_REDACTED]` | Stored in `Project` node relationships |

```python
# Async Presidio Wrapper
import asyncio
from presidio_analyzer import AnalyzerEngine
from presidio_anonymizer import AnonymizerEngine

analyzer = AnalyzerEngine()
anonymizer = AnonymizerEngine()

async def redact_pii_activity(email_data: dict) -> dict:
    text = email_data.get("body", "")
    # Run synchronous Presidio in thread pool
    results = await asyncio.to_thread(analyzer.analyze, text=text, language="en")
    
    # Anonymize in place
    anonymized = await asyncio.to_thread(
        anonymizer.anonymize, text=text, analyzer_results=results
    )
    email_data["body"] = anonymized.text
    return email_data
```

### 3. IMAP/SMTP Adapter Migration
Phase 5 prepares PRAXIS for self-hosted email routing by abstracting the `langchain-agentmail` dependency. The adapter layer translates standard RFC 5322/822 MIME formats into the existing `InboundEmail` Pydantic schema, enabling a unified processing pipeline regardless of transport.

**Inbound (IMAP) Architecture:**
- **Async Polling:** `imapclient` with `asyncio` integration polls the enterprise inbox at configurable intervals (default: 30s). Unread messages are fetched, parsed, and pushed to a Temporal `start_workflow` queue.
- **MIME Parsing:** Raw `bytes` are decoded using `email.message_from_bytes()`. Attachments (PDF, DOCX, XLSX) are extracted and routed to a local document parser (e.g., `unstructured` or `llama-parse`) before body assembly.
- **Connection Management:** Persistent TLS 1.3 connections with automatic reconnection and exponential backoff. Connection pooling ensures <50ms latency per fetch.

**Outbound (SMTP) Architecture:**
- `aiosmtplib` handles outbound responses and follow-up drafts. Templates are rendered via Jinja2, and emails are sent with DKIM/SPF headers pre-configured via environment variables.
- **Schema Mapping:** The adapter normalizes IMAP headers (`From`, `To`, `Subject`, `Date`, `Message-ID`) into the `InboundEmail` model, ensuring downstream LangGraph nodes receive identical structures regardless of ingress method.

```python
# Inbound Email Adapter Schema Mapping
from pydantic import BaseModel
from email.message import EmailMessage
from datetime import datetime

class InboundEmail(BaseModel):
    message_id: str
    sender: str
    recipients: list[str]
    subject: str
    body: str
    attachments: list[dict]  # {filename, mime_type, content_base64}
    received_at: datetime

def parse_imap_message(raw_bytes: bytes) -> InboundEmail:
    msg = EmailMessage()
    msg.as_bytes = raw_bytes
    # Extract & sanitize body, handle multipart alternatives
    body = msg.get_body(preferencelist=("plain", "html")).get_content()
    # Attachments extraction logic omitted for brevity
    return InboundEmail(
        message_id=msg["Message-ID"],
        sender=msg["From"],
        recipients=msg["To"].split(","),
        subject=msg["Subject"],
        body=body,
        attachments=[],
        received_at=datetime.now(timezone.utc)
    )
```

### 4. Quality Gate 5 & Validation Criteria
Phase 5 concludes with rigorous enterprise validation. The following criteria must be met before transitioning to production:

| Validation Metric | Target Threshold | Verification Method |
| :--- | :--- | :--- |
| **Webhook Latency** | `< 100ms` for `202 Accepted` | `k6` load test simulating 200 req/s to `/webhook/email` |
| **PII Redaction Coverage** | `100%` of detected entities masked | Automated test suite injecting 50 known PII patterns into mock payloads |
| **Concurrency Enforcement** | `0` Umans API 429/503 errors under load | Temporal worker processes 50 concurrent emails; verify `asyncio.Semaphore` blocks exceed limits |
| **IMAP Adapter Fidelity** | Schema parity with webhook payload | Diff test comparing `InboundEmail` objects from webhook vs. IMAP parser |
| **Out-of-Hours Scheduling** | Non-critical emails delayed to 08:00 UTC | Temporal visibility dashboard query for `cron_schedule` execution timestamps |

**Migration Path Notes:**
- The `langchain-agentmail` dependency is marked for deprecation in Phase 6. All existing webhook routes will be mirrored by the IMAP adapter during a 2-week parallel run.
- Temporal workers must be deployed as stateless containers behind a load balancer to ensure horizontal scaling during peak email volumes.
- Presidio model weights and custom recognizers will be version-controlled and deployed alongside the worker container to ensure reproducible redaction behavior.






# 4. Quality Gates and Validation Criteria

## 4.1 Phase-Specific Definition of Done Checklists

_Validate health checks, webhook signatures, router semaphore limits, structured JSON outputs, and tool return types per phase._


## Phase-Specific Definition of Done Checklists

To ensure architectural integrity and prevent regression during the greenfield build, each development phase must clear a strict Definition of Done (DoD) before merging to `main`. These checklists operationalize quality gates by mapping technical requirements to automated validation criteria, pass/fail thresholds, and CI/CD enforcement points.

### Phase 1: Foundation & Infrastructure Validation
*Focus: Health checks, webhook signatures, router semaphore limits*

| Validation Checkpoint | Testing Methodology | Pass/Fail Criteria | CI/CD Gate |
| :--- | :--- | :--- | :--- |
| **Health & Dependency Readiness** | `GET /health` endpoint simulation via `httpx` + `pytest` | Returns `200 OK` with sub-component statuses (`db: up`, `vector: up`, `api: ready`). Fails if any critical dependency reports `unhealthy`. | Blocked merge if health check fails in staging environment. |
| **Webhook Signature Verification** | Inject malformed, expired, and valid HMAC-SHA256 headers into `/webhook/email` | Accepts valid signatures (`200 OK`). Rejects tampered/missing signatures with `401 Unauthorized` and logs security event. Zero false positives over 100 test cycles. | Automated contract test must score 100% pass rate. |
| **Umans Concurrency Semaphore Enforcement** | Burst test: 50 concurrent `asyncio.gather()` calls routed through `UmansConcurrencyRouter` | Router strictly caps active requests to configured limits (4/8). No race conditions, no dropped requests, no semaphore deadlocks. Queue backpressure handled gracefully. | Load simulation must complete with 0 semaphore violations. |

### Phase 2: Core Intelligence & Triage Validation
*Focus: Structured JSON outputs, deterministic routing, state persistence*

| Validation Checkpoint | Testing Methodology | Pass/Fail Criteria | CI/CD Gate |
| :--- | :--- | :--- | :--- |
| **Structured JSON Schema Compliance** | Feed 50+ diverse email payloads through Triage node; validate output against `EmailTriage` Pydantic model | 100% of outputs must conform to schema. Rejects/flags malformed LLM outputs via `pydantic` strict validation or fallback retry. | Schema validation test suite must pass; any raw string leakage fails gate. |
| **Conditional Edge Routing Accuracy** | Inject edge-case triage results (e.g., `priority: critical`, `intent: spam`, `sentiment: neutral`) | Graph traversal matches expected paths 100% of the time. No orphaned states or misrouted high-priority items. | Path coverage test must hit all conditional branches. |
| **LangGraph Checkpoint Integrity** | Interrupt graph mid-execution, restore state, and resume via PostgreSQL checkpointer | State serialization/deserialization preserves all fields (`email_content`, `triage_result`, etc.) with zero data loss or type coercion errors. | Checkpoint round-trip test must pass under `pytest-asyncio`. |

### Phase 3: Tooling & Execution Layer Validation
*Focus: Tool return types, async completion, LDR/Hermes contract compliance*

| Validation Checkpoint | Testing Methodology | Pass/Fail Criteria | CI/CD Gate |
| :--- | :--- | :--- | :--- |
| **Strict Tool Return Type Enforcement** | Mock LDR/Hermes endpoints to return raw strings, nested dicts, and malformed JSON | All tool wrappers must reject invalid payloads via Pydantic validation. Only strictly typed models propagate to the ReAct loop. Zero untyped string returns allowed. | Contract validation test must fail gracefully on type mismatch. |
| **Asynchronous Tool Completion** | Trigger LDR research task; assert main event loop remains responsive during tool execution | Tool invocation returns `Awaitable` without blocking. Orchestrator correctly awaits completion and processes response within configured timeout. | Async timeout assertion must pass; event loop starvation fails gate. |
| **Memory Service Latency & Reliability** | Hammer `/recall` and `/store` endpoints with concurrent requests | 95th percentile latency ≤ 150ms. Connection pool exhaustion handled via backoff. No data corruption under concurrent write/read cycles. | Latency profiling test must stay within SLA thresholds. |

### Phase 4: Learning & Memory System Validation
*Focus: Graph updates, correction routing, context injection integrity*

| Validation Checkpoint | Testing Methodology | Pass/Fail Criteria | CI/CD Gate |
| :--- | :--- | :--- | :--- |
| **Correction Record & Graph Mutation** | Simulate user reply with `Correction: [feedback]`; trace execution to Neo4j | `record_correction` tool successfully creates/updates `HAS_CORRECTION` relationships. Graph query returns 100% match on subsequent retrieval. | Graph traversal assertion must verify node/edge creation. |
| **Semantic Context Injection Accuracy** | Query system with email similar to corrected scenario; validate context retrieval | Top-5 relevant `Correction` nodes retrieved with semantic relevance score ≥ threshold. Injected context does not exceed system prompt token budget. | Retrieval recall/precision test must meet accuracy floor. |
| **Learning Signal Propagation** | Trigger `hermes_learn` via correction tool; verify vector index update | Hermes successfully queues and processes learning signal. Vector embeddings updated within configured sync window without blocking main pipeline. | Async queue drain test must complete within SLA. |

### Phase 5: Enterprise Hardening & Migration Validation
*Focus: Async decoupling, PII redaction, scale concurrency limits*

| Validation Checkpoint | Testing Methodology | Pass/Fail Criteria | CI/CD Gate |
| :--- | :--- | :--- | :--- |
| **Webhook Decoupling & Response Latency** | Simulate high-throughput webhook ingestion; measure response time | Webhook returns `202 Accepted` in < 100ms. Temporal/Celery worker successfully picks up and processes payload without timeout or drop. | Latency benchmark must pass; queue ingestion test must hit 100% success. |
| **PII Redaction Completeness** | Inject emails containing emails, phone numbers, SSNs, and credit card patterns | Microsoft Presidio node masks 100% of detected PII before LLM prompt generation. Zero PII leakage in logs, traces, or LLM context. | PII scanner validation must return 0 false negatives across test corpus. |
| **Enterprise Concurrency Throughput** | Load test: 50+ concurrent inbound emails with mixed tool dependencies | System processes all items without exceeding Umans rate limits. No queue overflow, no semaphore deadlocks, graceful degradation under peak load. | Load simulation must complete with 0 dropped requests and 0 rate-limit breaches. |

### Cross-Phase Validation & Automation Protocol

To prevent manual checklist drift, all DoD items are enforced through a unified validation pipeline:

1.  **Schema-First Contract Testing:** All tool interfaces, webhook payloads, and LLM outputs are validated against Pydantic/JSON Schema definitions in CI. Mismatches fail the build immediately.
2.  **Deterministic Mocking:** External dependencies (Umans API, Neo4j, Qdrant, LDR/Hermes) are replaced with deterministic mock servers that return pre-validated responses, ensuring tests run in < 30 seconds without network variance.
3.  **Gate-Blocking Merge Policy:** No branch merge is permitted unless the corresponding phase's DoD checklist achieves a 100% automated pass rate. Manual overrides require architectural review board approval and must be logged with rollback criteria.
4.  **Observability Integration:** Each quality gate emits structured telemetry (success/failure, latency, schema violation counts) to the centralized monitoring stack. Trends indicating regression trigger automatic alerts before production deployment.

This checklist-driven validation framework ensures that architectural decisions (native async routing, strict typing, service decoupling) are continuously verified against operational requirements, maintaining system reliability as complexity scales across phases.



## 4.2 Performance and Load Testing Requirements

_Verify concurrent email processing capacity, enforcement of Umans concurrency limits, and sub-100ms webhook latency._


## Performance and Load Testing Requirements

This subsection defines the quantitative benchmarks, testing methodologies, and observability standards required to validate PRAXIS v2.0’s performance under production-like conditions. All testing will be executed in a staging environment with resource allocation (CPU, RAM, network topology, and connection pool sizing) mirroring production to ensure metrics reflect real-world behavior.

### 1. Webhook Latency Validation
The FastAPI `/webhook/email` endpoint must acknowledge inbound traffic without blocking on downstream processing. Latency is measured from the first byte of the incoming HTTP request to the first byte of the response.

*   **Target Threshold:** P95 ≤ 80ms, P99 ≤ 95ms, Absolute Maximum ≤ 100ms under sustained load.
*   **Test Methodology:** Utilize `k6` or `Locust` to simulate burst traffic (10–50 requests/second) with realistic payload sizes (10KB–200KB, including multipart/form-data headers and base64 attachment metadata). The endpoint will immediately validate Svix signatures, deserialize the payload into an `InboundEmail` schema, and dispatch to the Temporal workflow queue before returning `202 Accepted`.
*   **Failure Conditions:** Any blocking I/O (synchronous DB calls, heavy JSON parsing, or CPU-bound regex validation) during the handshake phase will trigger a test failure. Connection pooling and async serializers (`orjson`) are mandatory to meet thresholds.

### 2. Umans Concurrency Limit Enforcement
The `UmansConcurrencyRouter` must strictly cap simultaneous inference calls to prevent API throttling, quota exhaustion, and downstream service degradation. Testing focuses on race condition prevention and graceful backpressure.

*   **Target Threshold:** 0 semaphore breaches under 200% of configured limits; <1% request rejection rate when limits are exceeded; automatic graceful degradation (queueing or 429/202 responses).
*   **Test Methodology:** Deploy a custom `pytest-asyncio` stress suite that spawns 50 concurrent tasks targeting `umans-flash` and `umans-glm-5.2` endpoints simultaneously. The router’s internal state (active slots, queued requests, wait times) will be instrumented via OpenTelemetry. Tests will verify that:
    *   Active calls never exceed the defined semaphore (4 for Kimi/GLM, 8 for Qwen).
    *   Excess requests are correctly routed to the Temporal scheduler or receive immediate backpressure signals.
    *   Memory usage remains stable (no async task leaks or unbounded queue growth).
*   **Validation:** Automated regression tests will run after every router refactoring. Metrics will be exposed to Prometheus (`router_semaphore_active`, `router_queue_depth`, `router_wait_time_ms`).

### 3. Concurrent Email Processing Capacity
This phase validates the end-to-end throughput of the async pipeline: Webhook → Temporal → LangGraph State Machine → Umans Inference → Neo4j/Qdrant → Response. Testing must account for downstream dependency latency and checkpoint persistence overhead.

*   **Target Threshold:** Sustained processing of 50 concurrent inbound emails/min with <0.5% error rate; p95 end-to-end latency ≤ 15s for standard triage; ≤ 45s for full Deep Research cycles.
*   **Test Methodology:** 
    *   *Baseline Load:* 20 emails/min over 1 hour (steady-state validation).
    *   *Peak Load:* 100 emails/min burst for 15 minutes, followed by cooldown.
    *   *Chaos/Resilience:* Inject simulated downstream latency (e.g., +500ms to Umans API, Neo4j connection pool exhaustion) to verify Temporal retry policies and LangGraph checkpoint recovery.
*   **Resource Monitoring:** Track CPU utilization (<75% sustained), RAM growth (no memory leaks over 24h soak tests), and network I/O. Database connection pools (Neo4j Bolt, Qdrant HTTP) must maintain <50ms query latency under load.

### 4. Performance Acceptance Matrix
The following matrix defines the pass/fail criteria for automated CI/CD validation gates. Failure on any P95 metric or enforcement threshold will block deployment to staging.

| Test Category | Metric | Threshold | Validation Tool/Method |
|:---|:---|:---|:---|
| **Webhook Latency** | P95 Response Time | ≤ 80ms | `k6` / `Locust` (10-50 req/s burst) |
| **Webhook Latency** | Absolute Max | ≤ 100ms | Real-time APM tracing (OpenTelemetry) |
| **Concurrency Enforcement** | Semaphore Breaches | 0 under 200% load | `pytest-asyncio` stress suite |
| **Concurrency Enforcement** | Queue Backlog Growth | < 500 unprocessed tasks | Prometheus `router_queue_depth` |
| **Throughput** | Sustained Processing | 50 emails/min, <0.5% error | Temporal workflow orchestration test |
| **End-to-End Latency** | Triage Cycle (p95) | ≤ 15s | Synthetic email pipeline test |
| **End-to-End Latency** | Deep Research Cycle (p95) | ≤ 45s | Full LangGraph execution test |
| **Resource Stability** | Memory Leak (24h) | < 5% RAM growth | `pytest-asyncio` + `tracemalloc` |

### 5. Automated Validation & Observability Integration
Performance testing is treated as a continuous quality gate rather than a pre-release checkpoint. All latency, throughput, and semaphore metrics will be exported via OpenTelemetry to a Grafana/Prometheus stack. A dedicated `tests/performance/` suite will execute on every PR targeting `main`. If P95 webhook latency exceeds 85ms or semaphore enforcement fails 1 out of 10 runs, the pipeline will automatically block deployment and generate a performance regression report detailing queue depth, error breakdown, and resource snapshots.






# 5. Immediate Next Steps and Initialization

## 5.1 Architecture Approval and Credential Provisioning

_Confirm the native Python architecture decision and provision Umans API credentials._


## Architecture Approval and Credential Provisioning

### 1. Formal Architectural Ratification
**Directive:** The architectural decision to deploy PRAXIS v2.0 as a **native, asynchronous Python application** is hereby ratified and locked for the duration of the project lifecycle. All subsequent development, testing, and deployment pipelines must align with this runtime model.

**Ratified Boundaries:**
| Component | Approved Implementation | Deprecated/Excluded Pattern |
|-----------|------------------------|-----------------------------|
| **Runtime** | Python 3.12+ `asyncio` event loop with FastAPI ingress | `subprocess.Popen` / CLI wrappers / `os.system` |
| **Inference Routing** | `UmansConcurrencyRouter` with native `asyncio.Semaphore` | External semaphore managers, queue-based IPC, or blocking `requests` calls |
| **State Management** | LangGraph `TypedDict` state machine with PostgreSQL checkpointer | JSON serialization via `stdout`/`stdin`, file-based state dumps |
| **Service Integration** | Direct HTTP/gRPC clients for Hermes & LDR | CLI invocation, WebSocket polling, or REST polling loops |

**Technical Lock-In Rationale:**
The native async runtime eliminates context-switching overhead, enables true concurrency within a single memory space, and allows deterministic control over rate limits. By binding the `UmansConcurrencyRouter` directly to the event loop, we guarantee that semaphore acquisition, HTTP session pooling, and response deserialization occur without inter-process synchronization costs. This architecture is non-negotiable for meeting the sub-100ms webhook acceptance requirement and sustaining enterprise-scale throughput.

### 2. Umans API Credential Provisioning & Security Matrix
Provisioning Umans API credentials requires strict scoping, secure injection, and runtime validation to prevent unauthorized inference usage and ensure compliance with enterprise data governance policies.

#### Provisioning Workflow
1. **Key Generation:** Credentials must be generated via the Umans Enterprise Console under the `praxis-v2-ops` service account. Personal developer keys are prohibited.
2. **Scope Assignment:** Keys must be scoped to `inference:read`, `inference:write`, and `rate_limit:tier_enterprise`. No administrative or billing scopes are granted.
3. **Network Binding:** Keys must be restricted to the production VPC CIDR and CI/CD runner IP ranges. Egress filtering is enforced at the infrastructure layer.
4. **Secure Injection:** Credentials are never committed to version control. They are injected at runtime via a typed configuration loader (`pydantic-settings`) that reads from the organization's secret manager (e.g., AWS Secrets Manager, HashiCorp Vault, or GitHub Secrets for CI pipelines).

#### Credential Access Control Matrix
| Credential Type | Model Tier Mapping | Concurrency Limit | Storage Location | Rotation Policy | Audit Scope |
|-----------------|-------------------|-------------------|------------------|-----------------|-------------|
| `UMANS_KIMI_KEY` | Kimi / GLM-5.2 | 4 concurrent sessions | Secret Manager → Env (`asyncio` runtime) | 90-day mandatory rotation | Full prompt/response logging |
| `UMANS_QWEN_KEY` | Qwen / Flash | 8 concurrent sessions | Secret Manager → Env (`asyncio` runtime) | 90-day mandatory rotation | Full prompt/response logging |
| `UMANS_HERMES_KEY` | Hermes Memory Service | 2 concurrent sessions | Secret Manager → Env (`asyncio` runtime) | 60-day mandatory rotation | Memory read/write operations only |
| `UMANS_LDR_KEY` | Deep Research Synthesis | 3 concurrent sessions | Secret Manager → Env (`asyncio` runtime) | 90-day mandatory rotation | Research session IDs & output hashes |

**Runtime Configuration Enforcement:**
```python
# Example: Typed credential injection (pydantic-settings)
from pydantic_settings import BaseSettings
from pydantic import Field, SecretStr

class UmansConfig(BaseSettings):
    kimi_api_key: SecretStr = Field(..., alias="UMANS_KIMI_KEY")
    qwen_api_key: SecretStr = Field(..., alias="UMANS_QWEN_KEY")
    hermes_api_key: SecretStr = Field(..., alias="UMANS_HERMES_KEY")
    base_url: str = "https://api.umans.enterprise/v1"
    
    model_config = {"env_file": ".env.production", "extra": "ignore"}
```
The `UmansConcurrencyRouter` will initialize with these credentials on startup, validate connectivity via a lightweight `/health` ping, and fail fast if scopes are misconfigured or rate limits are exceeded.

### 3. Pre-Phase 1 Validation Checklist
Before repository scaffolding begins, the following validations must be completed and documented:

- [ ] **Architectural Sign-Off:** Lead architect and security lead have formally approved the native async runtime and deprecated the CLI subprocess pattern in the project's ADR (Architecture Decision Record).
- [ ] **Credential Injection Verified:** All Umans API keys are stored in the designated secret manager and successfully decrypt into the local development environment without exposing plaintext.
- [ ] **Router Initialization Test:** The `UmansConcurrencyRouter` successfully binds to the correct model tiers, enforces the 4/8/2/3 semaphore limits, and rejects out-of-scope requests.
- [ ] **Egress & Rate Limit Confirmation:** Network policies allow outbound HTTPS to Umans endpoints; IP whitelisting is active; and initial rate limit tiers are confirmed in the Umans console.
- [ ] **Audit Logging Pipeline:** All credential usage is routed through a centralized logging framework with PII masking enabled prior to key transmission.

**Next Action:** Upon completion of this checklist, proceed to **Repository Scaffolding and Environment Setup** to initialize the Python project, configure linting/testing tooling, and deploy the Neo4j/Qdrant Docker infrastructure.



## 5.2 Repository Scaffolding and Environment Setup

_Initialize the Python project with `uv`/`poetry`, configure `ruff`/`pytest`, and deploy Docker infrastructure for Neo4j and Qdrant._


## Repository Scaffolding and Environment Setup

To establish a high-performance, maintainable foundation for PRAXIS v2.0, the repository must be initialized using modern Python tooling. This section details the exact procedural steps for project bootstrapping, dependency resolution, static analysis configuration, and local infrastructure provisioning.

### 1. Project Initialization & Dependency Management
Given the strict latency requirements and Python 3.12 target, `uv` is selected over `poetry` for its native `pyproject.toml` compatibility, C-based dependency resolution, and sub-millisecond environment creation.

**Initialization Workflow:**
```bash
# Create project directory and enforce Python 3.12
uv init praxis-v2 --python 3.12
cd praxis-v2

# Install core runtime dependencies
uv add fastapi uvicorn langgraph pydantic httpx python-dotenv
uv add neo4j qdrant-client langchain-openai

# Install development tooling
uv add --dev ruff pytest pytest-asyncio httpx
```

**`pyproject.toml` Structure:**
The root configuration file serves as the single source of truth for metadata, version constraints, and tool routing.
```toml
[project]
name = "praxis-v2"
version = "0.1.0"
description = "Native asynchronous enterprise email agent"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.110.0",
    "uvicorn[standard]>=0.29.0",
    "langgraph>=0.0.50",
    "pydantic>=2.7.0",
    "httpx>=0.27.0",
    "python-dotenv>=1.0.1",
    "neo4j>=5.20.0",
    "qdrant-client>=1.8.0",
]

[project.optional-dependencies]
dev = [
    "ruff>=0.4.0",
    "pytest>=8.2.0",
    "pytest-asyncio>=0.23.0",
]
```

### 2. Development Tooling Configuration
Static analysis and testing frameworks must be configured to enforce strict typing, async compatibility, and deterministic code structure from the first commit.

**`ruff.toml` Configuration:**
Configured to align with enterprise standards, enforcing type hints, preventing unused imports, and optimizing import sorting.
```toml
target-version = "py312"
line-length = 100
fix = true

[lint]
select = ["E", "F", "I", "N", "UP", "B", "RUF"]
ignore = ["E501", "RUF012"] # Allow dynamic class creation for Pydantic models

[lint.isort]
known-first-party = ["praxis"]
```

**`pytest.ini` Configuration:**
Enables native `asyncio` mode and configures test discovery to ensure asynchronous tool wrappers and LangGraph state machines are properly validated.
```ini
[pytest]
testpaths = tests
asyncio_mode = auto
addopts = --strict-markers --tb=short
markers =
    slow: marks tests as slow (deselect with '-m "not slow"')
    integration: marks tests as integration tests
```

### 3. Docker Infrastructure Provisioning
The local development environment requires isolated, persistent containers for the graph database (Neo4j) and vector store (Qdrant). A `docker-compose.yml` file will be placed at the repository root.

**Service Configuration Matrix:**
| Service | Image | Ports | Environment Variables | Volume Mounts | Purpose |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `neo4j` | `neo4j:5.20` | `7687:7687` (Bolt)<br>`7474:7474` (UI) | `NEO4J_AUTH=neo4j/praxis_dev_password`<br>`NEO4J_apoc_*_enabled=true` | `neo4j_data:/data`<br>`neo4j_logs:/logs` | Graph storage for sender history & correction tracking |
| `qdrant` | `qdrant/qdrant:latest` | `6333:6333` (REST)<br>`6334:6334` (gRPC) | `QDRANT__SERVICE__API_KEY=praxis_dev_api_key` | `qdrant_data:/qdrant/storage` | Vector storage for semantic memory & embedding retrieval |

**`docker-compose.yml` Setup:**
```yaml
version: '3.8'

services:
  neo4j:
    image: neo4j:5.20
    container_name: praxis-neo4j
    ports:
      - "7687:7687"
      - "7474:7474"
    environment:
      - NEO4J_AUTH=neo4j/praxis_dev_password
      - NEO4J_apoc_export_file_enabled=true
      - NEO4J_apoc_import_file_enabled=true
      - NEO4J_apoc_import_file_use__neo4j__config=true
    volumes:
      - neo4j_data:/data
      - neo4j_logs:/logs
      - neo4j_import:/import
      - neo4j_plugins:/plugins

  qdrant:
    image: qdrant/qdrant:latest
    container_name: praxis-qdrant
    ports:
      - "6333:6333"
      - "6334:6334"
    environment:
      - QDRANT__SERVICE__API_KEY=praxis_dev_api_key
    volumes:
      - qdrant_data:/qdrant/storage

volumes:
  neo4j_data:
  neo4j_logs:
  neo4j_import:
  neo4j_plugins:
  qdrant_data:
```

**Network & Isolation Strategy:**
- **Bolt Protocol Binding:** Neo4j is explicitly bound to port `7687` to ensure the Python `neo4j` driver communicates via the optimized binary protocol rather than HTTP, reducing serialization overhead.
- **API Key Enforcement:** Qdrant is initialized with a development API key to mirror enterprise authentication models, ensuring local testing validates secure client connections.
- **Volume Persistence:** Named volumes (`neo4j_data`, `qdrant_data`) guarantee that graph schemas, vector indices, and learning corrections survive container restarts during iterative LangGraph development.

### 4. Local Development Workflow & Makefile
To standardize the developer experience and reduce command-line friction, a `Makefile` will be implemented at the repository root.

**`Makefile` Command Targets:**
| Target | Command | Description |
| :--- | :--- | :--- |
| `up` | `docker compose up -d neo4j qdrant` + `uv run uvicorn src.main:app --reload` | Boots infrastructure and starts the FastAPI dev server with hot-reload |
| `down` | `docker compose down -v` | Tears down containers and purges named volumes (clean state) |
| `lint` | `uv run ruff check .` + `uv run ruff format .` | Enforces formatting and catches static analysis violations |
| `test` | `uv run pytest -v` | Executes the full test suite with verbose async reporting |
| `shell` | `uv run python` | Spawns an interactive REPL with all project dependencies loaded |
| `clean` | `rm -rf .venv dist build *.egg-info` + cache cleanup | Removes virtual environments and compiled artifacts |

This scaffold guarantees that developers can initialize the entire PRAXIS v2.0 environment with a single `make up` command, while `uv` handles dependency resolution in milliseconds, ensuring the project scales efficiently alongside the Umans API integration.








## Sources

[1] GitHub - cheahjs/free-llm-api-resources: A list of freeLLMinferenceresources accessible viaAPI. · GitHub (source nr: 1)
   URL: https://github.com/cheahjs/free-llm-api-resources

[2] LLMAPIs (source nr: 2)
   URL: https://docs.api.nvidia.com/nim/reference/llm-apis

[3] Ultimate Guide – The BestAPIProviders of Open SourceLLMof 2026 (source nr: 3)
   URL: https://www.siliconflow.com/articles/en/The-best-API-providers-of-Open-Source-LLM

[4] LLMInference&APIDeployment (source nr: 4)
   URL: https://omc.cloud/usecases/llm-inference

[5] 11 BestLLMAPIProviders: Compare Inferencing Performance & Pricing (source nr: 5)
   URL: https://www.helicone.ai/blog/llm-api-providers

[6] LLMGateway - UnifiedAPIfor MultipleLLMProviders (source nr: 6)
   URL: https://llmgateway.io/

[7] Providers | liteLLM (source nr: 7)
   URL: https://docs.litellm.ai/docs/providers

[8] LLMaaSInferenceAPI: LLaMA as aServicefor AI Model Hosting (source nr: 8)
   URL: https://www.databasemart.com/llm-hosting/llmaas

[9] GitHub - 1b5d/llm-api: Run any Large Language Model behind a unifiedAPI· GitHub (source nr: 9)
   URL: https://github.com/1b5d/llm-api

[10] AI Model Serving Architecture: Building ScalableInferenceAPIs for Production Applications (source nr: 10)
   URL: https://www.runpod.io/articles/guides/ai-model-serving-architecture-building-scalable-inference-apis-for-production-applications

[11] The 10 BestLLMAPIProviders: Which Fits Your AI Workflow? | DataCamp (source nr: 11)
   URL: https://www.datacamp.com/blog/best-llm-api-providers

[12] Serve an Open-SourceLLM.LLMInferenceServiceon AWS Elastic… | by Tom Sharp 💻 | Data Science Collective | Medium (source nr: 12)
   URL: https://medium.com/data-science-collective/host-an-open-source-llm-daca461d18cd

[13] What isLLMInference? | IBM (source nr: 13)
   URL: https://www.ibm.com/think/topics/llm-inference

[14] LLMAPIProviders (2026): 12 APIs Compared by Price per 1M Tokens, Rate Limits, and Context (source nr: 14)
   URL: https://www.morphllm.com/llm-api

[15] LLMAPIPricing Comparison 2026: The Complete Guide toInferenceCosts - Featherless (source nr: 15)
   URL: https://featherless.ai/blog/llm-api-pricing-comparison-2026-complete-guide-inference-costs

[16] What IsLLMas-a-Service? (source nr: 16)
   URL: https://www.iguazio.com/glossary/llm-service

[17] Deploy anLLMinferenceserviceon OpenShift AI | Red Hat Developer (source nr: 17)
   URL: https://developers.redhat.com/articles/2025/11/03/deploy-llm-inference-service-openshift-ai

[18] LLMInferenceProviders - by Dr. Nimrita Koul (source nr: 18)
   URL: https://medium.com/@nimritakoul01/llm-inference-providers-7b374695a0a0

[19] Day 50: Building a RESTAPIforLLMInference- DEV Community (source nr: 19)
   URL: https://dev.to/nareshnishad/day-50-building-a-rest-api-for-llm-inference-3o3j

[20] OpenAI-compatibleAPI|LLMInferenceHandbook (source nr: 20)
   URL: https://bentoml.com/llm/llm-inference-basics/openai-compatible-api




## Research Metrics
- Search Iterations: 2
- Generated at: 2026-06-19T10:48:29.595191+00:00