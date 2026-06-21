"""Test LDR integration from inside praxis-app container."""
import httpx
import json
import sys

LDR_URL = "http://172.16.3.3:5001"
LDR_API_KEY = "praxis_ldr_2026"

try:
    # 1. Health check
    resp = httpx.get(f"{LDR_URL}/health", timeout=10.0)
    print(f"health: {resp.status_code} — {resp.text[:100]}")

    # 2. Quick research
    resp = httpx.post(
        f"{LDR_URL}/research",
        json={
            "query": "What is Bitcoin halving?",
            "api_key": LDR_API_KEY,
            "mode": "quick",
        },
        timeout=120.0,
    )
    print(f"\nresearch status: {resp.status_code}")
    data = resp.json()
    print(f"success: {data.get('success')}")

    r = data.get("result", {})
    if isinstance(r, dict):
        summary = r.get("summary", "")
        print(f"summary ({len(summary)} chars): {summary[:400]}")
        sources = r.get("sources", [])
        print(f"sources: {len(sources)}")
        for s in sources[:3]:
            if isinstance(s, dict):
                print(f"  - {s.get('title', 'untitled')[:80]}")
            else:
                print(f"  - {str(s)[:80]}")
        findings = r.get("findings", [])
        print(f"findings: {len(findings)}")
    else:
        print(f"result type: {type(r).__name__}")
        print(f"result: {str(r)[:300]}")

except Exception as e:
    print(f"ERROR: {type(e).__name__}: {e}", file=sys.stderr)
    sys.exit(1)
