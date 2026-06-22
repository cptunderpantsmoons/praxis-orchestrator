"""LangGraph nodes for Phase 2: triage, context loading, and ReAct reasoning."""

from __future__ import annotations

import asyncio
import json
import os
import re
from datetime import UTC, datetime
from typing import Any

import structlog
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.runnables import RunnableConfig

from praxis.chat.wrappers import UmansChatModel
from praxis.config import get_settings
from praxis.models.schemas import (
    AgentDelegationResult,
    AgentListingResult,
    AgentMetadata,
    CalculationResult,
    CorrectionSummary,
    DocumentAnalysisResult,
    DocumentCreationResult,
    EmailToolResult,
    EmailTriage,
    HermesLearnResult,
    HermesRecallResult,
    HermesStoreResult,
    Intent,
    LDRResult,
    MemoryContext,
    Priority,
)
from praxis.router import UmansConcurrencyRouter
from praxis.services.neo4j_client import Neo4jContextClient
from praxis.services.qdrant_client import QdrantSenderClient
from praxis.tools.email_tools import _reply_email, _send_email
from praxis.tools.stub_tools import dummy_calculator, dummy_search

from .state import AgentState

logger = structlog.get_logger(__name__)

# Patterns that indicate LLM internal reasoning (not meant for the user).
# NOTE: "this is" was intentionally omitted — too many genuine one-line
# replies start with it (e.g., "This is your account summary: $1,234.56.").
# The remaining prefixes are unambiguous reasoning markers.
_REASONING_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in [
        r"^(let me|let's|i should|i need to|i can see|based on|the user|the sender|the email|looking at|i'll|i will|analyzing|checking|reviewing|considering)\b",
        r"^\s*-\s*(PRAXIS|the sender memory|the memory)\s",
        r"^(however|so|therefore|additionally|furthermore),\s*(the user|the sender|i)",
    ]
]

# Safe fallback reply sent when the native ReAct loop exhausts its iterations
# without producing a usable final response. NEVER expose raw LLM reasoning.
SAFE_FALLBACK_REPLY = (
    "I'm reviewing your email and will respond properly shortly.\n\n"
    "--\nPRAXIS | Enterprise Email Assistant"
)


def _strip_prefixes(text: str) -> str:
    """Strip leading ``FINAL:`` prefix and extract ``body=`` from unparsed
    ``TOOL:reply_email(...)`` calls. Returns the text the cleaner should
    operate on.
    """
    text = text.strip()
    if text.upper().startswith("FINAL:"):
        text = text[len("FINAL:"):].strip()

    # If the LLM wrote a TOOL:reply_email(...) call that wasn't parsed (because
    # it spanned multiple lines), extract just the body= parameter as the reply.
    if "TOOL:reply_email(" in text or "TOOL:send_email(" in text:
        body_match = re.search(r"body=(.+?)(?:\)\s*$|$)", text, re.DOTALL)
        if body_match:
            text = body_match.group(1).strip()

    return text


def _strip_reasoning_lines(text: str) -> str:
    """Strip LLM internal reasoning lines from ``text`` and return the remainder.

    Removes the same prefixes as :data:`_REASONING_PATTERNS` plus stray
    ``FINAL:`` lines, but does NOT apply the raw-text fallback or PRAXIS
    signature that :func:`_clean_reply_response` adds. Returns an empty
    string when every non-blank line was reasoning — this lets callers
    distinguish "all reasoning" (discard and retry) from "genuine reply
    with some reasoning prefix" (use the cleaned text).
    """
    text = _strip_prefixes(text)

    lines = text.strip().splitlines()
    clean_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if clean_lines:
                clean_lines.append("")
            continue
        if any(p.match(stripped) for p in _REASONING_PATTERNS):
            continue
        # Also strip any stray FINAL: lines in the middle
        if stripped.upper().startswith("FINAL:"):
            continue
        clean_lines.append(line)

    return "\n".join(clean_lines).strip()


def _clean_reply_response(text: str) -> str:
    """Strip LLM internal reasoning from the reply text before sending as email.

    The kimi/qwen models often prefix the reply with analysis like
    "Let me analyze this email..." or "The user is asking..." — this function
    removes those lines so only the clean, user-facing response remains.
    Also strips a leading 'FINAL:' prefix if the model put it on its own line.
    """
    stripped = _strip_reasoning_lines(text)
    # Fall back to the prefix-stripped raw text when every line matched a
    # reasoning pattern — preserves the legacy behaviour where a one-line
    # reasoning fragment is still returned rather than dropped entirely.
    result = stripped or _strip_prefixes(text)

    # Ensure the Praxis signature is present
    if "PRAXIS" not in result:
        result += "\n\n--\nPRAXIS | Enterprise Email Assistant\nPowered by LangGraph + Umans AI"

    return result

# Model assignment per the DRD:
#   Triage        -> Qwen   (umans-flash)
#   ReAct         -> Kimi   (umans-coder)
#   Memory/Learn  -> GLM    (umans-glm-5.2) - used later
TRIAGE_MODEL = "umans-flash"
REACT_MODEL = "umans-flash"  # qwen — better at conversational email replies than kimi


def _get_router_and_model(
    model_name: str,
    router: UmansConcurrencyRouter | None = None,
) -> tuple[UmansConcurrencyRouter, UmansChatModel]:
    """Return a shared router and configured model wrapper."""
    if router is None:
        router = UmansConcurrencyRouter()
    model = UmansChatModel.create(model_name, router=router)
    return router, model


def _count_tokens_approx(text: str) -> int:
    """Very rough token estimate (4 chars ~= 1 token)."""
    return max(1, len(text) // 4)


async def triage_node(
    state: AgentState,
    config: RunnableConfig | None = None,
) -> dict[str, Any]:
    """Classify the inbound email and route to the next node.

    Uses Qwen (umans-flash) with JSON-mode prompting and validates the output
    against the EmailTriage Pydantic model.
    """
    email = state["email_content"]
    metadata = state.get("metadata") or AgentMetadata()
    metadata.model_calls["qwen"] += 1

    configurable = {} if config is None else (config.get("configurable") or {})
    router = configurable.get("router")

    triage_schema = EmailTriage.model_json_schema()
    system_prompt = (
        "You are an email triage classifier. Analyze the email below and "
        "respond with a single JSON object matching this schema:\n"
        f"{json.dumps(triage_schema, indent=2)}\n"
        "Rules:\n"
        "- priority is 'high' for urgent, time-sensitive, or VIP senders.\n"
        "- intent is 'correction' if the user corrects a previous action.\n"
        "- is_spam is true only for obvious spam / phishing.\n"
        "- sender_vip is true if the sender appears important.\n"
        "- confidence is 0.0-1.0."
    )

    user_prompt = f"Subject: {email.subject}\n\n{email.body}"

    _router, model = _get_router_and_model(TRIAGE_MODEL, router=router)

    _ainvoke_result = model.ainvoke(
        [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt),
        ]
    )
    response = _ainvoke_result if not asyncio.iscoroutine(_ainvoke_result) else await _ainvoke_result

    content = response.content if isinstance(response.content, str) else str(response.content)
    content = re.sub(r"```json\s*|\s*```", "", content).strip()

    try:
        triage = EmailTriage.model_validate_json(content)
    except Exception as exc:
        logger.warning("triage.parse_failed", content_preview=content[:200], error=str(exc))
        # Fallback to a neutral triage so the graph can continue
        triage = EmailTriage(
            priority=Priority.NORMAL,
            intent=Intent.GENERAL_INQUIRY,
            sentiment="neutral",
            is_spam=False,
            sender_vip=False,
            confidence=0.0,
        )

    return {
        "triage_result": triage,
        "metadata": metadata,
        "messages": [AIMessage(content=f"Triage: {triage.model_dump_json()}")],
    }


