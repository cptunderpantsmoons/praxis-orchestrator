"""Quality Gate 1: Webhook verifies Svix signature and accepts mock payload."""

import json
import time

from fastapi.testclient import TestClient

from praxis.webhooks.svix import compute_svix_signature, verify_svix_signature

# ── Svix Verification Unit Tests ───────────────────────────────


class TestSvixVerification:
    """Unit tests for the Svix-compatible signature verification."""

    SECRET = "whsec_dGVzdHNlY3JldA=="

    def test_valid_signature_passes(self):
        """A correctly computed signature is accepted."""
        body = b'{"id":"evt_123","type":"email.received","data":{"from":"a@b.com"}}'
        ts = str(int(time.time()))
        sig = compute_svix_signature(body, "msg_123", ts, self.SECRET)

        assert verify_svix_signature(body, "msg_123", ts, sig, self.SECRET)

    def test_tampered_body_fails(self):
        """A modified body produces a different signature and fails verification."""
        body = b'{"id":"evt_123","type":"email.received"}'
        tampered = b'{"id":"evt_456","type":"email.received"}'
        ts = str(int(time.time()))
        sig = compute_svix_signature(body, "msg_123", ts, self.SECRET)

        assert not verify_svix_signature(tampered, "msg_123", ts, sig, self.SECRET)

    def test_expired_timestamp_fails(self):
        """A timestamp older than the tolerance window is rejected."""
        body = b'{"id":"evt_123"}'
        old_ts = str(int(time.time()) - 600)  # 10 minutes ago
        sig = compute_svix_signature(body, "msg_123", old_ts, self.SECRET)

        assert not verify_svix_signature(body, "msg_123", old_ts, sig, self.SECRET)

    def test_wrong_secret_fails(self):
        """A signature computed with a different secret fails."""
        body = b'{"id":"evt_123"}'
        ts = str(int(time.time()))
        sig = compute_svix_signature(body, "msg_123", ts, "whsec_d3JvbmdzZWNyZXQ=")

        assert not verify_svix_signature(body, "msg_123", ts, sig, self.SECRET)

    def test_missing_headers_fails(self):
        """Empty headers are rejected."""
        body = b'{"id":"evt_123"}'
        assert not verify_svix_signature(body, "", "", "", self.SECRET)

    def test_multiple_signatures_one_valid(self):
        """Multiple signatures in the header: one valid is enough."""
        body = b'{"id":"evt_123"}'
        ts = str(int(time.time()))
        valid_sig = compute_svix_signature(body, "msg_123", ts, self.SECRET)
        fake_sig = "v1,aW52YWxpZHNpZw=="
        combined = f"{fake_sig} {valid_sig}"

        assert verify_svix_signature(body, "msg_123", ts, combined, self.SECRET)

    def test_raw_string_secret_works(self):
        """A non-whsec_ secret (raw string) also works."""
        body = b'{"id":"evt_123"}'
        ts = str(int(time.time()))
        raw_secret = "my-raw-secret"
        sig = compute_svix_signature(body, "msg_123", ts, raw_secret)

        assert verify_svix_signature(body, "msg_123", ts, sig, raw_secret)


# ── Webhook Endpoint Integration Tests ─────────────────────────


class TestWebhookEndpoint:
    """Integration tests for the /webhook/email endpoint."""

    def test_valid_payload_accepted(self, client: TestClient, svix_signer):
        """A properly signed webhook returns 200 OK with the event ID."""
        payload = {
            "id": "evt_abc123",
            "type": "email.received",
            "timestamp": "2026-06-19T12:00:00Z",
            "data": {
                "from": "sender@example.com",
                "to": ["agent@praxis.ai"],
                "subject": "Test Email",
                "body": "This is a test email.",
            },
        }
        body = json.dumps(payload).encode()
        headers = svix_signer(body)

        response = client.post("/webhook/email", content=body, headers=headers)

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "accepted"
        assert data["event_id"] == "evt_abc123"

    def test_missing_svix_headers_rejected(self, client: TestClient):
        """A request without Svix headers returns 401."""
        response = client.post(
            "/webhook/email",
            content=b'{"id":"evt_123"}',
            headers={},
        )
        assert response.status_code == 401

    def test_invalid_signature_rejected(self, client: TestClient):
        """A request with a bad signature returns 401."""
        response = client.post(
            "/webhook/email",
            content=b'{"id":"evt_123"}',
            headers={
                "svix-id": "msg_123",
                "svix-timestamp": str(int(time.time())),
                "svix-signature": "v1,aW52YWxpZHNpZw==",
            },
        )
        assert response.status_code == 401

    def test_expired_signature_rejected(self, client: TestClient, svix_signer):
        """A signature with an old timestamp returns 401."""
        body = b'{"id":"evt_old"}'
        old_ts = int(time.time()) - 600  # 10 minutes ago
        headers = svix_signer(body, timestamp=old_ts)

        response = client.post("/webhook/email", content=body, headers=headers)
        assert response.status_code == 401
