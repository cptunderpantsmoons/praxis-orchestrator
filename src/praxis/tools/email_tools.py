"""LangChain email tools for the ReAct agent.

Wraps ``AgentMailV2`` as LangChain tools so the agent can send, reply, search,
and label emails from its own dedicated inbox at AgentMail.to.

The agent does NOT access the user's mailbox — it has its own mailbox.

These tools are **async** (using ``StructuredTool.from_function(coroutine=...)``)
so they integrate cleanly with the rest of the async LangGraph pipeline. The
underlying ``AgentMailV2`` wrapper bridges to the sync ``agentmail`` SDK via
``asyncio.to_thread``.
"""

from __future__ import annotations

from langchain_core.tools import StructuredTool

from praxis.services.agentmail_v2 import get_agentmail_v2

# Default inbox ID — provisioned via AgentMail console
# In production, load from config or database
DEFAULT_INBOX_ID = "ib_default_agent_inbox"


async def _send_email(
    to: str,
    subject: str,
    body: str,
    inbox_id: str = DEFAULT_INBOX_ID,
) -> str:
    """Send a new email from the agent's mailbox.

    Use this tool to send a new email message. The email is sent from the
    agent's dedicated AgentMail inbox.
    """
    client = get_agentmail_v2()
    try:
        result = await client.send_message(
            inbox_id=inbox_id, to=to, subject=subject, body_text=body
        )
        msg_id = getattr(result, "message_id", None) or "unknown"
        return f"Email sent successfully. Message ID: {msg_id}"
    except Exception as e:
        return f"Failed to send email: {e}"


async def _reply_email(
    inbox_id: str,
    message_id: str,
    body: str,
) -> str:
    """Reply to an existing email in its original thread.

    Args:
        inbox_id: The agent's inbox ID (e.g. ``ib_abc123``).
        message_id: The message ID to reply to (from ``InboundEmail.message_id``).
        body: Plain-text reply body.
    """
    client = get_agentmail_v2()
    try:
        result = await client.reply_to_message(
            inbox_id=inbox_id, message_id=message_id, body=body
        )
        msg_id = getattr(result, "message_id", None) or "unknown"
        return f"Reply sent successfully. Message ID: {msg_id}"
    except Exception as e:
        return f"Failed to send reply: {e}"


async def _search_inbox(
    inbox_id: str,
    query: str,
    limit: int = 5,
) -> str:
    """Full-text search the agent's inbox for a query string.

    Returns a list of matching messages with subject and sender.
    """
    client = get_agentmail_v2()
    try:
        results = await client.search_messages(inbox_id=inbox_id, q=query, limit=limit)
        lines = [f"Found {len(results)} matching messages:"]
        for msg in results[:limit]:
            mid = getattr(msg, "message_id", "?")
            subj = getattr(msg, "subject", "(no subject)")
            sender = getattr(msg, "from_", None) or getattr(msg, "from", "?")
            lines.append(f"  - {mid}  from={sender}  subject={subj!r}")
        return "\n".join(lines) if lines else "No matches found."
    except Exception as e:
        return f"Search failed: {e}"


async def _list_threads(
    inbox_id: str,
    limit: int = 10,
) -> str:
    """List recent email threads in the agent's inbox.

    Useful for the agent to recall prior context with a sender.
    """
    client = get_agentmail_v2()
    try:
        threads = await client.list_threads(inbox_id=inbox_id, limit=limit)
        lines = [f"Recent {len(threads)} threads:"]
        for t in threads[:limit]:
            tid = getattr(t, "thread_id", "?")
            subject = getattr(t, "subject", "(no subject)")
            sender = getattr(t, "from_", None) or getattr(t, "from", "?")
            count = getattr(t, "message_count", "?")
            lines.append(f"  - {tid}  from={sender}  subject={subject!r}  msgs={count}")
        return "\n".join(lines) if lines else "No threads."
    except Exception as e:
        return f"List failed: {e}"


# ── Build async StructuredTools for LangGraph integration ─────

send_email_tool = StructuredTool.from_function(
    coroutine=_send_email,
    name="send_email",
    description=(
        "Send a new email from the agent's own mailbox. "
        "Args: to (recipient address), subject, body, inbox_id (optional). "
        "Returns the message ID on success."
    ),
)

reply_email_tool = StructuredTool.from_function(
    coroutine=_reply_email,
    name="reply_email",
    description=(
        "Reply to an existing email message in its original thread. "
        "Args: inbox_id, message_id, body. Returns the reply message ID on success."
    ),
)

search_inbox_tool = StructuredTool.from_function(
    coroutine=_search_inbox,
    name="search_inbox",
    description=(
        "Full-text search the agent's inbox. Args: inbox_id, query, limit (optional). "
        "Returns a list of matching messages."
    ),
)

list_threads_tool = StructuredTool.from_function(
    coroutine=_list_threads,
    name="list_threads",
    description=(
        "List recent email threads in the agent's inbox. "
        "Args: inbox_id, limit (optional). Returns thread summaries."
    ),
)


# Export for tool registry
EMAIL_TOOLS = [send_email_tool, reply_email_tool, search_inbox_tool, list_threads_tool]