async def context_loading_node(
    state: AgentState,
    config: RunnableConfig | None = None,
) -> dict[str, Any]:
    """Load sender history and relevant corrections from Neo4j.

    In Phase 2, if Neo4j is not running, the client gracefully returns empty
    context so the graph remains testable.
    """
    email = state["email_content"]
    triage = state.get("triage_result")

    configurable = {} if config is None else (config.get("configurable") or {})
    neo4j_client = configurable.get("neo4j_client")

    client = neo4j_client
    if client is None:
        client = Neo4jContextClient()
        await client.connect()

    intent_category = triage.intent.value if triage else "general"
    history = await client.fetch_sender_history(email.sender, limit=5)
    corrections = await client.fetch_relevant_corrections(
        email.sender,
        intent_category=intent_category,
        limit=5,
    )

    if neo4j_client is None:
        await client.close()

    # Recall per-sender memories from Hindsight (each sender gets an
    # isolated memory bank: praxis:user:<email>).
    from praxis.services import hindsight_client

    hindsight_memories: list[dict[str, Any]] = []
    try:
        await hindsight_client.ensure_bank(email.sender)
        hindsight_memories = await hindsight_client.recall_memory(
            email.sender,
            query=f"{email.subject} {email.body}",
            limit=5,
        )
    except Exception as exc:
        logger.warning("context.hindsight_failed", error=str(exc))

    memory = MemoryContext(
        sender_history=history,
        corrections=corrections,
        hindsight_memories=hindsight_memories,
    )
    return {"memory_context": memory}


async def embedding_node(
    state: AgentState,
    config: RunnableConfig | None = None,
) -> dict[str, Any]:
    """Embed the sender email and persist to Qdrant (REQ-306).

    Calls :class:`EmbeddingService` for a vector, then upserts it via
    :class:`QdrantSenderClient` with structured metadata
    (``sender_domain``, ``first_seen``, ``last_seen``, ``total_emails``).

    On any failure (embedding call fails, Qdrant unavailable, etc.) the
    node logs a warning and returns an empty ``embedding`` value rather
    than raising — this preserves the graceful-degradation contract
    required by REQ-306: ``Handle embedding failures gracefully``.
    """
    email = state["email_content"]
    sender = email.sender

    configurable = {} if config is None else (config.get("configurable") or {})

    embedding_service = configurable.get("embedding_service")
    qdrant_client = configurable.get("qdrant_client")

    owns_qdrant = qdrant_client is None
    if owns_qdrant:
        qdrant_client = QdrantSenderClient()

    try:
        # Lazy-create the embedding service so callers can inject a mock
        if embedding_service is None:
            from praxis.services.embedding_service import EmbeddingService

            embedding_service = EmbeddingService()

        vector = await embedding_service.embed_sender(sender)
        ok = await qdrant_client.upsert_sender(sender, vector)
        if not ok:
            logger.warning("embedding_node.qdrant_failed", sender=sender)
        return {"metadata": state.get("metadata") or AgentMetadata()}
    except Exception as exc:
        logger.warning("embedding_node.failed", sender=sender, error=str(exc))
        return {"metadata": state.get("metadata") or AgentMetadata()}
    finally:
        if owns_qdrant and qdrant_client is not None:
            await qdrant_client.close()


def _build_correction_section(corrections: list, sender_email: str) -> str:
    """Build a corrections section for the ReAct system prompt.

    Ranks corrections by sender match (exact > domain > fuzzy), recency,
    and confidence. Returns the top 3 formatted as structured messages.

    Args:
        corrections: List of correction dicts (or CorrectionSummary objects).
        sender_email: Current email sender for matching.

    Returns:
        String to prepend to system prompt, empty if no corrections.
    """
    if not corrections:
        return ""

    def _match_score(corr: dict, email: str) -> float:
        """Calculate sender match score."""
        corr_sender = corr.get("sender", "") if isinstance(corr, dict) else ""
        if corr_sender == email:
            return 1.0  # Exact match
        # Domain match
        corr_domain = corr_sender.split("@")[-1] if "@" in corr_sender else ""
        email_domain = email.split("@")[-1] if "@" in email else ""
        if corr_domain == email_domain:
            return 0.7  # Domain match
        return 0.4  # Fuzzy

    def _recency_score(corr: dict) -> float:
        """Exponential decay based on correction timestamp (1 week half-life)."""
        ts = corr.get("timestamp", "") if isinstance(corr, dict) else ""
        try:
            from datetime import datetime
            corr_time = datetime.fromisoformat(ts)
            hours_since = (datetime.now(UTC) - corr_time).total_seconds() / 3600
            import math
            return math.exp(-hours_since / 168)  # 168 hours = 1 week
        except Exception:
            return 0.5  # Unknown age, mid-range score

    def _confidence_score(corr: dict) -> float:
        """Extract confidence from correction record."""
        if isinstance(corr, dict):
            return corr.get("confidence", 0.5)
        # If it's a CorrectionSummary-like object
        return getattr(corr, "confidence", 0.5)

    # Rank corrections
    scored = []
    for c in corrections:
        c_dict = c.model_dump() if hasattr(c, "model_dump") else c
        score = (
            _match_score(c_dict, sender_email) * 0.5 +
            _recency_score(c_dict) * 0.3 +
            _confidence_score(c_dict) * 0.2
        )
        scored.append((score, c_dict))

    # Sort by score descending, take top 3
    scored.sort(key=lambda x: x[0], reverse=True)
    top_n = scored[:3]

    # Build formatted string (500 token budget)
    lines: list[str] = ["\n[LEARNED CORRECTIONS FROM PAST FEEDBACK]\n"]
    for _score, c in top_n:
        sender = c.get("sender", "unknown")
        orig = c.get("original_triage", {})
        corr_type = orig.get("intent", "unknown") if isinstance(orig, dict) else "unknown"
        lines.append(
            f"[CORRECTION] Sender: {sender}\n"
            f"[CORRECTION] Original: intent={corr_type}\n"
            f"[CORRECTION] Take this into account.\n"
        )

    result = "\n".join(lines)
    # Token budget: rough estimate ~4 chars per token
    if len(result) > 2000:  # ~500 tokens * 4 chars/token
        # Truncate to last 2 corrections
        lines = ["\n[LEARNED CORRECTIONS FROM PAST FEEDBACK]\n"]
        for _score, c in top_n[-2:]:
            sender = c.get("sender", "unknown")
            lines.append(f"[CORRECTION] Sender: {sender}\n")
        result = "\n".join(lines)

    return result


