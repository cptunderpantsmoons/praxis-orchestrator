#!/usr/bin/env bash
# PRAXIS v2.0 — One-shot self-extracting deploy for a fresh Ubuntu server.
# Run this on the server (via KVM, web shell, or working SSH). No auth required
# to read; it will prompt for the secrets interactively.
#
# Usage:
#   bash praxis-bootstrap.sh
#
# After it finishes, PRAXIS is live on http://<server-ip>:8000
set -euo pipefail

RED='\033[1;31m'; GRN='\033[1;32m'; YEL='\033[1;33m'; NC='\033[0m'
log()  { printf "${GRN}[setup]${NC} %s\n" "$*"; }
warn() { printf "${YEL}[warn]${NC}  %s\n" "$*"; }
fail() { printf "${RED}[FAIL]${NC}  %s\n" "$*"; exit 1; }

[ "$EUID" -eq 0 ] || fail "Run as root: sudo bash $0"

# ── 0. Detect OS ─────────────────────────────────────────────
. /etc/os-release
log "Detected: $ID $VERSION_ID ($VERSION_CODENAME)"

case "$ID" in
  ubuntu|debian) ;;
  *) fail "This script supports Ubuntu/Debian only. Detected: $ID" ;;
esac

# ── 1. Install Docker if missing ────────────────────────────
if ! command -v docker >/dev/null 2>&1; then
  log "Installing Docker..."
  apt-get update -y
  apt-get install -y ca-certificates curl gnupg git
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/$ID/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  chmod a+r /etc/apt/keyrings/docker.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/$ID $VERSION_CODENAME stable" > /etc/apt/sources.list.d/docker.list
  apt-get update -y
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  systemctl enable --now docker
fi
log "Docker: $(docker --version)"

# ── 2. Install uv (for tests if needed) ────────────────────
command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh

# ── 3. Create directories ───────────────────────────────────
PRAXIS_HOME=/opt/praxis
mkdir -p "$PRAXIS_HOME/repo"
cd "$PRAXIS_HOME/repo"

# ── 4. Collect secrets interactively ───────────────────────
ENV_FILE="$PRAXIS_HOME/repo/.env"
if [ -f "$ENV_FILE" ]; then
  log "Existing .env found at $ENV_FILE — keeping it (chmod 600 enforced)"
  chmod 600 "$ENV_FILE"
else
  log "Collecting production secrets (input hidden)..."
  echo
  printf "  AGENTMAIL_API_KEY  (am_us_... format): "
  read -rs AM_KEY; echo
  [ -z "$AM_KEY" ] && fail "AGENTMAIL_API_KEY cannot be empty"
  printf "  AGENTMAIL_WEBHOOK_SECRET  (whsec_... format): "
  read -rs AM_SECRET; echo
  [ -z "$AM_SECRET" ] && fail "AGENTMAIL_WEBHOOK_SECRET cannot be empty"
  printf "  UMANS_API_KEY  (sk-R-... format): "
  read -rs UMANS_KEY; echo
  [ -z "$UMANS_KEY" ] && fail "UMANS_API_KEY cannot be empty"
  printf "  GITHUB_TOKEN  (ghp_... format, optional — press Enter to skip): "
  read -rs GH_TOKEN; echo
  if [ -z "$GH_TOKEN" ]; then
    warn "No GitHub token — you will need to manually upload the repo"
    SKIP_CLONE=1
  fi
  echo

  cat > "$ENV_FILE" <<ENV_TEMPLATE
# PRAXIS v2.0 — Production secrets
# Generated $(date -u +%Y-%m-%dT%H:%M:%SZ)
# chmod 600 — do not commit, do not share

ENVIRONMENT=production
HOST=0.0.0.0
PORT=8000

