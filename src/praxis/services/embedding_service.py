"""Embedding service for sender vector history (REQ-306).

Production embedding strategy (in priority order):

1. **fastembed** (Qdrant) — primary, local, no API, fast
   - Default model: ``BAAI/bge-small-en-v1.5`` (384 dims, 33MB)
   - Runs on CPU, no GPU required
   - First call downloads the model (~30s), subsequent calls are instant
   - Real semantic embeddings, not fake

2. **Deterministic hash fallback** — if fastembed fails or model
   download is blocked, we still produce a usable 384-dim vector.
   The hash fallback is NOT semantically meaningful, but it is
   consistent (same input → same vector) and lets the system
   function offline.

The service is intentionally narrow: ``embed_sender`` takes a sender
email and returns a list of floats. The caller (embedding_node) is
responsible for persistence to Qdrant.

Reference: https://github.com/qdrant/fastembed
"""

from __future__ import annotations

import hashlib
import math
from typing import Any

logger_module_name = "praxis.embedding"


# Default embedding dimension. Matches BAAI/bge-small-en-v1.5.
DEFAULT_DIM = 384


def _hash_embedding(text: str, dim: int = DEFAULT_DIM) -> list[float]:
    """Deterministic hash-based embedding fallback.

    Not semantically meaningful, but:
    - Same input always produces the same vector
    - Different inputs produce different vectors
    - Works offline, zero dependencies, microseconds to compute

    Algorithm: split the hash into 4-byte chunks, normalize to [-1, 1],
    then L2-normalize so it can be used for cosine similarity.
    """
    digest = hashlib.sha512(text.encode("utf-8")).digest()
    # Each 4-byte chunk gives us a float in [0, 2^32)
    raw = []
    for i in range(0, len(digest), 4):
        chunk = digest[i:i + 4]
        if len(chunk) < 4:
            chunk = chunk + b"\x00" * (4 - len(chunk))
        # Map to [-1, 1]
        value = (int.from_bytes(chunk, "big") / 0xFFFFFFFF) * 2.0 - 1.0
        raw.append(value)
    # Extend to dim by hashing again with a salt
    while len(raw) < dim:
        digest = hashlib.sha512(digest).digest()
        for i in range(0, len(digest), 4):
            if len(raw) >= dim:
                break
            chunk = digest[i:i + 4]
            if len(chunk) < 4:
                chunk = chunk + b"\x00" * (4 - len(chunk))
            value = (int.from_bytes(chunk, "big") / 0xFFFFFFFF) * 2.0 - 1.0
            raw.append(value)
    # L2-normalize
    norm = math.sqrt(sum(x * x for x in raw)) or 1.0
    return [x / norm for x in raw[:dim]]


class EmbeddingServiceError(Exception):
    """Raised when the embedding service cannot produce a vector."""


class EmbeddingService:
    """Async embedding service. Tries fastembed first, falls back to hash.

    Usage::

        svc = EmbeddingService()
        vector = await svc.embed_sender("alice@example.com")
        # vector is a 384-dim list of floats in [-1, 1]
    """

    def __init__(
        self,
        model_name: str = "BAAI/bge-small-en-v1.5",
        dim: int = DEFAULT_DIM,
    ) -> None:
        self.model_name = model_name
        self.dim = dim
        self._model: Any = None
        self._backend: str = "not_initialized"

    def _ensure_model(self) -> None:
        """Lazy-load the fastembed model on first call."""
        if self._model is not None:
            return
        try:
            from fastembed import TextEmbedding  # type: ignore

            self._model = TextEmbedding(model_name=self.model_name)
            self._backend = "fastembed"
        except Exception as exc:
            # No network / no model / not installed — fall back to hash
            self._model = False  # sentinel: tried and failed
            self._backend = f"hash (fastembed unavailable: {exc})"

    async def embed_sender(self, sender_email: str) -> list[float]:
        """Embed a sender email into a 384-dim vector.

        Args:
            sender_email: The full email address, e.g. "user@example.com".

        Returns:
            A list of floats (length = self.dim) representing the embedding.
            Always succeeds — falls back to deterministic hash if fastembed
            is unavailable.

        Raises:
            ValueError: If ``sender_email`` is empty.
        """
        if not sender_email or not sender_email.strip():
            raise ValueError("sender_email must be a non-empty string")

        # Try fastembed first
        try:
            self._ensure_model()
        except Exception:
            self._backend = "hash (init failed)"
            self._model = False

        if self._model is not False:
            # fastembed path
            try:
                # fastembed.embed() returns a generator of numpy arrays
                embeddings = list(self._model.embed([sender_email]))
                if embeddings and len(embeddings[0]) > 0:
                    vec = embeddings[0]
                    return [float(x) for x in vec]
            except Exception as exc:
                # fall through to hash
                self._backend = f"hash (fastembed error: {exc})"
                self._model = False

        # Hash fallback — always works
        return _hash_embedding(sender_email, self.dim)

    @property
    def backend(self) -> str:
        """Which backend is currently active ('fastembed' or 'hash')."""
        return self._backend


__all__ = ["DEFAULT_DIM", "EmbeddingService", "EmbeddingServiceError"]
