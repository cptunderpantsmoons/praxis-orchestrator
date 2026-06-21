"""Test Qdrant from inside the app container."""
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


def main():
    env = load_env()
    pw = env["SSH_PASSWORD"]
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect("2.25.195.193", 22, "root", pw, timeout=10, allow_agent=False, look_for_keys=False)

    # Write a small test script to the server and exec it inside the app container
    test_script = (
        "from praxis.config import get_settings; "
        "s = get_settings(); "
        "print('qdrant_url:', s.qdrant_url); "
        "print('qdrant_api_key:', repr(s.qdrant_api_key))"
    )
    stdin, stdout, _ = c.exec_command(
        f"docker exec praxis-app python -c {test_script!r}", timeout=15
    )
    print(stdout.read().decode())

    # Now try a real call
    test_call = (
        "import asyncio; "
        "from praxis.services.qdrant_client import QdrantSenderClient; "
        "async def main(): "
        "  c = QdrantSenderClient(); "
        "  await c._ensure_collection(); "
        "  print('OK: collection ensured'); "
        "asyncio.run(main())"
    )
    stdin, stdout, _ = c.exec_command(
        f"docker exec praxis-app python -c {test_call!r}", timeout=30
    )
    print("=== real call ===")
    print(stdout.read().decode()[:1500])
    c.close()


if __name__ == "__main__":
    main()
