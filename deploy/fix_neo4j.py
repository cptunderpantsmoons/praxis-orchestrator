"""Fix Neo4j password on the live server, restart, verify."""
import os
import sys
import time
import paramiko
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
    ssh_pw = env["SSH_PASSWORD"]
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect("2.25.195.193", 22, "root", ssh_pw, timeout=15, allow_agent=False, look_for_keys=False)
    print("=== Connected to server ===")

    # Get current neo4j password from compose
    print("\n[1/4] Get current Neo4j password from .env")
    stdin, stdout, _ = c.exec_command("grep NEO4J_PASSWORD /opt/praxis/repo/.env", timeout=5)
    neo4j_pw = stdout.read().decode().strip().split("=", 1)[1].strip().strip('"').strip("'")
    print(f"  current neo4j password length: {len(neo4j_pw)}")

    # Update docker-compose to use the .env password
    print("\n[2/4] Patch docker-compose to use the correct Neo4j password")
    cmd = f"""cd /opt/praxis/repo && sed -i 's|NEO4J_AUTH=neo4j/.*|NEO4J_AUTH=neo4j/{neo4j_pw}|' docker-compose.yml && grep NEO4J_AUTH docker-compose.yml"""
    stdin, stdout, _ = c.exec_command(cmd, timeout=10)
    print(stdout.read().decode()[:200])

    # Wipe Neo4j volume and restart
    print("\n[3/4] Wipe Neo4j data and restart with new password")
    cmd = "cd /opt/praxis/repo && docker compose down neo4j 2>&1 | tail -3 && docker volume rm $(docker volume ls -q | grep -i neo4j) 2>&1 && docker compose up -d neo4j 2>&1 | tail -5"
    stdin, stdout, _ = c.exec_command(cmd, timeout=120)
    print(stdout.read().decode()[:500])

    print("\n[4/4] Wait for Neo4j to come up healthy")
    for i in range(60):
        stdin, stdout, _ = c.exec_command("docker inspect --format='{{.State.Health.Status}}' praxis-neo4j 2>&1", timeout=5)
        status = stdout.read().decode().strip()
        if "healthy" in status:
            print(f"  ✓ Neo4j healthy after {i*2}s")
            break
        time.sleep(2)
    else:
        print("  Neo4j not healthy after 120s, last 10 lines of log:")
        stdin, stdout, _ = c.exec_command("docker logs praxis-neo4j --tail 10 2>&1", timeout=10)
        print(stdout.read().decode())

    c.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