def _build_praxis_v22_prompt(
    inbox_id: str,
    message_id: str,
    sender: str,
    user_region: str,
    corrections_text: str,
    attachment_lines: list[str],
) -> str:
    """Build the PRAXIS v2.2 Agency Delegation Edition system prompt.

    Replaces the legacy enterprise-assistant prompt with the Central Orchestrator
    identity, security protocols, Agency Roster delegation rules, region-awareness
    directives, and institutional tone standards.
    """
    region_display = user_region if user_region else "Unknown (provide internationally applicable guidance with jurisdictional caveats)"

    prompt = f"""\
# CORE IDENTITY: THE CENTRAL ORCHESTRATOR
You are PRAXIS, an Autonomous Cognitive Inbox Operator (CIO) and the Central Orchestrator for The Agency Roster — a massive, multi-disciplinary collective of specialized autonomous agents developed by Audit Intellect.
You are NOT a generalist chatbot. You do not attempt to answer complex domain-specific questions yourself.
Your primary function is to analyze incoming requests, route them to the exact specialist(s) within The Agency Roster via the AgentDelegator, adopt their specific domain expertise, and execute complex workflows autonomously via email.

# SECURITY & SANDBOX PROTOCOLS
1. Zero-Trust Boundary: You operate in a sandboxed mailbox. Never execute commands outside your explicit toolset.
2. PII Awareness: Mask or ignore sensitive PII (SSNs, credentials, financial IDs) in incoming payloads. Do not repeat PII in your responses.
3. No Hallucinated Expertise: If a request falls outside the capabilities of The Agency Roster, state clearly that the request is out of scope. Do not fabricate domain knowledge.

# THE AGENCY ROSTER & DELEGATION PROTOCOL
You have access to the AgentDelegator, which routes tasks to specialized agents. Before executing ANY complex domain task, you MUST perform Expert Routing:

### 1. Agent Selection & Routing
- Identify the primary domain and select the appropriate agent_slug (e.g., "finance-financial-analyst", "engineering-backend-architect", "marketing-seo-specialist").
- If you are unsure of the exact slug, use the list_agents tool to search by keyword or division before delegating.
- Persona Adoption: Once routed, the Delegator will inject the specialist's exact system prompt. You must present their response using their specific terminology, frameworks, and quality standards.

### 2. Region-Awareness
- The sender's region has been determined: {region_display}.
- You MUST pass this region to the delegate_to_agent tool. The specialist will automatically adapt their advice to local laws, regulations, taxes, and business norms. If the region is unknown, the specialist will provide internationally applicable guidance with jurisdictional caveats.

### 3. Multi-Agent Collaboration
- For complex requests, orchestrate a pipeline.
- Example: A request to "Build a new Drupal Commerce checkout flow" requires sequential delegation:
  1. delegate_to_agent(agent_slug="product-product-manager", task="Define requirements...")
  2. delegate_to_agent(agent_slug="cms-drupal-shopping-cart-engineer", task="Implement flow based on requirements...")
  3. delegate_to_agent(agent_slug="security-application-security-engineer", task="Review for PCI compliance...")

# MEMORY & CORRECTION ALIGNMENT (HERMES)
You possess a persistent, graph-backed long-term memory. Principle: "Correct once. Aligned forever."
- Context Retrieval: You have been provided with a memory_context block. You MUST adhere to these rules implicitly.
- Correction Detection: If the user's email contains "Correction:", "Actually,", "Note:", or "Fix:", immediately route to the hermes_learn tool. Extract the rule, categorize it, and save it to the graph.

# TOOL EXECUTION RULES
You have access to specialized production engines. Route to them based on the active Agent's needs:

1. Expert Delegation - delegate_to_agent & list_agents
   - USE WHEN: The user asks a domain-specific question, requires code implementation, strategic advice, or creative work.
   - RULE: Always prefer delegation over answering directly. Pass the agent_slug, task, context, and region.

2. Local Deep Research (LDR) - deep_research
   - USE WHEN: The active Agent requires market intelligence, factual verification, or competitive analysis.
   - RULE: Never rely on pre-trained knowledge for current data. Always use LDR to compile verified, cited reports.

3. Document Analysis - analyze_document
   - USE WHEN: The active Agent needs to extract data from attached files (.docx, .pdf, .xlsx, .csv, .pptx, .txt).
   - RULE: Always analyze attached documents before responding about their contents.

4. Report Generation - create_report
   - USE WHEN: The active Agent needs to produce a formatted .docx deliverable.

5. Memory Tools - hermes_recall, hermes_store, hermes_learn
   - USE WHEN: You need to recall past interactions, store a fact, or process a user correction.

# RESPONSE FORMATTING & TONE
When drafting your final response:
1. Tone: Professional, precise, objective, and institutional (Audit Intellect standard). Use active, precise verbs (execute, compile, extract, align, redact).
2. Structure:
   - Routing Acknowledgment: Briefly state which specialist(s) from The Agency Roster handled the request.
   - Execution/Findings: Present data, code, or analysis clearly. Use domain-appropriate formatting.
   - Required Action: If human intervention is needed, state it explicitly under a "Required Action" header.
3. Citations: If using LDR, always cite source URLs.
4. Brevity: Enterprise operators value time. Be concise. No conversational filler.
5. Always end your reply with:
   --
   PRAXIS | Enterprise Email Assistant

# EMAIL CONTEXT (for tool routing)
- inbox_id: {inbox_id}
- message_id: {message_id}
- sender: {sender}

{corrections_text}
"""

    # Append attachment-specific instructions if attachments are present
    if attachment_lines:
        prompt += "# ATTACHMENT INSTRUCTIONS\n" + "\n".join(attachment_lines) + "\n\n"

    prompt += """\
# OUTPUT FORMAT
Use exactly one of these formats per line:
  TOOL:reply_email(inbox_id=<id>, message_id=<id>, body=<your reply text>)
  TOOL:delegate_to_agent(agent_slug=<agent-id>, task=<task description>, context=<optional context>, region=<user region>)
  TOOL:list_agents(division=<optional filter>, keyword=<optional search>, region=<optional>)
  TOOL:deep_research(query=<research question>, mode=<quick|full>)
  TOOL:analyze_document(file_path=<path to .docx/.pdf/.xlsx/.csv>)
  TOOL:create_report(title=<title>, sections=<Heading1::content1||Heading2::content2>)
  TOOL:hermes_recall(query=<what to look up>)
  TOOL:hermes_store(fact=<fact to remember>)
  TOOL:hermes_learn(correction=<correction text>)
  TOOL:send_email(to=<recipient>, subject=<subject>, body=<body>)
  FINAL:<your reply text if you cannot use reply_email>

Begin your ReAct (Reason and Act) loop. Think step-by-step:
1. Confirm the routing to the correct Agency Roster specialist(s) and extract the region.
2. Select the appropriate tools (delegate_to_agent, deep_research, analyze_document, hermes_recall).
3. Execute the task using the specialist's exact domain expertise.
4. Generate the final email response via reply_email.
"""

    return prompt


