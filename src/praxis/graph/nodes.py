"""LangGraph nodes for Phase 2: triage, context loading, and ReAct reasoning."""

from __future__ import annotations

import asyncio
import json
import re
from datetime import UTC, datetime
from typing import Any

import structlog
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig

from praxis.chat.wrappers import UmansChatModel
from praxis.models.schemas import (
    AgentMetadata,
    CalculationResult,
    CorrectionSummary,
    EmailTriage,
    Intent,
    MemoryContext,
    Priority,
)
from praxis.router import UmansConcurrencyRouter
from praxis.services.neo4j_client import Neo4jContextClient
from praxis.services.qdrant_client import QdrantSenderClient
from praxis.tools.stub_tools import dummy_calculator, dummy_search

from .state import AgentState

logger = structlog.get_logger()

# Model assignment per the DRD:
#   Triage        -> Qwen   (umans-flash)
#   ReAct         -> Kimi   (umans-coder)
#   Memory/Learn  -> GLM    (umans-glm-5.2) - used later
TRIAGE_MODEL = "umans-flash"
REACT_MODEL = "umans-coder"


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

    memory = MemoryContext(sender_history=history, corrections=corrections)
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


async def react_node(
    state: AgentState,
    config: RunnableConfig | None = None,
    *,
    max_iterations: int = 3,
) -> dict[str, Any]:
    """Run a custom ReAct reasoning loop using Kimi (umans-coder).

    The node receives an email and memory context, builds a system prompt, and
    iteratively asks the model whether to call a tool. Dummy tools return
    strictly typed Pydantic models (never raw strings).
    """
    email = state["email_content"]
    triage = state.get("triage_result")
    memory = state.get("memory_context") or MemoryContext()

    metadata = state.get("metadata") or AgentMetadata()
    if metadata.started_at is None:
        metadata.started_at = datetime.now(UTC)

    configurable = {} if config is None else (config.get("configurable") or {})
    router = configurable.get("router")

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

    # P3-F: Prepend top-N corrections to system prompt
    corrections_text = _build_correction_section(memory.corrections, email.sender)

    system_prompt = (
        "You are PRAXIS, an enterprise email assistant.\n"
        "You have access to these tools:\n"
        "- dummy_search(query: str)\n"
        "- dummy_calculator(expression: str)\n"
        "When you need information, output exactly one of:\n"
        "  TOOL:dummy_search(query=<query>)\n"
        "  TOOL:dummy_calculator(expression=<expr>)\n"
        "When you have enough information, output:\n"
        "  FINAL:<your concise response>\n"
        "Do not explain your reasoning; only output the action lines."
        + corrections_text
    )

    user_prompt = "\n".join(
        [
            "EMAIL:",
            f"From: {email.sender}",
            f"Subject: {email.subject}",
            f"Body:\n{email.body}",
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
                tool_name, tool_result = await _execute_tool(tool_call)
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
) -> tuple[str, Any]:
    """Execute a stub tool and return (tool_name, typed_output)."""
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
