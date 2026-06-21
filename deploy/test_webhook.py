"""Upload new .env to server, restart app, send a test webhook."""
import paramiko
import time
import hmac
import hashlib
import base64
import httpx
from pathlib import Path

ENV_FILE = Path("/workspace/Latest/.env")


def load_env() -> dict[str, str]:
    out = {}
    for line in ENV_FILE.read_text().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            v = v.strip().strip('"').strip("'")
            out[k.strip()] = v
    return out


def main() -> int:
    env = load_env()
    pw = env["SSH_PASSWORD"]
    secret = env["AGENTMAIL_WEBHOOK_SECRET"]
    print(f"loaded secret: len={len(secret)} starts={secret[:8]!r}")

    # 1. Upload .env
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect("2.25.195.193", 22, "root", pw, timeout=10, allow_agent=False, look_for_keys=False)
    sftp = c.open_sftp()
    sftp.put(str(ENV_FILE), "/opt/praxis/repo/.env")
    sftp.close()
    print("[1/3] .env uploaded")

    # 2. Restart app
    stdin, stdout, _ = c.exec_command(
        "cd /opt/praxis/repo && docker compose restart app 2>&1 | tail -3",
        timeout=30,
    )
    print(f"[2/3] {stdout.read().decode()}")
    time.sleep(8)

    # 3. Send a test webhook from the local machine
    secret_b64 = secret.replace("whsec_", "")
    # Pad base64 if needed
    padding = (4 - len(secret_b64) % 4) % 4
    secret_b64 += "=" * padding
    try:
        secret_bytes = base64.b64decode(secret_b64)
    except Exception as e:
        print(f"  base64 decode failed: {e}")
        # Fallback: use raw secret bytes
        secret_bytes = secret.replace("whsec_", "").encode()

    ts = str(int(time.time()))
    msg_id = "evt_local_test_1"
    body = (
        '{"type":"event","event_type":"message.received","event_id":"evt_local_test_1",'
        '"message":{"id":"m_local","from":"alice@example.com",'
        '"to":["praxis2@agentmail.to"],"subject":"local smoke","body":"smoke body"},'
        '"thread":{"thread_id":"t_local"}}'
    )
    signed = f"{msg_id}.{ts}.{body}"
    sig = "v1," + base64.b64encode(
        hmac.new(secret_bytes, signed.encode(), hashlib.sha256).digest()
    ).decode()

    print(f"[3/3] Sending test webhook to https://praxis.2.25.195.193.sslip.io/webhook/email")
    r = httpx.post(
        "https://praxis.2.25.195.193.sslip.io/webhook/email",
        content=body,
        headers={
            "Content-Type": "application/json",
            "svix-id": msg_id,
            "svix-timestamp": ts,
            "svix-signature": sig,
        },
        timeout=15,
    )
    print(f"  HTTP {r.status_code}: {r.text[:200]}")

    # Check the app log to see what happened
    time.sleep(2)
    stdin, stdout, _ = c.exec_command(
        "docker logs praxis-app --tail 5 2>&1", timeout=10
    )
    print("\n=== App log (last 5 lines) ===")
    print(stdout.read().decode())

    c.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
