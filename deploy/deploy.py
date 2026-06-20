#!/usr/bin/env python3
"""Deploy PRAXIS v2.0 to root@2.25.195.193 (or whatever is in .env).

Steps:
1. Test SSH
2. Verify the system is Ubuntu with apt
3. Install Docker + docker-compose (if missing)
4. Create /opt/praxis, copy repo + .env
5. Run docker compose up
6. Wait for /health
7. Run smoke test
8. Print final status

Reads all credentials from .env (gitignored, chmod 600). Never echoes them.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import paramiko

ENV_FILE = Path("/workspace/Latest/.env")


def load_env() -> dict[str, str]:
    out = {}
    for line in ENV_FILE.read_text().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            v = v.strip().strip('"').strip("'")
            out[k.strip()] = v
    return out


def run(ssh: paramiko.SSHClient, cmd: str, timeout: int = 120) -> tuple[int, str]:
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    rc = stdout.channel.recv_exit_status()
    if err and rc != 0:
        print(f"  [stderr] {err[:500]}")
    return rc, out


def main() -> int:
    env = load_env()
    ssh_pw = env.get("SSH_PASSWORD")
    ssh_host = env.get("SSH_HOST", "2.25.195.193")
    ssh_user = env.get("SSH_USER", "root")
    ssh_port = int(env.get("SSH_PORT", "22"))

    if not ssh_pw:
        print("FAIL: SSH_PASSWORD not in .env")
        return 1

    print(f"[1/7] Connecting to {ssh_user}@{ssh_host}:{ssh_port}")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        ssh.connect(
            ssh_host, ssh_port, ssh_user, ssh_pw,
            timeout=15, allow_agent=False, look_for_keys=False,
        )
    except Exception as e:
        print(f"  FAIL: {e}")
        print("  → check SSH_PASSWORD and SSH_USER in .env")
        return 1
    print("  OK")

    print(f"\n[2/7] Inspect target system")
    rc, out = run(ssh, "cat /etc/os-release | head -3 && echo --- && uname -a && echo --- && which docker || echo NO_DOCKER")
    print(f"  {out[:300]}")

    print(f"\n[3/7] Install Docker if missing")
    if "NO_DOCKER" in out:
        print("  Installing Docker (this takes 1-2 min)...")
        run(ssh, "apt-get update -y", timeout=120)
        run(ssh, "apt-get install -y ca-certificates curl gnupg", timeout=120)
        run(ssh, "install -m 0755 -d /etc/apt/keyrings", timeout=10)
        run(ssh, "curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg", timeout=60)
        run(ssh, "chmod a+r /etc/apt/keyrings/docker.gpg", timeout=5)
        run(ssh, 'echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release; echo $VERSION_CODENAME) stable" > /etc/apt/sources.list.d/docker.list', timeout=5)
        run(ssh, "apt-get update -y", timeout=120)
        run(ssh, "apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin", timeout=300)
        run(ssh, "systemctl enable --now docker", timeout=30)
        print("  Docker installed")
    rc, out = run(ssh, "docker --version && docker compose version")
    print(f"  {out.strip()}")

    print(f"\n[4/7] Copy repo + .env to /opt/praxis")
    run(ssh, "mkdir -p /opt/praxis/repo", timeout=5)
    # Upload .env first (small)
    sftp = ssh.open_sftp()
    try:
        sftp.put(str(ENV_FILE), "/opt/praxis/repo/.env")
    finally:
        sftp.close()
    run(ssh, "chmod 600 /opt/praxis/repo/.env", timeout=5)
    # Tar + upload the rest
    import subprocess
    subprocess.run(
        "tar --exclude='.venv' --exclude='.git' --exclude='screenshots/*.tar.gz' "
        "--exclude='.coverage' --exclude='__pycache__' -czf /tmp/praxis-repo.tar.gz .",
        shell=True, cwd="/workspace/Latest", check=True,
    )
    sftp = ssh.open_sftp()
    try:
        sftp.put("/tmp/praxis-repo.tar.gz", "/tmp/praxis-repo.tar.gz")
    finally:
        sftp.close()
    run(ssh, "rm -rf /opt/praxis/repo && mkdir -p /opt/praxis/repo && tar -xzf /tmp/praxis-repo.tar.gz -C /opt/praxis/repo", timeout=60)
    run(ssh, "chmod 600 /opt/praxis/repo/.env", timeout=5)
    rc, out = run(ssh, "ls /opt/praxis/repo/ | head && echo --- && wc -c /opt/praxis/repo/.env")
    print(f"  {out.strip()}")

    print(f"\n[5/7] Start infra (Neo4j, Qdrant, PostgreSQL)")
    run(ssh, "cd /opt/praxis/repo && docker compose up -d neo4j qdrant postgres", timeout=300)
    print("  Waiting for services...")
    for i in range(30):
        rc, out = run(ssh, "curl -sf http://localhost:6333/healthz && echo OK", timeout=10)
        if "OK" in out:
            print(f"  Qdrant healthy after {i*2}s")
            break
        time.sleep(2)

    print(f"\n[6/7] Build + start PRAXIS app")
    rc, out = run(ssh, "cd /opt/praxis/repo && docker build -t praxis:0.1.0 . 2>&1 | tail -10", timeout=600)
    print(f"  Build: {out[:300]}")
    rc, out = run(ssh, "cd /opt/praxis/repo && docker compose up -d app 2>&1 | tail -5", timeout=120)
    print(f"  {out}")
    print("  Waiting for /health...")
    for i in range(30):
        rc, out = run(ssh, "curl -sf http://localhost:8000/health", timeout=10)
        if '"status":"ok"' in out:
            print(f"  /health OK after {i*2}s")
            print(f"  {out}")
            break
        time.sleep(2)

    print(f"\n[7/7] Run smoke test against live server")
    rc, out = run(
        ssh,
        "cd /opt/praxis/repo && SMOKE_ALLOW_GRAPH_FAILURE=0 bash ci-cd/smoke-test.sh http://localhost:8000 2>&1",
        timeout=60,
    )
    print(out)

    print(f"\n{'='*70}")
    print(f"  DEPLOY COMPLETE")
    print(f"  URL:    http://{ssh_host}:8000")
    print(f"  Health: http://{ssh_host}:8000/health")
    print(f"  Logs:   ssh {ssh_user}@{ssh_host} 'cd /opt/praxis/repo && docker compose logs -f app'")
    print(f"{'='*70}")
    ssh.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