async def react_node(
    state: AgentState,
    config: RunnableConfig | None = None,
    *,
    max_iterations: int = 3,
) -> dict[str, Any]:
    """ReAct reasoning loop — routes through native tool-calling or legacy text protocol.

    When ``settings.tool_protocol == "native"`` (default), builds the model with
    ``bind_tools([...])`` and loops calling ``model.ainvoke``, executing
    ``response.tool_calls`` via ``_execute_tool`` and appending ``ToolMessage``
    observations. Breaks when ``response.content`` is non-empty and no tool_calls.
    If ``max_iterations`` is exhausted without resolution, sends the safe
    fallback reply.

    When ``settings.tool_protocol == "legacy"``, runs the existing ``TOOL:``/``FINAL:``
    text-protocol path (v0.1.0 behavior preserved verbatim).
    """
    settings = get_settings()
    if settings.tool_protocol == "legacy":
        return await _react_legacy(state, config, max_iterations=max_iterations)
    return await _react_native(state, config, max_iterations=max_iterations)


async def _react_native(
    state: AgentState,
    config: RunnableConfig | None = None,
    *,
    max_iterations: int = 3,
) -> dict[str, Any]:
    """Native tool-calling ReAct loop.

    Builds the model with ``bind_tools([...])`` and loops calling
    ``model.ainvoke``. Executes ``response.tool_calls`` via ``_execute_tool``
    (which routes ``reply_email``/``send_email`` through the dedup guard),
    appends ``ToolMessage`` observations, and either returns when
    ``response.content`` is non-empty (no tool_calls) or hits ``max_iterations``
    and sends a safe fallback reply.
    """
    email = state["email_content"]
    triage = state.get("triage_result")
    memory = state.get("memory_context") or MemoryContext()

    metadata = state.get("metadata") or AgentMetadata()
    if metadata.started_at is None:
        metadata.started_at = datetime.now(UTC)
    # Ensure sent_message_ids set is initialised so the dedup guard can track
    # reply_email/send_email calls across iterations.
    sent_message_ids: set[str] = set(metadata.sent_message_ids or set())

    settings = get_settings()
    configurable = {} if config is None else (config.get("configurable") or {})
    router = configurable.get("router")
    hermes_service = configurable.get("hermes_service")
    ldr_service = configurable.get("ldr_service")
    document_service = configurable.get("document_service")
    agent_delegator = configurable.get("agent_delegator")
    _settings = configurable.get("settings") or settings
    user_region = configurable.get("user_region") or _settings.default_region or ""

    _router, model = _get_router_and_model(REACT_MODEL, router=router)

    # Build the available tool registry. Email tools are bound so the model can
    # invoke reply_email/send_email with structured arguments.
    from praxis.tools.email_tools import reply_email_tool, send_email_tool

    tools: list[Any] = [
        reply_email_tool,
        send_email_tool,
        dummy_search,
        dummy_calculator,
    ]
    if settings.sender_style_enabled:
        try:
            from praxis.features.sender_style import update_sender_style_tool
            tools.append(update_sender_style_tool)
        except ImportError:
            # Sender-style feature not yet implemented; skip silently.
            pass

    bound_model = model.bind_tools(tools)

    # Build system context
    context_lines: list[str] = []
    if triage:
        context_lines.append(f"TRIAGE: {triage.model_dump_json()}")
    if memory.sender_history:
        context_lines.append("SENDER HISTORY:")
        for thread in memory.sender_history:
            context_lines.append(f"- {thread.subject} ({thread.timestamp.isoformat()})")
    if memory.corrections:
        context_lines.append("RELEVANT CORRECTIONS:")
        for correction in memory.corrections:
            context_lines.append(f"- {correction.rule}")
    if memory.hindsight_memories:
        context_lines.append("SENDER MEMORY (from past interactions):")
        for mem in memory.hindsight_memories:
            mem_text = mem.get("text", str(mem)[:200])
            context_lines.append(f"- {mem_text}")

    corrections_text = _build_correction_section(memory.corrections, email.sender)

    attachment_lines: list[str] = []
    if email.attachments:
        attachment_lines.append("ATTACHMENTS (already saved to disk and available for analysis):")
        for idx, att in enumerate(email.attachments, 1):
            quoted_path = str(att.local_path).replace("\\", "/") if att.local_path else ""
            attachment_lines.append(
                f"  {idx}. {att.filename} "
                f"(type={att.content_type}, size={att.size}, path={quoted_path})"
            )
        attachment_lines.append(
            "MANDATORY: If the email includes attachments, call analyze_document "
            "on every .docx, .pdf, .xlsx, .xls, .csv, .pptx, or .txt file "
            "before composing your reply. Then reply with the findings."
        )

    inbox_id = (
        settings.agentmail_inbox_id
        or os.environ.get("AGENTMAIL_INBOX_ID", "")
        or "ib_default_agent_inbox"
    )

    system_prompt = _build_praxis_v22_prompt(
        inbox_id=inbox_id,
        message_id=email.message_id,
        sender=email.sender,
        user_region=user_region,
        corrections_text=corrections_text,
        attachment_lines=attachment_lines,
    )

    user_prompt = "\n".join(
        [
            "EMAIL:",
            f"From: {email.sender}",
            f"Subject: {email.subject}",
            f"Body:\n{email.body}",
            *attachment_lines,
            *context_lines,
        ]
    )

    messages: list[BaseMessage] = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_prompt),
    ]

    tool_outputs: dict[str, Any] = {}
    final_response: str | None = None

    for _iteration in range(max_iterations):
        metadata.model_calls["qwen"] += 1
        response = await bound_model.ainvoke(messages)
        messages.append(response)

        if response.tool_calls:
            for tc in response.tool_calls:
                tool_name = tc.get("name", "")
                tool_args = tc.get("args", {}) or {}
                tool_call_id = tc.get("id", "")
                # Enrich agent tools with the inferred user region if not provided
                if user_region and tool_name in ("delegate_to_agent", "list_agents"):
                    tool_args.setdefault("region", user_region)
                _name, tool_result = await _execute_tool(
                    {"name": tool_name, **tool_args},
                    hermes_service=hermes_service,
                    ldr_service=ldr_service,
                    document_service=document_service,
                    agent_delegator=agent_delegator,
                    sent_message_ids=sent_message_ids,
                    state=state,
                )
                tool_outputs[tool_name] = tool_result
                observation = (
                    tool_result.model_dump_json()
                    if hasattr(tool_result, "model_dump_json")
                    else str(tool_result)
                )
                messages.append(
                    ToolMessage(content=observation, tool_call_id=tool_call_id)
                )
            continue

        # No tool_calls — check if the content is a real reply or just reasoning.
        content = (
            response.content
            if isinstance(response.content, str)
            else str(response.content)
        )
        if content.strip():
            # Pre-clean with the reasoning-line stripper (no fallback, no
            # signature) to decide whether this is a genuine reply or just
            # LLM internal reasoning that should be discarded.
            #
            # - If the stripped result is non-empty, the model produced real
            #   user-facing content (possibly with some reasoning prefix
            #   lines mixed in). Use the full ``_clean_reply_response``
            #   output (which strips reasoning + ensures the PRAXIS
            #   signature) as the final reply and break.
            # - If the stripped result is empty, every line was reasoning.
            #   Keep iterating to give the model another chance; the loop
            #   will exhaust ``max_iterations`` and trigger the safe
            #   fallback below.
            #
            # This avoids the false-positive where a genuine one-line reply
            # that happens to start with a reasoning-pattern prefix (e.g.
            # "This is your account summary: $1,234.56.") would be
            # discarded — ``_strip_reasoning_lines`` returns the line
            # unchanged when no pattern matches it.
            stripped = _strip_reasoning_lines(content)
            if stripped:
                final_response = _clean_reply_response(content)
                break
            # Pure reasoning — keep iterating.
        # Either empty content or pure reasoning text — keep iterating. The
        # loop will exhaust max_iterations and trigger the safe fallback.

    if final_response is None:
        # Loop exhausted without a final reply — send safe fallback.
        logger.warning(
            "react.max_iterations_exhausted",
            message_id=email.message_id,
            iterations=max_iterations,
        )
        try:
            await _reply_email(
                inbox_id=inbox_id,
                message_id=email.message_id,
                body=SAFE_FALLBACK_REPLY,
                sent_message_ids=sent_message_ids,
            )
            tool_outputs["reply_email"] = EmailToolResult(
                message="Safe fallback sent.",
                success=True,
                body=SAFE_FALLBACK_REPLY,
            )
        except Exception as exc:
            logger.error("react.safe_fallback_failed", error=str(exc))
            tool_outputs["reply_email"] = EmailToolResult(
                message=f"Safe fallback failed: {exc}",
                success=False,
                body=SAFE_FALLBACK_REPLY,
            )
        final_response = SAFE_FALLBACK_REPLY

    # Strip LLM internal reasoning from the response before it goes out as an
    # email (defensive — native tool-calling rarely produces reasoning text
    # since the model is told to call tools, but be safe). ``_clean_reply_response``
    # also ensures the PRAXIS signature is present, so no separate signature
    # check is needed here.
    final_response = _clean_reply_response(final_response)

    logger.info(
        "react.loop_complete",
        final_response=final_response[:200],
        tools_used=list(tool_outputs.keys()),
    )

    metadata.sent_message_ids = sent_message_ids
    metadata.finished_at = datetime.now(UTC)
    return {
        "tool_outputs": tool_outputs,
        "final_response": final_response,
        "metadata": metadata,
        "messages": [AIMessage(content=final_response)],
    }


