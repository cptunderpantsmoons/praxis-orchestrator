#!/usr/bin/env python3
"""Validate all production secrets by making real API calls.

Reads from .env (never echoes the values), tests each provider, and reports
which keys are valid, which services are reachable, and what the deployment
surface looks like.

Usage:
    venv/bin/python scripts/validate_secrets.py
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import httpx

# Load .env BEFORE importing any praxis modules
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")


def mask(value: str | None) -> str:
    """Return a masked version of a secret — never the full value."""
    if not value:
        return "(missing)"
    if len(value) <= 12:
        return "***"
    return f"{value[:6]}***{value[-4:]}"


async def test_umans() -> bool:
    print("\n[1/4] UMANS (inference API)")
    key = os.environ.get("UMANS_API_KEY")
    print(f"  key: {mask(key)}")
    if not key:
        print("  SKIP: UMANS_API_KEY not set")
        return False
    base = os.environ.get("UMANS_BASE_URL", "https://api.code.umans.ai/v1").rstrip("/")
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(f"{base}/models", headers=headers)
            if r.status_code == 200:
                data = r.json()
                models = data.get("data", data) if isinstance(data, dict) else data
                if isinstance(models, list):
                    print(f"  OK: {len(models)} models available")
                    for m in models[:7]:
                        mid = m.get("id", "?") if isinstance(m, dict) else m
                        print(f"    - {mid}")
                return True
            else:
                print(f"  FAIL: HTTP {r.status_code} {r.text[:200]}")
                return False
    except Exception as e:
        print(f"  FAIL: {type(e).__name__}: {e}")
        return False


async def test_agentmail() -> bool:
    print("\n[2/4] AGENTMAIL (email provider)")
    key = os.environ.get("AGENTMAIL_API_KEY")
    inbox = os.environ.get("AGENTMAIL_INBOX_ID")
    print(f"  key:  {mask(key)}")
    print(f"  inbox: {inbox}")
    if not key:
        print("  SKIP: AGENTMAIL_API_KEY not set")
        return False
    try:
        import agentmail
        sdk = agentmail.AgentMail(api_key=key)
        result = sdk.inboxes.list()
        inboxes = getattr(result, "inboxes", None) or getattr(result, "items", None) or []
        print(f"  OK: {len(inboxes)} inbox(es) in account")
        for ib in inboxes[:5]:
            ib_id = getattr(ib, "inbox_id", ib)
            ib_addr = getattr(ib, "address", "?")
            print(f"    - {ib_id}  ({ib_addr})")
        if inbox and any(getattr(ib, "inbox_id", ib) == inbox for ib in inboxes):
            print(f"  OK: configured inbox {inbox} exists")
        elif inbox:
            print(f"  WARN: configured inbox {inbox} NOT found in account")
        return True
    except Exception as e:
        print(f"  FAIL: {type(e).__name__}: {e}")
        return False


async def test_github() -> bool:
    print("\n[3/4] GITHUB (deployment / source control)")
    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPO")
    print(f"  token: {mask(token)}")
    print(f"  repo:  {repo}")
    if not token:
        print("  SKIP: GITHUB_TOKEN not set")
        return False
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                "https://api.github.com/user",
                headers={"Authorization": f"Bearer {token}"},
            )
            if r.status_code == 200:
                u = r.json()
                print(f"  OK: user={u.get('login')!r}  name={u.get('name')!r}")
            else:
                print(f"  FAIL: HTTP {r.status_code} {r.text[:200]}")
                return False
            if repo:
                owner, name = repo.split("/", 1)
                r = await client.get(
                    f"https://api.github.com/repos/{owner}/{name}",
                    headers={"Authorization": f"Bearer {token}"},
                )
                if r.status_code == 200:
                    repo_data = r.json()
                    print(f"  OK: repo {repo} exists (private={repo_data.get('private')})")
                elif r.status_code == 404:
                    print(f"  WARN: repo {repo} does NOT exist — needs to be created")
                else:
                    print(f"  FAIL: repo check HTTP {r.status_code}")
            return True
    except Exception as e:
        print(f"  FAIL: {type(e).__name__}: {e}")
        return False


async def test_composio() -> bool:
    print("\n[4/4] COMPOSIO (hostinger integration)")
    key = os.environ.get("COMPOSIO_API_KEY")
    print(f"  key: {mask(key)}")
    if not key:
        print("  SKIP: COMPOSIO_API_KEY not set")
        return False
    # Composio v3 endpoint
    headers = {"x-api-key": key, "Content-Type": "application/json"}
    for url in [
        "https://backend.composio.dev/api/v3/apps",
        "https://backend.composio.dev/api/v1/auth/whoami",
    ]:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(url, headers=headers)
                if r.status_code == 200:
                    data = r.json()
                    if isinstance(data, list):
                        print(f"  OK ({url}): {len(data)} apps")
                    elif isinstance(data, dict):
                        print(f"  OK ({url}): {list(data.keys())[:6]}")
                    return True
                else:
                    print(f"  {r.status_code} {url}")
        except Exception as e:
            print(f"  FAIL: {type(e).__name__}: {e}")
    print("  FAIL: no working Composio endpoint found")
    return False


async def main() -> int:
    print("=" * 70)
    print("PRAXIS Production Secrets Validation")
    print("=" * 70)
    results = await asyncio.gather(
        test_umans(), test_agentmail(), test_github(), test_composio()
    )
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    labels = ["UMANS", "AGENTMAIL", "GITHUB", "COMPOSIO"]
    for label, ok in zip(labels, results):
        print(f"  {label:12s} {'OK' if ok else 'FAIL'}")
    if all(results):
        print("\nAll keys valid. Ready to deploy.")
        return 0
    print("\nOne or more keys failed validation. Fix before deploying.")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
