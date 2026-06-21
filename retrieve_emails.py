"""Retrieve emails from the AgentMail inbox to find user instructions."""
import asyncio
import os
from praxis.services.agentmail_v2 import get_agentmail_v2

async def main():
    client = get_agentmail_v2()
    inbox = os.environ.get("AGENTMAIL_INBOX_ID", "praxis2@agentmail.to")
    print(f"Listing messages from inbox: {inbox}")
    try:
        result = await client.list_messages(inbox, limit=20)
        # The SDK returns a ListMessagesResponse object
        if hasattr(result, "messages"):
            messages = result.messages
        elif hasattr(result, "items"):
            messages = result.items
        elif isinstance(result, list):
            messages = result
        else:
            # Try to find any list attribute
            print(f"  Response type: {type(result)}")
            print(f"  Attributes: {[a for a in dir(result) if not a.startswith('_')]}")
            for attr in dir(result):
                if not attr.startswith("_"):
                    val = getattr(result, attr)
                    if isinstance(val, list) and len(val) > 0:
                        print(f"  Found list at .{attr}: {len(val)} items")
                        messages = val
                        break
            else:
                messages = []
        
        print(f"Got {len(messages)} messages\n")
        for m in messages:
            msg = m if isinstance(m, dict) else (m.model_dump() if hasattr(m, "model_dump") else dict(m))
            sender = msg.get("from", {})
            if isinstance(sender, dict):
                sender = sender.get("email", str(sender))
            subject = msg.get("subject", "?")
            text = str(msg.get("text", msg.get("body", "")))
            direction = msg.get("direction", msg.get("type", "?"))
            msg_id = msg.get("id", msg.get("message_id", "?"))
            print(f"--- [{direction}] id={msg_id}")
            print(f"    from: {sender}")
            print(f"    subject: {subject}")
            print(f"    body: {text[:800]}")
            print()
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()

asyncio.run(main())
