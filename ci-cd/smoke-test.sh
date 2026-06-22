#!/usr/bin/env bash
# PRAXIS Smoke Test — end-to-end health + webhook signature + graph invoke
#
# Usage:
#   ./ci-cd/smoke-test.sh [BASE_URL]
#   BASE_URL defaults to http://localhost:8000
#
# Environment variables:
#   PRAXIS_WEBHOOK_SECRET  — the Svix secret (default: whsec_dGVzdHNlY3JldA==)
#   SMOKE_TIMEOUT          — per-request timeout in seconds (default: 10)
#   SMOKE_ALLOW_GRAPH_FAILURE — set to 1 to accept 5xx responses from the
#                               valid-signature test when the LLM API is
#                               unreachable (useful in CI sandboxes)
#
# Exit codes:
#   0  All smoke tests passed
#   1  Health check failed
#   2  Webhook signature verification failed (security regression)
#   3  Webhook missing-headers handling failed
#   4  Webhook graph invoke failed (auth still passed but unexpected status)

set -euo pipefail

BASE_URL="${1:-${PRAXIS_BASE_URL:-http://localhost:8000}}"
SECRET="${PRAXIS_WEBHOOK_SECRET:-whsec_dGVzdHNlY3JldA==}"
TIMEOUT="${SMOKE_TIMEOUT:-10}"
ALLOW_GRAPH_FAIL="${SMOKE_ALLOW_GRAPH_FAILURE:-0}"

log() { printf "  [smoke] %s\n" "$*"; }
fail() { printf "  [FAIL] %s\n" "$*" >&2; exit "${2:-1}"; }

log "BASE_URL=$BASE_URL"
log "TIMEOUT=${TIMEOUT}s"
log "SMOKE_ALLOW_GRAPH_FAILURE=$ALLOW_GRAPH_FAIL"

# ── 1. Health check ────────────────────────────────────────────────
log "[1/4] GET /health"
HEALTH=$(curl -sf -m "$TIMEOUT" "$BASE_URL/health" || true)
if [ -z "$HEALTH" ]; then
  fail "Health endpoint unreachable at $BASE_URL/health"
fi
echo "    Response: $HEALTH"
echo "$HEALTH" | grep -q '"status":"ok"' || fail "Health status not ok: $HEALTH"
echo "$HEALTH" | grep -q '"version"'     || fail "Health missing version: $HEALTH"
echo "$HEALTH" | grep -q '"services"'    || fail "Health missing services: $HEALTH"
log "    PASS — health endpoint returned 200 OK with version + services"

# ── 2. Webhook signature: missing headers → 401 ──────────────────
log "[2/4] POST /webhook/email with missing Svix headers (expect 401)"
CODE=$(curl -s -o /dev/null -w "%{http_code}" -m "$TIMEOUT" \
  -X POST "$BASE_URL/webhook/email" \
  -H "Content-Type: application/json" \
  -d '{"type":"email.received","data":{"from":"x@y.com","subject":"x","body":"x"}}')
[ "$CODE" = "401" ] || fail "Expected 401 for missing headers, got $CODE" 3
log "    PASS — missing Svix headers correctly rejected with 401"

# ── 3. Webhook signature: bad signature → 401 ────────────────────
log "[3/4] POST /webhook/email with invalid signature (expect 401)"
PAYLOAD='{"id":"smoke-1","type":"email.received","data":{"from":"a@b.com","subject":"smoke","body":"smoke test body"}}'
CODE=$(curl -s -o /dev/null -w "%{http_code}" -m "$TIMEOUT" \
  -X POST "$BASE_URL/webhook/email" \
  -H "Content-Type: application/json" \
  -H "svix-id: smoke_msg_$$" \
  -H "svix-timestamp: $(date +%s)" \
  -H "svix-signature: v1,AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=" \
  --data "$PAYLOAD")
[ "$CODE" = "401" ] || fail "Expected 401 for invalid signature, got $CODE" 2
log "    PASS — invalid signature correctly rejected with 401"

# ── 4. Webhook signature: valid signature → 2xx (or 5xx in sandbox) ─
log "[4/4] POST /webhook/email with valid signature (expect 2xx or 5xx in sandbox)"
TS=$(date +%s)
MSG_ID="smoke_msg_$$_$(date +%N)"

