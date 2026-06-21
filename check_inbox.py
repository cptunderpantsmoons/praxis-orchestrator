"""Check AgentMail inbox and Hindsight banks for the user's instructions."""
import asyncio
import json
import os
import urllib.request

from praxis.services.agentmail_v2 import get_agentmail_v2

HINDSIGHT_KEY = os.environ.get("HINDSIGHT_API_KEY", "")
BASE = "https://api.hindsight.vectorize.io"


async def main():
    # 1. List all Hindsight banks
    print("=== Hindsight banks ===")
    req = urllib.request.Request(
        f"{BASE}/v1/default/banks",
        headers={"Authorization": f"Bearer {HINDSIGHT_KEY}"},
    )
    try:
        with urllib.request.urlopen(req) as resp:
            banks = json.loads(resp.read().decode())
            if isinstance(banks, dict):
                banks = banks.get("items", banks.get("banks", [banks]))
            for b in banks:
                bank_id = b.get("bank_id", b.get("id", "?"))
                print(f"  bank: {bank_id}")
    except Exception as e:
        print(f"  Error: {e}")

    # 2. Check AgentMail inbox for past emails
    print("\n=== AgentMail inbox (praxis2@agentmail.to) ===")
    client = get_agentmail_v2()
    try:
        messages = await client.list_messages("praxis2@agentmail.to", limit=20)
        for m in messages:
            msg = m if isinstance(m, dict) else (m.model_dump() if hasattr(m, "model_dump") else dict(m))
            sender = msg.get("from", {})
            if isinstance(sender, dict):
                sender = sender.get("email", str(sender))
            subject = msg.get("subject", "?")
            text = str(msg.get("text", msg.get("body", "")))[:300]
            direction = msg.get("direction", msg.get("type", "?"))
            print(f"  [{direction}] from={sender}  subject={subject}")
            print(f"    preview: {text[:200]}")
            print()
    except Exception as e:
        print(f"  Error: {e}")


asyncio.run(main())
