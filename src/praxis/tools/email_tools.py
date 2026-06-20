"""Email tools for the ReAct agent.

Wraps AgentMailClient as LangChain tools so the agent can send emails
from its own dedicated inbox at AgentMail.to.

The agent does NOT access the user's mailbox — it has its own mailbox.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from langchain_core.tools import tool

if TYPE_CHECKING:
    pass

# Default inbox ID — provisioned via AgentMail console
# In production, load from config or database
DEFAULT_INBOX_ID = "ib_default_agent_inbox"


@tool
def send_email_tool(
    to: str,
    subject: str,
    body: str,
    inbox_id: str = DEFAULT_INBOX_ID,
) -> str:
    """Send an email from the agent's own mailbox.

    Use this tool to send a new email message to a recipient.
    The email is sent from the agent's dedicated AgentMail inbox.

    Args:
        to: Recipient email address (e.g., "user@example.com")
        subject: Email subject line
        body: Plain text email body
        inbox_id: Agent's inbox ID (defaults to configured inbox)

    Returns:
        Confirmation message with message ID on success, error message on failure.
    """
    import asyncio

    from praxis.services.agentmail_client import get_agentmail_client

    async def _send() -> str:
        client = get_agentmail_client()
        try:
            result = await client.send_message(
                inbox_id=inbox_id,
                to=to,
                subject=subject,
                body=body,
            )
            message_id = result.get("id", "unknown")
            return f"Email sent successfully. Message ID: {message_id}"
        except Exception as e:
            return f"Failed to send email: {e}"

    return asyncio.run(_send())


@tool
def reply_email_tool(
    message_id: str,
    body: str,
    reply_all: bool = False,
) -> str:
    """Reply to an existing email message.

    Use this tool to reply to a message that was received.
    The reply maintains the email thread and includes proper headers.

    Args:
        message_id: The message ID to reply to (from InboundEmail.message_id)
        body: Plain text reply body
        reply_all: Whether to reply to all recipients (default: False)

    Returns:
        Confirmation message with reply message ID on success, error message on failure.
    """
    import asyncio

    from praxis.services.agentmail_client import get_agentmail_client

    async def _reply() -> str:
        client = get_agentmail_client()
        try:
            result = await client.reply_to_message(
                message_id=message_id,
                body=body,
                reply_all=reply_all,
            )
            reply_id = result.get("id", "unknown")
            return f"Reply sent successfully. Message ID: {reply_id}"
        except Exception as e:
            return f"Failed to send reply: {e}"

    return asyncio.run(_reply())


# Export for tool registry
EMAIL_TOOLS = [send_email_tool, reply_email_tool]