# ── AgentMail (inbound + outbound email) ──────────────────
AGENTMAIL_API_KEY=*** AGENTMAIL_INBOX_ID=ib_praxis2
AGENTMAIL_WEBHOOK_SECRET=*** ── Umans Inference (LLM) ───────────────────────
UMANS_API_KEY=*** _BASE_URL=https://api.code.umans.ai/v1
UMANS_GLM_CONTEXT_WINDOW=1000000
UMANS_KIMI_CONTEXT_WINDOW=131072
UMANS_QWEN_CONTEXT_WINDOW=131072

# ── Neo4j (graph DB) ──────────────────────────────────────
NEO4J_URI=bolt://neo4j:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=*** ── Qdrant (vector DB) ───────────────────────────────────
QDRANT_URL=http://qdrant:6333
QDRANT_API_KEY=*** ── PostgreSQL (LangGraph checkpointing) ─────────────
POSTGRES_DSN=postgresql://praxis:***@postgres:5432/praxis

# ── Optional services ─────────────────────────────────────
# COMPOSIO_API_KEY=*** # HOSTINGER_API_KEY=*** ENV_TEMPLATE
  chmod 600 "$ENV_FILE"
  log ".env written, chmod 600"
fi

# ── 5. Get the repo ───────────────────────────────────────
if [ ! -d .git ]; then
  if [ -n "${GH_TOKEN:-}" ] && [ "${SKIP_CLONE:-0}" != "1" ]; then
    log "Cloning from GitHub (using token)..."
    git clone "https://${GH_TOKEN}@github.com/cptunderpantsmoons/praxis-v2.git" .
  else
    fail "No GitHub token AND no existing repo. Upload the project tarball or git clone it manually into $PRAXIS_HOME/repo before re-running."
  fi
fi
log "Repo HEAD: $(git rev-parse --short HEAD)"

# ── 6. Start infra (postgres, neo4j, qdrant) ──────────────
log "Starting Neo4j + Qdrant + PostgreSQL (this can take 1-2 min)..."
docker compose up -d neo4j qdrant postgres
log "Waiting for services to be healthy..."
for i in $(seq 1 60); do
  ok=true
  curl -sf http://localhost:6333/healthz >/dev/null 2>&1 || ok=false
  curl -sf http://localhost:7474/        >/dev/null 2>&1 || ok=false
  if $ok; then
    log "Infra ready after ${i}s"
    break
  fi
  sleep 1
done

# ── 7. Build and start PRAXIS app ─────────────────────────
log "Building PRAXIS image (this can take 2-3 min)..."
docker build -t praxis:0.1.0 . 2>&1 | tail -5
log "Starting PRAXIS app..."
docker compose up -d app

# ── 8. Wait for /health ───────────────────────────────────
log "Waiting for /health..."
for i in $(seq 1 30); do
  if health=$(curl -sf http://localhost:8000/health); then
    log "PRAXIS is live after ${i}s"
    echo "  $health" | sed 's/^/    /'
    break
  fi
  sleep 2
done

# ── 9. Smoke test ─────────────────────────────────────────
if [ -f ci-cd/smoke-test.sh ]; then
  log "Running smoke test..."
  bash ci-cd/smoke-test.sh http://localhost:8000 || warn "smoke test had failures (see above)"
fi

# ── 10. Done ──────────────────────────────────────────────
cat <<EOF

${GRN}================================================${NC}
${GRN}  PRAXIS v2.0 is deployed and live${NC}
${GRN}================================================${NC}

  URL:        http://$(hostname -I | awk '{print $1}'):8000
  Health:     http://$(hostname -I | awk '{print $1}'):8000/health
  Logs:       cd $PRAXIS_HOME/repo && docker compose logs -f app
  Update:     cd $PRAXIS_HOME/repo && git pull && docker compose build app && docker compose up -d app
  Rollback:   cd $PRAXIS_HOME/repo && git checkout v0.1.0 && docker compose up -d --build

  NOTE: If port 8000 is not reachable from outside, open it in your
  hosting provider's firewall (Hostinger: panel → Firewall → + rule).

EOF
