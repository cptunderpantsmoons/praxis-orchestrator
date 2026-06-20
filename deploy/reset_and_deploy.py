"""Hostinger API: reset root password, verify SSH, save to .env, run deploy."""
import os
import sys
import time
import string
import secrets
import httpx
import paramiko
from pathlib import Path

ENV_FILE = Path("/workspace/Latest/.env")

# Load .env
env = {}
for line in ENV_FILE.read_text().splitlines():
    if "=" in line and not line.strip().startswith("#"):
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()

hostinger_key = env.get("HOSTINGER_API_KEY")
if not hostinger_key:
    print("FAIL: HOSTINGER_API_KEY missing")
    sys.exit(1)

VM_ID = 1603169
VM_IP = "2.25.195.193"

base = "https://developers.hostinger.com"
headers = {
    "Authorization": f"Bearer {hostinger_key}",
    "Content-Type": "application/json",
    "Accept": "application/json",
}

# ── 1. Generate strong password ───────────────────────────
# Hostinger requires at least one symbol from: -().&@?'#;/,+
required_symbols = "-().&@?'#;/,+"
required_lower = string.ascii_lowercase
required_upper = string.ascii_uppercase
required_digit = string.digits
# Ensure at least one of each
new_pw = [
    secrets.choice(required_lower),
    secrets.choice(required_upper),
    secrets.choice(required_digit),
    secrets.choice(required_symbols),
]
# Fill the rest
all_chars = required_lower + required_upper + required_digit + required_symbols
for _ in range(16):
    new_pw.append(secrets.choice(all_chars))
secrets.SystemRandom().shuffle(new_pw)
new_pw = ''.join(new_pw)
print(f"[1/5] Generated new root password ({len(new_pw)} chars)")

# ── 2. Reset via Hostinger API ─────────────────────────────
print(f"[2/5] PUT /api/vps/v1/virtual-machines/{VM_ID}/root-password")
with httpx.Client(timeout=30) as client:
    r = client.put(
        f"{base}/api/vps/v1/virtual-machines/{VM_ID}/root-password",
        headers=headers,
        json={"password": new_pw},
    )
    print(f"  status: {r.status_code}")
    print(f"  body: {r.text[:300]}")
    if r.status_code not in (200, 202, 204):
        print("  FAIL: password reset failed")
        sys.exit(1)
    print("  OK")

# ── 3. Wait for propagation ───────────────────────────────
print("[3/5] Waiting 30s for password to propagate...")
time.sleep(30)

# ── 4. Test SSH ───────────────────────────────────────────
print(f"[4/5] SSH to root@{VM_IP}")
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
try:
    c.connect(VM_IP, 22, "root", new_pw, timeout=15, allow_agent=False, look_for_keys=False, banner_timeout=10)
    stdin, stdout, _ = c.exec_command("whoami && hostname && uname -a && cat /etc/os-release | head -2")
    print("STDOUT:")
    print(stdout.read().decode())
    stdin, stdout, _ = c.exec_command("which docker || echo NO_DOCKER; which docker-compose || echo NO_DC")
    print("Docker check:")
    print(stdout.read().decode())
    c.close()
    print("=== SSH OK ===")
except Exception as e:
    print(f"  FAIL: {type(e).__name__}: {e}")
    sys.exit(1)

# ── 5. Save password to .env ──────────────────────────────
print("[5/5] Saving new password to .env")
content = ENV_FILE.read_text()
new_pw_escaped = new_pw.replace('\\', '\\\\').replace('"', '\\"')
lines = []
for line in content.splitlines():
    if line.startswith("SSH_PASSWORD="):
        lines.append(f'SSH_PASSWORD="{new_pw_escaped}"')
    else:
        lines.append(line)
ENV_FILE.write_text("\n".join(lines) + "\n")
os.chmod(ENV_FILE, 0o600)
print(f"  .env updated, perms={oct(os.stat(ENV_FILE).st_mode)}")
print()
print("=" * 60)
print("  READY TO DEPLOY")
print("=" * 60)
print(f"  VM: root@{VM_IP}:22")
print("Next: python deploy/deploy.py")
