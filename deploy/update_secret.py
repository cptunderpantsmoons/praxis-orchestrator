"""Update .env with the real AgentMail webhook secret."""
from pathlib import Path
import os

# Read current .env
content = Path('.env').read_text()
new_secret = "whsec_7RXgJHJDa4ETBmRgq91bKwgHpK3QtR4+"

# Update the AGENTMAIL_WEBHOOK_SECRET line
prefix = "AGENTMAIL_WEBHOOK_SECRET="
new_lines = []
replaced = False
for line in content.splitlines():
    if line.startswith(prefix):
        new_lines.append(f"{prefix}{new_secret}")
        replaced = True
    else:
        new_lines.append(line)

if not replaced:
    new_lines.append(f"{prefix}{new_secret}")

Path('.env').write_text("\n".join(new_lines) + "\n")
os.chmod('.env', 0o600)
print(f'.env updated (replaced={replaced})')

# Verify
for line in Path('.env').read_text().splitlines():
    if 'WEBHOOK' in line:
        k, v = line.split('=', 1)
        print(f"  {k}: len={len(v)}")
