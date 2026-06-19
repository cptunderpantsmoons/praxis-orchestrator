"""Svix-compatible webhook signature verification.

Implements HMAC-SHA256 signature verification following the Svix
webhook signing specification:
  https://docs.svix.com/receiving/verifying-payloads

The signed payload is: ``{svix_id}.{svix_timestamp}.{raw_body}``
The signature is: ``base64(HMAC-SHA256(secret, signed_payload))``
The signature header contains: ``v1,{base64_signature}`` (space-separated
for multiple signatures).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time

# Maximum allowed clock skew between sender and receiver (seconds).
DEFAULT_TOLERANCE_SECONDS = 300  # 5 minutes


def _decode_secret(secret: str) -> bytes:
    """Decode the Svix webhook signing secret.

    Svix secrets use the ``whsec_`` prefix followed by a base64-encoded key.
    If the prefix is absent, treat the value as a raw secret string.
    """
    if secret.startswith("whsec_"):
        return base64.b64decode(secret[len("whsec_") :])
    return secret.encode("utf-8")


def compute_svix_signature(
    raw_body: bytes,
    svix_id: str,
    svix_timestamp: str,
    secret: str,
) -> str:
    """Compute the expected Svix signature for the given payload.

    Returns the signature in header format: ``v1,{base64_signature}``.
    """
    secret_bytes = _decode_secret(secret)
    signed_payload = f"{svix_id}.{svix_timestamp}.".encode() + raw_body
    digest = hmac.new(secret_bytes, signed_payload, hashlib.sha256).digest()
    return f"v1,{base64.b64encode(digest).decode('utf-8')}"


def verify_svix_signature(
    raw_body: bytes,
    svix_id: str,
    svix_timestamp: str,
    svix_signature: str,
    secret: str,
    *,
    tolerance_seconds: int = DEFAULT_TOLERANCE_SECONDS,
    current_time: int | None = None,
) -> bool:
    """Verify a Svix-compatible webhook signature.

    Args:
        raw_body: Raw request body bytes (must be the exact bytes received).
        svix_id: The ``svix-id`` header value.
        svix_timestamp: The ``svix-timestamp`` header value (Unix epoch seconds).
        svix_signature: The ``svix-signature`` header value.
        secret: The webhook signing secret (``whsec_...`` or raw string).
        tolerance_seconds: Max allowed clock skew (default: 300s = 5 min).
        current_time: Override for the current time (for testing).

    Returns:
        True if the signature is valid and within the timestamp tolerance.
    """
    # Validate timestamp
    try:
        timestamp = int(svix_timestamp)
    except (ValueError, TypeError):
        return False

    now = current_time if current_time is not None else int(time.time())
    if abs(now - timestamp) > tolerance_seconds:
        return False

    # Compute expected signature
    expected = compute_svix_signature(raw_body, svix_id, svix_timestamp, secret)

    # Compare against provided signatures
    # The svix-signature header may contain multiple space-separated signatures.
    provided_signatures = svix_signature.split(" ")
    for sig in provided_signatures:
        if hmac.compare_digest(expected, sig):
            return True

    return False


# ── Convenience aliases ────────────────────────────────────────────


def verify_webhook(
    raw_body: bytes,
    svix_id: str | None,
    svix_timestamp: str | None,
    svix_signature: str | None,
    secret: str,
) -> bool:
    """Verify a Svix-compatible webhook (convenience wrapper).

    Handles ``None`` values gracefully by treating them as empty strings,
    making it suitable for direct use with FastAPI header extraction
    where headers may be absent.
    """
    return verify_svix_signature(
        raw_body=raw_body,
        svix_id=svix_id or "",
        svix_timestamp=svix_timestamp or "",
        svix_signature=svix_signature or "",
        secret=secret,
    )


def sign_for_testing(
    raw_body: bytes,
    msg_id: str,
    secret: str,
    *,
    timestamp: int | None = None,
) -> tuple[str, str, str]:
    """Compute a valid Svix signature triple for test payloads.

    Args:
        raw_body: Raw request body bytes.
        msg_id: The ``svix-id`` to use.
        secret: The webhook signing secret.
        timestamp: Optional override (defaults to current time).

    Returns:
        ``(svix_id, svix_timestamp, svix_signature)`` — ready to use as
        HTTP headers in test requests.
    """
    ts = timestamp if timestamp is not None else int(time.time())
    sig = compute_svix_signature(raw_body, msg_id, str(ts), secret)
    return msg_id, str(ts), sig
