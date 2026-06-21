"""Get full content of specific emails from AgentMail."""
import asyncio
import os
from praxis.services.agentmail_v2 import get_agentmail_v2

async def main():
    client = get_agentmail_v2()
    inbox = os.environ.get("AGENTMAIL_INBOX_ID", "praxis2@agentmail.to")
    
    # The IDs we want to read
    target_ids = [
        "CAOieQ7uqf8-Bq9wbjoAgWSs757n92Xtv04O-k=J+HtDGV9y1cQ@mail.gmail.com",
        "CAOieQ7tHY2pwriuoBQ-t7=B2bt9_TY9zdZ+temWCCg-ePxyPUA@mail.gmail.com",
        "CAOieQ7uGCLm=LbVxoyf3Rgx99mzFujaDM3VJjHQt367vzVM1bg@mail.gmail.com",
    ]
    
    for msg_id in target_ids:
        print(f"\n{'='*80}")
        print(f"Message ID: {msg_id}")
        try:
            msg = await client.get_message(inbox, msg_id)
            # Convert to dict
            d = msg if isinstance(msg, dict) else (msg.model_dump() if hasattr(msg, "model_dump") else dict(msg))
            
            sender = d.get("from", {})
            if isinstance(sender, dict):
                sender = sender.get("email", str(sender))
            
            print(f"From: {sender}")
            print(f"Subject: {d.get('subject', '?')}")
            print(f"Direction: {d.get('direction', d.get('type', '?'))}")
            
            # Try multiple body fields
            body = d.get("text", "") or d.get("body", "") or d.get("html", "") or ""
            print(f"Body ({len(body)} chars):")
            print(body[:3000])
            if len(body) > 3000:
                print(f"\n... ({len(body) - 3000} more chars)")
        except Exception as e:
            print(f"Error: {e}")

asyncio.run(main())