async def _react_legacy(
    state: AgentState,
    config: RunnableConfig | None = None,
    *,
    max_iterations: int = 3,
) -> dict[str, Any]:
    """Legacy TOOL:/FINAL: text-protocol ReAct loop (v0.1.0 behavior).

    Preserved verbatim from v0.1.0 for rollback safety. Do NOT modify.
    """
    email = state["email_content"]
    triage = state.get("triage_result")
    memory = state.get("memory_context") or MemoryContext()

    metadata = state.get("metadata") or AgentMetadata()
    if metadata.started_at is None:
        metadata.started_at = datetime.now(UTC)

    configurable = {} if config is None else (config.get("configurable") or {})
    router = configurable.get("router")
    hermes_service = configurable.get("hermes_service")
    ldr_service = configurable.get("ldr_service")
    document_service = configurable.get("document_service")
    agent_delegator = configurable.get("agent_delegator")
    # Get settings for default_region fallback (and other config defaults)
    _settings = configurable.get("settings") or get_settings()
    user_region = configurable.get("user_region") or _settings.default_region or ""

    _router, model = _get_router_and_model(REACT_MODEL, router=router)

    # Build system context
    context_lines: list[str] = []
    if triage:
        context_lines.append(f"TRIAGE: {triage.model_dump_json()}")
    if memory.sender_history:
        context_lines.append("SENDER HISTORY:")
        for thread in memory.sender_history:
            context_lines.append(f"- {thread.subject} ({thread.timestamp.isoformat()})")
    if memory.corrections:
        context_lines.append("RELEVANT CORRECTIONS:")
        for correction in memory.corrections:
            context_lines.append(f"- {correction.rule}")
    if memory.hindsight_memories:
        context_lines.append("SENDER MEMORY (from past interactions):")
        for mem in memory.hindsight_memories:
            mem_text = mem.get("text", str(mem)[:200])
            context_lines.append(f"- {mem_text}")

    # P3-F: Prepend top-N corrections to system prompt
    corrections_text = _build_correction_section(memory.corrections, email.sender)

    # Build attachment context for the ReAct agent. Even if the body is short
    # or purely conversational, the agent must analyse documents before replying.
    attachment_lines: list[str] = []
    if email.attachments:
        attachment_lines.append("ATTACHMENTS (already saved to disk and available for analysis):")
        for idx, att in enumerate(email.attachments, 1):
            quoted_path = str(att.local_path).replace("\\", "/") if att.local_path else ""
            attachment_lines.append(
                f"  {idx}. {att.filename} "
                f"(type={att.content_type}, size={att.size}, path={quoted_path})"
            )
        attachment_lines.append(
            "MANDATORY: If the email includes attachments, call analyze_document "
            "on every .docx, .pdf, .xlsx, .xls, .csv, .pptx, or .txt file "
            "before composing your reply. Then reply with the findings."
        )

    # Email context the LLM needs to construct a reply.
    inbox_id = os.environ.get("AGENTMAIL_INBOX_ID", "ib_default_agent_inbox")

    system_prompt = _build_praxis_v22_prompt(
        inbox_id=inbox_id,
        message_id=email.message_id,
        sender=email.sender,
        user_region=user_region,
        corrections_text=corrections_text,
        attachment_lines=attachment_lines,
    )

    user_prompt = "\n".join(
        [
            "EMAIL:",
            f"From: {email.sender}",
            f"Subject: {email.subject}",
            f"Body:\n{email.body}",
            *attachment_lines,
            *context_lines,
        ]
    )

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_prompt),
    ]

    tool_outputs: dict[str, Any] = {}
    final_response = ""

    for _iteration in range(max_iterations):
        metadata.model_calls["kimi"] += 1
        _ainvoke_result = model.ainvoke(messages)
        response = _ainvoke_result if not asyncio.iscoroutine(_ainvoke_result) else await _ainvoke_result
        content = response.content if isinstance(response.content, str) else str(response.content)
        lines = [line.strip() for line in content.splitlines() if line.strip()]

        action_taken = False
        for line in lines:
            if line.startswith("FINAL:"):
                final_response = line[6:].strip()
                break

            tool_call = _parse_tool_call(line)
            if tool_call:
                # Enrich agent tools with the inferred user region if not provided
                if user_region and tool_call["name"] in ("delegate_to_agent", "list_agents"):
                    tool_call.setdefault("region", user_region)
                tool_name, tool_result = await _execute_tool(
                    tool_call, hermes_service=hermes_service,
                    ldr_service=ldr_service, document_service=document_service,
                    agent_delegator=agent_delegator,
                )
                tool_outputs[tool_name] = tool_result
                tool_message = HumanMessage(
                    content=f"Observation from {tool_name}: {tool_result.model_dump_json()}"
                )
                messages.append(tool_message)
                action_taken = True

        if final_response:
            break
        if not action_taken:
            # Model didn't emit a recognizable action; treat the response as final
            final_response = content.strip()
            break

    if not final_response:
        final_response = "I reviewed the email but could not determine a specific action."

    # Strip LLM internal reasoning from the response before it goes out as an
    # email.  Small models (kimi/qwen) often prefix the reply with analysis
    # like "Let me analyze..." or "The user is asking..." — the user should
    # never see that.
    final_response = _clean_reply_response(final_response)

    logger.info("react.loop_complete", final_response=final_response[:200], tools_used=list(tool_outputs.keys()))

    # Fallback: if the LLM produced a text response but never successfully sent
    # a reply (either didn't call reply_email, or the call failed), automatically
    # send the response as a reply email.
    prior_reply = tool_outputs.get("reply_email")
    reply_already_sent = prior_reply is not None and getattr(prior_reply, "success", False)
    # If reply_email was called but failed, use its body (which has the full
    # formatted response) instead of final_response (which might be just a
    # brief acknowledgment after the tool call).
    if prior_reply is not None and not reply_already_sent:
        reply_body = getattr(prior_reply, "body", "") or final_response
    else:
        reply_body = final_response
    if not reply_already_sent and reply_body:
        inbox_id = os.environ.get("AGENTMAIL_INBOX_ID", "ib_default_agent_inbox")
        logger.info("react.auto_reply_sending", sender=email.sender, message_id=email.message_id)
        try:
            reply_result = ""
            # Try reply_to_message first (preserves thread context). If the
            # message_id is empty or the reply fails, fall back to send_message
            # (sends a new email to the sender).
            if email.message_id:
                reply_result = await _reply_email(
                    inbox_id=inbox_id,
                    message_id=email.message_id,
                    body=reply_body,
                )
                if "successfully" not in reply_result.lower():
                    logger.warning("react.reply_failed_trying_send", reply_result=reply_result[:200])
                    reply_result = ""  # fall through to send_message

            if not reply_result or "successfully" not in reply_result.lower():
                # Extract bare email from "Name <addr@domain>" or use as-is
                sender_addr = email.sender
                if "<" in sender_addr and ">" in sender_addr:
                    sender_addr = sender_addr[sender_addr.rfind("<")+1:sender_addr.rfind(">")].strip()
                reply_result = await _send_email(
                    to=sender_addr,
                    subject=f"Re: {email.subject}" if email.subject else "Re: Your email",
                    body=reply_body,
                )

            success = "successfully" in reply_result.lower()
            sent_msg_id = ""
            if "Message ID:" in reply_result:
                sent_msg_id = reply_result.split("Message ID:")[-1].strip()
            tool_outputs["reply_email"] = EmailToolResult(
                message=reply_result,
                message_id=sent_msg_id,
                success=success,
            )
            logger.info(
                "react.auto_reply_sent",
                sender=email.sender,
                message_id=email.message_id,
                success=success,
                reply_msg_id=sent_msg_id,
            )

            # Retain this interaction in the sender's Hindsight memory bank
            # so future emails from this sender get contextual recall.
            if success:
                try:
                    from praxis.services import hindsight_client

                    await hindsight_client.retain_memory(
                        email.sender,
                        content=(
                            f"Sender: {email.sender}\n"
                            f"Subject: {email.subject}\n"
                            f"Incoming: {email.body[:500]}\n"
                            f"Praxis replied: {reply_body[:500]}"
                        ),
                    )
                except Exception as retain_exc:
                    logger.warning("hindsight.retain_failed", error=str(retain_exc))
        except Exception as exc:
            logger.warning("react.auto_reply_failed", sender=email.sender, error=str(exc))
            tool_outputs["reply_email"] = EmailToolResult(
                message=f"Auto-reply failed: {exc}",
                success=False,
            )

    metadata.finished_at = datetime.now(UTC)
    return {
        "tool_outputs": tool_outputs,
        "final_response": final_response,
        "metadata": metadata,
        "messages": [AIMessage(content=final_response)],
    }


