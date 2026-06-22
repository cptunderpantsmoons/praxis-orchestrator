"""Quality Gate 1: Webhook verifies Svix signature and accepts mock payload."""

import json
import time

from fastapi.testclient import TestClient

from praxis.webhooks.svix import compute_svix_signature, verify_svix_signature

# Test webhook secret — base64 for "testsecret"
TEST_SECRET = "whsec_dGVzdHNlY3JldA=="

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

    def test_valid_payload_accepted(self, client: TestClient, svix_signer, monkeypatch):
        """A properly signed webhook returns 200 OK with the event ID."""
        # Mock the model to avoid real HTTP calls
        from unittest.mock import MagicMock

        from praxis.models.schemas import EmailTriage, Intent, Priority, Sentiment

        mock_model = MagicMock()
        mock_model.ainvoke = MagicMock(
            return_value=MagicMock(content=EmailTriage(
                priority=Priority.NORMAL,
                intent=Intent.GENERAL_INQUIRY,
                sentiment=Sentiment.NEUTRAL,
                is_spam=False,
                sender_vip=False,
                confidence=0.5,
            ).model_dump_json())
        )
        monkeypatch.setattr("praxis.graph.nodes.UmansChatModel.create", lambda *a, **k: mock_model)

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

    def test_graph_failure_is_acknowledged_and_persisted(
        self, client: TestClient, svix_signer, monkeypatch, tmp_path
    ):
        """If graph processing fails, webhook still 200s and persists the event."""
        from praxis.config import get_settings

        failed_path = tmp_path / "failed_events.jsonl"
        get_settings.cache_clear()
        monkeypatch.setenv("AGENTMAIL_WEBHOOK_SECRET", TEST_SECRET)
        monkeypatch.setenv("FAILED_EVENTS_PATH", str(failed_path))

        async def _raise(*args, **kwargs):
            raise RuntimeError("boom")

        async def _noop(*args, **kwargs):
            return None

        # Make the graph raise
        monkeypatch.setattr(
            "praxis.webhooks.email._invoke_graph",
            staticmethod(_raise),
        )

        payload = {
            "type": "event",
            "event_type": "message.received",
            "event_id": "evt_fail123",
            "message": {
                "id": "msg_fail123",
                "from": "sender@example.com",
                "to": ["agent@praxis.ai"],
                "subject": "Fails",
                "body": "This will fail internally.",
            },
            "thread": {"thread_id": "thr_fail123"},
        }
        body = json.dumps(payload).encode()
        headers = svix_signer(body)

        response = client.post("/webhook/email", content=body, headers=headers)

        # Webhook is acknowledged so provider won't retry storm us.
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "deferred"
        assert data["thread_id"] == "thread_thr_fail123"

        # Failure should be persisted.
        assert failed_path.exists()
        lines = failed_path.read_text().strip().splitlines()
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["event_id"] == "evt_fail123"
        assert "boom" in entry["error"]

        # Retry endpoint replays successfully once failure is fixed.
        monkeypatch.setattr(
            "praxis.webhooks.email._invoke_graph",
            staticmethod(_noop),
        )
        monkeypatch.setenv("PRAXIS_ADMIN_TOKEN", "test-admin-token-1234567890abcdef1234567890")
        get_settings.cache_clear()
        admin_headers = {"Authorization": "Bearer test-admin-token-1234567890abcdef1234567890"}
        retry_resp = client.post(
            "/admin/retry-failed?event_id=evt_fail123",
            headers=admin_headers,
        )
        assert retry_resp.status_code == 200
        assert retry_resp.json()["status"] == "accepted_retry"
        # Resolved entry removed.
        assert failed_path.read_text().strip() == ""

        get_settings.cache_clear()