# Compute Svix signature: base64(HMAC-SHA256(secret, "{id}.{ts}.{body}"))
# Strip the "whsec_" prefix and base64-decode the secret key.
SECRET_B64="${SECRET#whsec_}"
SIG=$(SECRET_B64="$SECRET_B64" PAYLOAD="$MSG_ID.$TS.$PAYLOAD" python3 -c "
import os, hmac, hashlib, base64
secret = base64.b64decode(os.environ['SECRET_B64'])
msg = os.environ['PAYLOAD'].encode('utf-8')
digest = hmac.new(secret, msg, hashlib.sha256).digest()
print('v1,' + base64.b64encode(digest).decode())
")
CODE=$(curl -s -o /tmp/smoke_resp.json -w "%{http_code}" -m "$TIMEOUT" \
  -X POST "$BASE_URL/webhook/email" \
  -H "Content-Type: application/json" \
  -H "svix-id: $MSG_ID" \
  -H "svix-timestamp: $TS" \
  -H "svix-signature: $SIG" \
  --data "$PAYLOAD")
RESP=$(cat /tmp/smoke_resp.json)
echo "    Response: $RESP"

# Auth must not reject a correctly-signed request (no 401, no 400).
if [ "$CODE" = "401" ] || [ "$CODE" = "400" ]; then
  fail "Signature accepted by HMAC but request rejected: $CODE $RESP" 2
fi

if [ "$CODE" = "200" ]; then
  echo "$RESP" | grep -q '"status":"accepted"' || fail "Webhook did not return accepted: $RESP" 4
  echo "$RESP" | grep -q '"thread_id"'          || fail "Webhook did not return thread_id: $RESP" 4
  log "    PASS — valid signature accepted, graph invoked, thread_id returned"
elif [ "$CODE" = "500" ] && [ "$ALLOW_GRAPH_FAIL" = "1" ]; then
  log "    PASS — valid signature accepted (auth verified); graph failed because no real LLM API in sandbox (expected in CI)"
elif [ "$CODE" = "500" ]; then
  fail "Graph failed (500) — set SMOKE_ALLOW_GRAPH_FAILURE=1 for sandbox runs, or fix the LLM API" 4
else
  fail "Unexpected status $CODE for valid signature" 4
fi

# ── 5. New event-type routing: message.delivered → 200 logged ─────
log "[5/5] POST /webhook/email with message.delivered event (expect 200 logged)"
TS=$(date +%s)
MSG_ID="smoke_deliv_$$_$(date +%N)"
PAYLOAD='{"type":"event","event_type":"message.delivered","event_id":"evt_deliv_001","delivery":{"message_id":"m_001","recipients":["alice@example.com"],"delivered_at":"2026-06-20T08:01:00Z"}}'
SECRET_B64="${SECRET#whsec_}"
SIG=$(SECRET_B64="$SECRET_B64" PAYLOAD="$MSG_ID.$TS.$PAYLOAD" python3 -c "
import os, hmac, hashlib, base64
secret = base64.b64decode(os.environ['SECRET_B64'])
msg = os.environ['PAYLOAD'].encode('utf-8')
digest = hmac.new(secret, msg, hashlib.sha256).digest()
print('v1,' + base64.b64encode(digest).decode())
")
CODE=$(curl -s -o /tmp/smoke_deliv.json -w "%{http_code}" -m "$TIMEOUT" \
  -X POST "$BASE_URL/webhook/email" \
  -H "Content-Type: application/json" \
  -H "svix-id: $MSG_ID" \
  -H "svix-timestamp: $TS" \
  -H "svix-signature: $SIG" \
  --data "$PAYLOAD")
RESP=$(cat /tmp/smoke_deliv.json)
echo "    Response: $RESP"
[ "$CODE" = "200" ] || fail "Expected 200 for message.delivered, got $CODE" 4
echo "$RESP" | grep -q '"status":"logged"' || fail "Webhook did not return status=logged: $RESP" 4
echo "$RESP" | grep -q '"message_id"' && log "    PASS — message.delivered logged with message_id echoed" || log "    PASS — message.delivered logged (no message_id in response)"

# ── 6. TUI boot check ──────────────────────────────────────────────
log "[6/6] praxis tui --help (expect 0 exit, usage printed)"
if uv run praxis tui --help > /dev/null 2>&1; then
  log "    PASS — praxis tui --help exited 0"
else
  fail "praxis tui --help failed" 5
fi

echo ""
echo "  All smoke tests passed at $BASE_URL"
exit 0
