"""Full redeploy: upload, wipe data, rebuild, start, verify."""
import paramiko
import time
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


def run(c, cmd, timeout=600):
    print(f"  $ {cmd[:120]}{'...' if len(cmd) > 120 else ''}")
    stdin, stdout, stderr = c.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode()
    err = stderr.read().decode()
    if out:
        print(out)
    if err and "Error" in err:
        print(f"  [stderr] {err[:300]}")
    return out


def main():
    env = load_env()
    pw = env["SSH_PASSWORD"]
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect("2.25.195.193", 22, "root", pw, timeout=10, allow_agent=False, look_for_keys=False)
    print("[1/6] Connected to server")

    # Upload files
    for f in ["Dockerfile", "docker-compose.yml"]:
        sftp = c.open_sftp()
        sftp.put(f, f"/opt/praxis/repo/{f}")
        sftp.close()
        print(f"[2/6] Uploaded {f}")

    # Wipe data
    run(c, "cd /opt/praxis/repo && docker compose down 2>&1 | tail -3", timeout=60)
    run(c, "cd /opt/praxis/repo && for v in $(docker volume ls -q | grep -E 'neo4j|qdrant|postgres'); do docker volume rm $v; done", timeout=30)
    print("[3/6] Wiped volumes")

    # Build
    print("[4/6] Building image...")
    run(c, "cd /opt/praxis/repo && docker build --no-cache -t praxis:0.1.0 . 2>&1 | tail -8", timeout=600)

    # Start
    print("[5/6] Starting all services...")
    run(c, "cd /opt/praxis/repo && docker compose up -d 2>&1 | tail -10", timeout=120)

    # Wait for health
    time.sleep(20)
    print("[6/6] Verifying /health...")
    out = run(c, "curl -s -m 10 https://praxis.2.25.195.193.sslip.io/health -k", timeout=15)
    print(f"  Result: {out}")
    c.close()


if __name__ == "__main__":
    main()