def _parse_tool_call(line: str) -> dict[str, str] | None:
    """Parse a TOOL:<name>(key=value) action line."""
    match = re.match(r"^TOOL:(\w+)\((.*)\)$", line)
    if not match:
        return None
    tool_name = match.group(1)
    args_str = match.group(2)

    args: dict[str, str] = {}
    # Simple key=value parser; values may be quoted
    for pair in re.findall(r'(\w+)=(["\'].*?["\']|[^,\s]+)', args_str):
        key, value = pair
        value = value.strip("\"'")
        args[key] = value
    return {"name": tool_name, **args}


async def _execute_tool(
    tool_call: dict[str, str],
    hermes_service: Any | None = None,
    ldr_service: Any | None = None,
    document_service: Any | None = None,
    agent_delegator: Any | None = None,
    sent_message_ids: set[str] | None = None,
    state: AgentState | None = None,
) -> tuple[str, Any]:
    """Execute a tool and return (tool_name, typed_output).

    Hermes tools (hermes_recall, hermes_store, hermes_learn) require the
    HermesService instance, passed from react_node via configurable.

    For ``reply_email`` and ``send_email``, the ``sent_message_ids`` set is
    passed through to the underlying ``_reply_email`` / ``_send_email``
    functions in ``email_tools.py``. The dedup guard lives there — at the
    actual send boundary — so this function does NOT short-circuit duplicate
    sends itself. The ``state`` parameter is used to extract the inbound
    ``message_id`` for dedup tracking when the tool call itself doesn't
    supply one (native tool-calling path).
    """
    name = tool_call.pop("name")
    if name == "dummy_search":
        query = tool_call.get("query", "")
        _ainvoke_result = dummy_search.ainvoke({"query": query})
        result = _ainvoke_result if not asyncio.iscoroutine(_ainvoke_result) else await _ainvoke_result
        return name, result
    if name == "dummy_calculator":
        expression = tool_call.get("expression", "")
        _ainvoke_result = dummy_calculator.ainvoke({"expression": expression})
        result = _ainvoke_result if not asyncio.iscoroutine(_ainvoke_result) else await _ainvoke_result
        return name, result

    # ── Hermes memory tools (REQ-309) ──────────────────────────
    if name == "hermes_recall":
        query = tool_call.get("query", "")
        if hermes_service is None:
            return name, HermesRecallResult(
                query=query,
                context_summary="Hermes service unavailable; recall skipped.",
                success=False,
            )
        try:
            result = await hermes_service.recall(query, context={})
            return name, HermesRecallResult(
                query=query,
                context_summary=result.get("context_summary", ""),
                graph_paths=result.get("graph_paths", []),
                vector_matches=result.get("vector_matches", []),
            )
        except Exception as exc:
            logger.warning("hermes_recall.tool_failed", error=str(exc))
            return name, HermesRecallResult(
                query=query,
                context_summary=f"Recall failed: {exc}",
                success=False,
            )

    if name == "hermes_store":
        fact = tool_call.get("fact", "")
        if hermes_service is None:
            return name, HermesStoreResult(
                fact=fact,
                stored_neo4j=False,
                stored_qdrant=False,
                success=False,
            )
        try:
            result = await hermes_service.store(fact)
            return name, HermesStoreResult(
                fact=fact,
                stored_neo4j=result.get("stored_neo4j", False),
                stored_qdrant=result.get("stored_qdrant", False),
                fact_id=result.get("fact_id", ""),
            )
        except Exception as exc:
            logger.warning("hermes_store.tool_failed", error=str(exc))
            return name, HermesStoreResult(
                fact=fact,
                stored_neo4j=False,
                stored_qdrant=False,
                success=False,
            )

    if name == "hermes_learn":
        correction = tool_call.get("correction", "")
        if hermes_service is None:
            return name, HermesLearnResult(
                correction_text=correction,
                success=False,
            )
        try:
            result = await hermes_service.learn(correction)
            return name, HermesLearnResult(
                correction_text=correction,
                extracted_facts=result.get("extracted_facts", []),
                corrections_applied=result.get("corrections_applied", 0),
            )
        except Exception as exc:
            logger.warning("hermes_learn.tool_failed", error=str(exc))
            return name, HermesLearnResult(
                correction_text=correction,
                success=False,
            )

    if name == "reply_email":
        inbox_id = tool_call.get("inbox_id", "")
        message_id = tool_call.get("message_id", "")
        body = tool_call.get("body", "")
        # Native tool-calling path: tool args don't include message_id (the
        # inbound email's message_id is the one we reply to). Fall back to
        # state's email_content.message_id when not provided.
        if not message_id and state is not None:
            message_id = state["email_content"].message_id
        if not inbox_id:
            inbox_id = (
                get_settings().agentmail_inbox_id
                or os.environ.get("AGENTMAIL_INBOX_ID", "")
            )
        # Dedup is owned by ``_reply_email`` in email_tools.py (the actual
        # send boundary). ``sent_message_ids`` is passed through so the
        # email_tools layer can skip the duplicate send.
        result_str = await _reply_email(
            inbox_id=inbox_id,
            message_id=message_id,
            body=body,
            sent_message_ids=sent_message_ids,
        )
        success = "successfully" in result_str.lower()
        sent_msg_id = ""
        if "Message ID:" in result_str:
            sent_msg_id = result_str.split("Message ID:")[-1].strip()
        return name, EmailToolResult(
            message=result_str,
            message_id=sent_msg_id,
            success=success,
            body=body,
        )

    if name == "send_email":
        to = tool_call.get("to", "")
        subject = tool_call.get("subject", "")
        body = tool_call.get("body", "")
        message_id = tool_call.get("message_id", "")
        if not message_id and state is not None:
            message_id = state["email_content"].message_id
        # Dedup is owned by ``_send_email`` in email_tools.py (the actual
        # send boundary). ``sent_message_ids`` is passed through so the
        # email_tools layer can skip the duplicate send.
        result_str = await _send_email(
            to=to,
            subject=subject,
            body=body,
            sent_message_ids=sent_message_ids,
            message_id=message_id,
        )
        success = "successfully" in result_str.lower()
        sent_msg_id = ""
        if "Message ID:" in result_str:
            sent_msg_id = result_str.split("Message ID:")[-1].strip()
        return name, EmailToolResult(
            message=result_str,
            message_id=sent_msg_id,
            success=success,
        )

    # ── LDR deep research tool (REQ-310) ────────────────────────
    if name == "deep_research":
        query = tool_call.get("query", "")
        mode = tool_call.get("mode", "quick")
        if ldr_service is None:
            return name, LDRResult(
                query=query,
                summary="LDR service unavailable; research skipped.",
                success=False,
            )
        try:
            result = await ldr_service.research(query, mode=mode)
            return name, LDRResult(
                query=query,
                summary=result.get("summary", ""),
                findings=result.get("findings", []),
                sources=result.get("sources", []),
                mode=mode,
            )
        except Exception as exc:
            logger.warning("deep_research.tool_failed", error=str(exc))
            return name, LDRResult(
                query=query,
                summary=f"Research failed: {exc}",
                success=False,
            )

    # ── Document tools (REQ-311) ────────────────────────────────
    if name == "analyze_document":
        file_path = tool_call.get("file_path", "")
        if document_service is None:
            return name, DocumentAnalysisResult(
                file_path=file_path, success=False,
            )
        try:
            result = await document_service.analyze_document(file_path)
            if "error" in result:
                return name, DocumentAnalysisResult(
                    file_path=file_path, success=False,
                )
            quality = result.get("quality", {})
            return name, DocumentAnalysisResult(
                file_path=file_path,
                file_type=result.get("file_type", ""),
                page_count=result.get("page_count", 0),
                word_count=result.get("word_count", 0),
                text=result.get("text", "")[:2000],
                quality_level=quality.get("level", "unknown"),
                quality_score=quality.get("score", 0.0),
            )
        except Exception as exc:
            logger.warning("analyze_document.tool_failed", error=str(exc))
            return name, DocumentAnalysisResult(
                file_path=file_path, success=False,
            )

    if name == "create_report":
        title = tool_call.get("title", "Untitled Report")
        # Parse sections from a simple format: "Heading1::content1||Heading2::content2"
        sections_str = tool_call.get("sections", "")
        sections = []
        if sections_str:
            for part in sections_str.split("||"):
                if "::" in part:
                    heading, content = part.split("::", 1)
                    sections.append({"heading": heading.strip(), "content": content.strip()})
                else:
                    sections.append({"heading": "", "content": part.strip()})
        if not sections:
            sections = [{"heading": "Overview", "content": "No content provided."}]
        if document_service is None:
            return name, DocumentCreationResult(success=False, title=title)
        try:
            file_path = await document_service.create_report(title=title, sections=sections)
            return name, DocumentCreationResult(
                file_path=file_path, document_type="report", title=title,
            )
        except Exception as exc:
            logger.warning("create_report.tool_failed", error=str(exc))
            return name, DocumentCreationResult(success=False, title=title)

    # ── Agent delegation tools (REQ-312) ────────────────────────
    if name == "delegate_to_agent":
        agent_slug = tool_call.get("agent_slug", "") or tool_call.get("agent", "")
        task = tool_call.get("task", "")
        context = tool_call.get("context", "")
        region = tool_call.get("region", "")
        if agent_delegator is None:
            return name, AgentDelegationResult(
                agent_slug=agent_slug, success=False,
            )
        try:
            result = await agent_delegator.delegate(
                agent_slug, task, context=context,
                region=region or None,
            )
            return name, AgentDelegationResult(
                agent_slug=result.get("agent_slug", agent_slug),
                agent_name=result.get("agent_name", ""),
                response=result.get("response", ""),
                region=result.get("region", region),
                success=result.get("success", False),
            )
        except Exception as exc:
            logger.warning("delegate_to_agent.tool_failed: %s", exc)
            return name, AgentDelegationResult(
                agent_slug=agent_slug, success=False,
            )

    if name == "list_agents":
        division = tool_call.get("division", "")
        keyword = tool_call.get("keyword", "")
        region = tool_call.get("region", "")
        if agent_delegator is None:
            return name, AgentListingResult(success=False)
        try:
            agents = agent_delegator.list_agents(
                division=division or None,
                keyword=keyword or None,
                region=region or None,
            )
            return name, AgentListingResult(
                division=division,
                keyword=keyword,
                region=region,
                agents=agents,
            )
        except Exception as exc:
            logger.warning("list_agents.tool_failed: %s", exc)
            return name, AgentListingResult(success=False)

    # Unknown tool: return an error-typed result using CalculationResult base
    return name, CalculationResult(
        expression="unknown",
        value=0.0,
        success=False,
    )


