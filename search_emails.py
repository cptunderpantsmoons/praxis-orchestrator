"""Search AgentMail inbox for the capabilities overview email."""
import asyncio
import os
import json
from praxis.services.agentmail_v2 import get_agentmail_v2

async def main():
    client = get_agentmail_v2()
    inbox = os.environ.get("AGENTMAIL_INBOX_ID", "praxis2@agentmail.to")
    
    # Try searching for the capabilities email
    for query in ["capabilities overview", "intro", "what you should"]:
        print(f"\n{'='*80}")
        print(f"Searching for: '{query}'")
        try:
            result = await client.search_messages(inbox, query=query, limit=5)
            # Handle response object
            if hasattr(result, "messages"):
                messages = result.messages
            elif hasattr(result, "items"):
                messages = result.items
            elif isinstance(result, list):
                messages = result
            else:
                print(f"  Response type: {type(result)}")
                attrs = [a for a in dir(result) if not a.startswith("_")]
                print(f"  Attributes: {attrs}")
                messages = []
                for attr in attrs:
                    val = getattr(result, attr)
                    if isinstance(val, list) and len(val) > 0:
                        messages = val
                        break
            
            print(f"  Found {len(messages)} messages")
            for m in messages:
                d = m if isinstance(m, dict) else (m.model_dump() if hasattr(m, "model_dump") else dict(m))
                sender = d.get("from", {})
                if isinstance(sender, dict):
                    sender = sender.get("email", str(sender))
                print(f"\n  --- subject: {d.get('subject', '?')}")
                print(f"      from: {sender}")
                print(f"      id: {d.get('id', d.get('message_id', '?'))}")
                body = d.get("text", "") or d.get("body", "") or ""
                print(f"      body ({len(body)} chars): {body[:1500]}")
        except Exception as e:
            print(f"  Error: {e}")

asyncio.run(main())
