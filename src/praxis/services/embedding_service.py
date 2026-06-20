"""Embedding service for sender vector history (REQ-306).

Wraps the Umans embeddings endpoint through the shared
``UmansConcurrencyRouter`` and converts the sender email into a vector
representation suitable for storage in Qdrant.

The service is intentionally narrow: one method, ``embed_sender``,
which takes a sender email and returns a deterministic vector. The
caller is responsible for persistence (see ``embedding_node`` in
``praxis.graph.nodes``).

Failures (HTTP errors, empty vectors, timeouts) are surfaced as
``EmbeddingServiceError`` so the calling node can decide whether to
log-and-continue (graceful degradation) or hard-fail.
"""

from __future__ import annotations

from praxis.router import UmansConcurrencyRouter


class EmbeddingServiceError(Exception):
    """Raised when the embedding service cannot produce a vector."""


class EmbeddingService:
    """Thin async wrapper around the Umans embeddings endpoint."""

    def __init__(
        self,
        router: UmansConcurrencyRouter | None = None,
        model_name: str = "umans-embed-small",
    ) -> None:
        self._router = router
        self.model_name = model_name

    @property
    def router(self) -> UmansConcurrencyRouter:
        """Return the router, lazily creating one if needed."""
        if self._router is None:
            self._router = UmansConcurrencyRouter()
        return self._router

    async def embed_sender(self, sender_email: str) -> list[float]:
        """Embed a sender email into a vector.

        Args:
            sender_email: The full email address, e.g. "user@example.com".

        Returns:
            A list of floats representing the sender's embedding.

        Raises:
            EmbeddingServiceError: If the embeddings endpoint fails or
                returns an empty vector.
            ValueError: If ``sender_email`` is empty.
        """
        if not sender_email or not sender_email.strip():
            raise ValueError("sender_email must be a non-empty string")

        try:
            vector = await self.router.embed(self.model_name, sender_email)
        except Exception as exc:
            raise EmbeddingServiceError(
                f"Embedding call failed for {sender_email!r}: {exc}"
            ) from exc

        if not vector:
            raise EmbeddingServiceError(
                f"Embedding endpoint returned an empty vector for {sender_email!r}"
            )
        return vector


__all__ = ["EmbeddingService", "EmbeddingServiceError"]