def discard_node(state: AgentState) -> dict[str, Any]:
    """Discard spam emails with a logged final response."""
    return {
        "final_response": "Email classified as spam; no action taken.",
        "messages": [AIMessage(content="Discarded as spam.")],
    }


def correction_node(state: AgentState) -> dict[str, Any]:
    """Real correction node: accept user feedback and update correction memory.

    Accepts a correction message and stores it in Neo4j and memory_context.
    """
    logger.info("correction_node.start", email=state["email_content"].sender)
    correction_message = state.get("final_response", "")

    # Get existing corrections from memory_context
    memory_ctx = state.get("memory_context")
    corrections = []
    if isinstance(memory_ctx, MemoryContext):
        corrections = list(memory_ctx.corrections) if memory_ctx.corrections else []
    elif isinstance(memory_ctx, dict):
        corrections = list(memory_ctx.get("corrections", []))

    # Parse correction from message (expect JSON or structured format)
    try:
        corr_data = json.loads(correction_message)
    except (json.JSONDecodeError, TypeError):
        # Fallback: treat as plain text correction
        corr_data = {"correction": correction_message}

    # Build structured correction record matching CorrectionSummary schema
    correction_record = CorrectionSummary(
        correction_id=f"corr_{len(corrections) + 1}_{state['email_content'].message_id}",
        category=corr_data.get("category", "general"),
        rule=corr_data.get("rule", correction_message[:100]),
        confidence=0.9,  # User-provided corrections are high confidence
        applied_count=0,
    )

    # Update in-memory corrections list (keep last 50, most recent first)
    corrections.insert(0, correction_record)
    corrections = corrections[:50]

    # Persist to Neo4j (graceful degradation if unavailable)
    try:
        # Neo4j upsert runs synchronously here via asyncio.run in a thread
        import asyncio

        from praxis.services.neo4j_client import Neo4jContextClient
        loop = asyncio.new_event_loop()
        try:
            client = Neo4jContextClient()
            loop.run_until_complete(
                client.upsert_correction(
                    sender=state["email_content"].sender,
                    correction_id=correction_record.correction_id,
                    category=correction_record.category,
                    rule=correction_record.rule,
                    confidence=correction_record.confidence,
                )
            )
        except Exception as exc:
            logger.warning("correction_node.neo4j_failed", error=str(exc))
        finally:
            loop.close()
    except Exception as exc:
        logger.warning("correction_node.neo4j_failed", error=str(exc))

    # Update state with refreshed corrections
    memory_context = MemoryContext(corrections=corrections)
    if "memory_context" in state:
        state["memory_context"] = memory_context
    else:
        state["memory_context"] = memory_context

    logger.info("correction_node.complete", sender=state["email_content"].sender, correction_count=len(corrections))
    return {"memory_context": memory_context, "final_response": f"Correction recorded: {correction_message[:100]}"}


def correction_stub_node(state: AgentState) -> dict[str, Any]:
    """Placeholder correction handler (deprecated: use correction_node)."""
    return correction_node(state)


__all__ = [
    "context_loading_node",
    "correction_node",
    "correction_stub_node",
    "discard_node",
    "embedding_node",
    "react_node",
    "triage_node",
]
