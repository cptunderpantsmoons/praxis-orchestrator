"""Quick SSH connectivity test for the deploy target.

Reads SSH credentials from .env (gitignored, chmod 600). Never echoes them.
"""
import os
from pathlib import Path

import paramiko

ENV_FILE = Path("/workspace/Latest/.env")


def load_env() -> dict[str, str]:
    out = {}
    if not ENV_FILE.exists():
        return out
    for line in ENV_FILE.read_text().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            # Strip quotes if present
            v = v.strip().strip('"').strip("'")
            out[k.strip()] = v
    return out


def main() -> int:
    env = load_env()
    ssh_password = env.get("SSH_PASSWORD")
    ssh_host = env.get("SSH_HOST", "2.25.195.193")
    ssh_user = env.get("SSH_USER", "root")
    ssh_port = int(env.get("SSH_PORT", "22"))

    if not ssh_password:
        print("FAIL: SSH_PASSWORD not in .env")
        return 1

    print(f"target: {ssh_user}@{ssh_host}:{ssh_port}")

    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        c.connect(
            hostname=ssh_host,
            port=ssh_port,
            username=ssh_user,
            password=ssh_password,
            timeout=15,
            allow_agent=False,
            look_for_keys=False,
        )
        stdin, stdout, stderr = c.exec_command(
            "whoami && hostname && uname -a && cat /etc/os-release | head -2 && "
            "(which docker || echo NO_DOCKER) && (which docker-compose || echo NO_DC) && "
            "free -h | head -2 && df -h / | tail -1 && echo READY"
        )
        out = stdout.read().decode()
        err = stderr.read().decode()
        print("STDOUT:")
        print(out)
        if err:
            print("STDERR:")
            print(err)
        c.close()
        print("=== SSH OK ===")
        return 0
    except Exception as e:
        print(f"SSH FAIL: {type(e).__name__}: {e}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
