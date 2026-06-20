"""
Register the AgentMail webhook to point at the live PRAXIS server.

PREREQUISITES (one-time, on the PC):
    pip install agentmail

USAGE:
    python register_webhook.py
    (then paste the API key when prompted)

WHAT IT DOES:
    1. Connects to AgentMail with the API key
    2. Verifies the inbox "ib_praxis2" exists
    3. Lists any existing webhooks (so we don't double-register)
    4. Registers a new webhook pointing at http://2.25.195.193:8000/webhook/email
       for all 6 standard event types
    5. Verifies the registration was accepted
    6. Prints a summary + what to do next

After running this, send a test email to praxis2@agentmail.to and
the agent will receive it.
"""
import getpass
import sys
import agentmail

# The 6 event types we want to subscribe to
EVENTS = [
    "message.received",   # new inbound email (the one we care about)
    "message.sent",       # our outbound email accepted
    "message.delivered",  # recipient server accepted it
    "message.bounced",    # delivery failed
    "message.complained", # recipient marked as spam
    "message.rejected",   # rejected before send (policy)
]

# Your live PRAXIS server (already deployed on Hostinger VPS)
PRAXIS_URL = "http://2.25.195.193:8000/webhook/email"
PRAXIS_INBOX = "ib_praxis2"


def main() -> int:
    print("=" * 70)
    print("  PRAXIS Webhook Registration")
    print("=" * 70)
    print()
    print(f"This will register a webhook pointing at:")
    print(f"  {PRAXIS_URL}")
    print(f"for inbox: {PRAXIS_INBOX}")
    print(f"subscribed to: {len(EVENTS)} event types")
    print()
    api_key = getpass.getpass("Paste your AGENTMAIL_API_KEY (starts with am_us_): ").strip()
    if not api_key:
        print("FAIL: empty API key")
        return 1
    if not api_key.startswith("am_us_"):
        print(f"WARN: key doesn't start with 'am_us_', got: {api_key[:8]}...")

    print("\n[1/5] Connecting to AgentMail...")
    sdk = agentmail.AgentMail(api_key=api_key)

    print("\n[2/5] Listing existing inboxes...")
    result = sdk.inboxes.list()
    inboxes = getattr(result, "inboxes", None) or getattr(result, "items", None) or []
    print(f"  found {len(inboxes)} inbox(es):")
    for ib in inboxes:
        ib_id = getattr(ib, "inbox_id", ib)
        ib_addr = getattr(ib, "address", "?")
        print(f"    - {ib_id}  ({ib_addr})")
    target = next((ib for ib in inboxes if getattr(ib, "inbox_id", ib) == PRAXIS_INBOX), None)
    if target:
        print(f"  OK: inbox {PRAXIS_INBOX} exists")
    else:
        print(f"  WARN: inbox {PRAXIS_INBOX} not found in account, will still try to register")

    print("\n[3/5] Listing existing webhooks (to avoid duplicates)...")
    existing = sdk.webhooks.list()
    print(f"  found {len(existing)} existing webhook(s)")
    for h in existing:
        h_id = getattr(h, "webhook_id", "?")
        h_url = getattr(h, "url", "?")
        h_events = getattr(h, "events", [])
        # Delete any existing webhook for the same URL
        if PRAXIS_URL in str(h_url):
            print(f"    deleting duplicate {h_id} for {h_url}")
            sdk.webhooks.delete(h_id)
    # Refresh list after deletes
    existing = sdk.webhooks.list()
    print(f"  after cleanup: {len(existing)} existing webhook(s)")

    print(f"\n[4/5] Registering webhook...")
    result = sdk.webhooks.create(
        url=PRAXIS_URL,
        events=EVENTS,
        inbox_id=PRAXIS_INBOX,
        enabled=True,
    )
    webhook_id = getattr(result, "webhook_id", "?")
    secret = getattr(result, "secret", None)  # if AgentMail returns a signing secret
    print(f"  ✓ registered: webhook_id={webhook_id}")
    if secret:
        print(f"  secret (save this!): {secret}")
    else:
        print(f"  (AgentMail uses the inbox webhook secret; no new secret returned)")

    print(f"\n[5/5] Verifying registration...")
    final = sdk.webhooks.list()
    found = False
    for h in final:
        if getattr(h, "webhook_id", "?") == webhook_id:
            found = True
            print(f"  ✓ confirmed in webhook list")
            print(f"    url: {getattr(h, 'url', '?')}")
            print(f"    events: {getattr(h, 'events', [])}")
            print(f"    enabled: {getattr(h, 'enabled', '?')}")
            break
    if not found:
        print("  FAIL: webhook not found after registration")
        return 1

    print()
    print("=" * 70)
    print("  ✅ WEBHOOK REGISTERED")
    print("=" * 70)
    print()
    print("  WHAT TO DO NEXT:")
    print()
    print("  1. Open port 8000 in the Hostinger firewall (if not already open)")
    print("     - Log in to hpanel.hostinger.com")
    print("     - VPS -> your server -> Firewall -> Add rule")
    print("     - Protocol: TCP, Port: 8000, Source: 0.0.0.0/0")
    print()
    print("  2. Send a test email to:")
    print(f"     To: praxis2@agentmail.to")
    print(f"     Subject: Hello PRAXIS")
    print(f"     Body: This is a test email. Please acknowledge receipt.")
    print()
    print("  3. Within 5 seconds, the email should:")
    print("     a) Hit AgentMail's SMTP server")
    print("     b) Trigger a webhook POST to http://2.25.195.193:8000/webhook/email")
    print("     c) Be verified via Svix HMAC (signed by AgentMail)")
    print("     d) Invoke the LangGraph (triage -> context -> ReAct)")
    print("     e) The agent should send an auto-reply")
    print()
    print("  4. To watch what happens live:")
    print("     ssh root@2.25.195.193 'cd /opt/praxis/repo && docker compose logs -f app'")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
