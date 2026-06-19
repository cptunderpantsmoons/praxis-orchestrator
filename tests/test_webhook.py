"""Tests for the AgentMail webhook endpoint and Svix verification — QG1 criterion 2.

Verifies that:
- Valid signature → 200 OK with event ID
- Missing Svix headers → 401 Unauthorized
- Invalid signature → 401 Unauthorized
- Expired timestamp → 401 Unauthorized
- Tampered payload → 401 Unauthorized
"""

import json
import time

from fastapi.testclient import TestClient

from praxis.webhooks.svix import sign_for_testing, verify_webhook

VALID_PAYLOAD = {
    "id": "evt_abc123",
    "type": "email.received",
    "timestamp": "2026-06-19T12:00:00Z",
    "data": {
        "from": "sender@example.com",
        "to": ["recipient@example.com"],
        "subject": "Test Email",
        "body": "This is a test email body.",
    },
}


def _payload_bytes() -> bytes:
    return json.dumps(VALID_PAYLOAD).encode()


# ── Endpoint tests ───────────────────────────────────────────────────


def test_valid_webhook_accepted(client: TestClient, make_svix_headers):
    """Valid signature + valid payload → 200 OK with event ID."""
    payload = _payload_bytes()
    headers = make_svix_headers(payload, msg_id="evt_abc123")

    response = client.post("/webhook/email", content=payload, headers=headers)

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "accepted"
    assert data["event_id"] == "evt_abc123"


def test_missing_svix_headers_rejected(client: TestClient):
    """No Svix headers → 401 Unauthorized."""
    payload = _payload_bytes()

    response = client.post("/webhook/email", content=payload)

    assert response.status_code == 401


def test_invalid_signature_rejected(client: TestClient, make_svix_headers):
    """Wrong signature → 401 Unauthorized."""
    payload = _payload_bytes()
    headers = make_svix_headers(payload, msg_id="evt_abc123")
    headers["svix-signature"] = "v1,aW52YWxpZF9zaWduYXR1cmU="

    response = client.post("/webhook/email", content=payload, headers=headers)

    assert response.status_code == 401


def test_expired_timestamp_rejected(client: TestClient, make_svix_headers):
    """Timestamp older than 5 minutes → 401 Unauthorized."""
    payload = _payload_bytes()
    old_ts = int(time.time()) - 600  # 10 minutes ago
    headers = make_svix_headers(payload, msg_id="evt_abc123", timestamp=old_ts)

    response = client.post("/webhook/email", content=payload, headers=headers)

    assert response.status_code == 401


def test_tampered_payload_rejected(client: TestClient, make_svix_headers):
    """Signature computed for original payload, body tampered → 401."""
    payload = _payload_bytes()
    headers = make_svix_headers(payload, msg_id="evt_abc123")

    tampered = json.dumps(
        {**VALID_PAYLOAD, "data": {**VALID_PAYLOAD["data"], "body": "TAMPERED"}}
    ).encode()

    response = client.post("/webhook/email", content=tampered, headers=headers)

    assert response.status_code == 401


def test_html_body_and_attachments_accepted(client: TestClient, make_svix_headers):
    """Payload with optional html_body and attachments is accepted."""
    payload_dict = {
        **VALID_PAYLOAD,
        "data": {
            **VALID_PAYLOAD["data"],
            "html_body": "<p>Test</p>",
            "attachments": [
                {"filename": "test.pdf", "size": 1024, "content_type": "application/pdf"}
            ],
        },
    }
    payload = json.dumps(payload_dict).encode()
    headers = make_svix_headers(payload, msg_id="evt_with_attachments")

    response = client.post("/webhook/email", content=payload, headers=headers)

    assert response.status_code == 200
    assert response.json()["event_id"] == "evt_abc123"


# ── Unit tests for verify_webhook ────────────────────────────────────


def test_verify_webhook_valid(svix_secret: str):
    """Valid signature verifies successfully."""
    payload = b'{"id":"test"}'
    sid, sts, ssig = sign_for_testing(payload, "msg_001", svix_secret)
    assert verify_webhook(payload, sid, sts, ssig, svix_secret) is True


def test_verify_webhook_invalid_signature(svix_secret: str):
    """Wrong signature fails verification."""
    payload = b'{"id":"test"}'
    sid, sts, _ = sign_for_testing(payload, "msg_001", svix_secret)
    assert verify_webhook(payload, sid, sts, "v1,d3Jvbmc=", svix_secret) is False


def test_verify_webhook_expired_timestamp(svix_secret: str):
    """Expired timestamp fails verification."""
    payload = b'{"id":"test"}'
    old_ts = int(time.time()) - 600
    sid, _, ssig = sign_for_testing(
        payload, "msg_001", svix_secret, timestamp=old_ts
    )
    assert verify_webhook(payload, sid, str(old_ts), ssig, svix_secret) is False


def test_verify_webhook_tampered_payload(svix_secret: str):
    """Tampered payload fails verification."""
    payload = b'{"id":"test"}'
    sid, sts, ssig = sign_for_testing(payload, "msg_001", svix_secret)
    assert (
        verify_webhook(b'{"id":"tampered"}', sid, sts, ssig, svix_secret) is False
    )


def test_verify_webhook_missing_headers(svix_secret: str):
    """Missing/empty headers fail verification."""
    payload = b'{"id":"test"}'
    assert verify_webhook(payload, "", "", "", svix_secret) is False
    assert verify_webhook(payload, None, None, None, svix_secret) is False  # type: ignore[arg-type]


def test_verify_webhook_multiple_signatures(svix_secret: str):
    """Multiple signatures in header — one valid → verification passes."""
    payload = b'{"id":"test"}'
    sid, sts, ssig = sign_for_testing(payload, "msg_001", svix_secret)
    # Header with multiple sigs, one valid
    multi_sig = f"v1,aW52YWxpZA== {ssig}"
    assert verify_webhook(payload, sid, sts, multi_sig, svix_secret) is True
