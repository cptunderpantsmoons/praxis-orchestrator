"""Svix-compatible webhook signature verification.

Implements HMAC-SHA256 verification following the Svix webhook specification:
https://docs.svix.com/receiving/verifying-payloads

The verification pipeline:

1.  Extract ``svix-id``, ``svix-timestamp``, ``svix-signature`` headers.
2.  Enforce a 5-minute clock-skew tolerance on the timestamp.
3.  Construct the signed content: ``"{svix_id}.{svix_timestamp}.{raw_body}"``.
4.  Compute ``HMAC-SHA256(secret, signed_content)`` → base64-encode.
5.  Compare against each ``v1,<signature>`` in the signature header
    using constant-time comparison.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time

# Maximum allowed clock skew between sender and receiver (5 minutes)
MAX_TIMESTAMP_DRIFT_SECONDS = 300


def _prepare_secret(secret: str) -> bytes:
    """Convert a Svix webhook secret string to raw key bytes.

    Handles secrets with or without the ``whsec_`` prefix.
    The secret is base64-decoded to obtain the raw HMAC key.
    """
    raw = secret.encode() if isinstance(secret, str) else secret
    if raw.startswith(b"whsec_"):
        raw = raw[len(b"whsec_") :]
    return base64.b64decode(raw)


def verify_webhook(
    payload: bytes,
    svix_id: str,
    svix_timestamp: str,
    svix_signature: str,
    secret: str,
) -> bool:
    """Verify a Svix-compatible webhook signature.

    Args:
        payload: Raw request body bytes.
        svix_id: Value of the ``svix-id`` header.
        svix_timestamp: Value of the ``svix-timestamp`` header (Unix epoch seconds).
        svix_signature: Value of the ``svix-signature`` header.
        secret: Webhook signing secret (with or without ``whsec_`` prefix).

    Returns:
        ``True`` if the signature is valid and the timestamp is within tolerance.
    """
    # ── Timestamp freshness check ───────────────────────────────────
    try:
        timestamp = int(svix_timestamp)
    except (ValueError, TypeError):
        return False

    now = int(time.time())
    if abs(now - timestamp) > MAX_TIMESTAMP_DRIFT_SECONDS:
        return False

    # ── Compute expected signature ──────────────────────────────────
    key = _prepare_secret(secret)
    signed_content = f"{svix_id}.{svix_timestamp}.".encode() + payload
    expected = base64.b64encode(
        hmac.new(key, signed_content, hashlib.sha256).digest()
    ).decode()

    # ── Constant-time comparison against provided signatures ────────
    # The header may contain multiple signatures: "v1,<sig> v1,<sig2>"
    for sig_part in svix_signature.split(" "):
        if "," not in sig_part:
            continue
        version, signature = sig_part.split(",", 1)
        if version == "v1" and hmac.compare_digest(signature, expected):
            return True

    return False


def sign_for_testing(
    payload: bytes,
    msg_id: str,
    secret: str,
    *,
    timestamp: int | None = None,
) -> tuple[str, str, str]:
    """Generate a valid Svix-compatible signature for testing.

    Returns:
        Tuple of ``(svix_id, svix_timestamp, svix_signature)`` header values.
    """
    if timestamp is None:
        timestamp = int(time.time())

    key = _prepare_secret(secret)
    signed_content = f"{msg_id}.{timestamp}.".encode() + payload
    signature = base64.b64encode(
        hmac.new(key, signed_content, hashlib.sha256).digest()
    ).decode()

    return msg_id, str(timestamp), f"v1,{signature}"
