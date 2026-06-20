"""Async Qdrant client for sender vector history and embeddings.

Wraps qdrant-client AsyncQdrantClient with application-specific methods
for upserting, searching, and managing sender embeddings.
"""

from __future__ import annotations

from datetime import UTC

import structlog
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PointStruct,
    VectorParams,
)

logger = structlog.get_logger()


class QdrantSenderClient:
    """Async wrapper around AsyncQdrantClient for sender embeddings."""

    def __init__(
        self,
        url: str | None = None,
        collection: str = "praxis_senders",
        vector_size: int = 1536,
        distance: Distance = Distance.COSINE,
        timeout: float = 5.0,
    ) -> None:
        self.url = url
        self.collection = collection
        self.vector_size = vector_size
        self.distance = distance
        self.timeout = timeout
        self._client: AsyncQdrantClient | None = None

    # ── Public API ────────────────────────────────────────────

    async def upsert_sender(self, email: str, embedding: list[float]) -> bool:
        """Store or update a sender's embedding in Qdrant."""
        await self._ensure_client()
        point = PointStruct(
            id=email,
            vector=embedding,
            payload={
                "email": email,
                "metadata": {"first_seen": _now_iso(), "last_seen": _now_iso()},
            },
        )
        try:
            await self._client.upsert(self.collection, points=[point])
            logger.info("qdrant.upsert_sender", email=email)
            return True
        except Exception as exc:
            logger.warning("qdrant.upsert_failed", email=email, error=str(exc))
            return False

    async def get_similar_senders(
        self, query_embedding: list[float], limit: int = 10
    ) -> list[dict]:
        """Find similar senders by embedding cosine similarity."""
        await self._ensure_client()
        try:
            results = await self._client.search(
                collection_name=self.collection,
                query_vector=query_embedding,
                limit=limit,
            )
            return [
                {
                    "email": r.payload["email"],
                    "score": r.score,
                    "metadata": r.payload.get("metadata", {}),
                }
                for r in results
            ]
        except Exception as exc:
            logger.warning("qdrant.search_failed", error=str(exc))
            return []

    async def get_sender_history(self, email: str, limit: int = 5) -> list[dict]:
        """Retrieve recent triage results for a sender."""
        await self._ensure_client()
        try:
            records = await self._client.scroll(
                collection_name=self.collection,
                scroll_filter=Filter(must=[FieldCondition(key="email", match=MatchValue(value=email))]),
                limit=limit,
            )
            return [r[0].payload for r in records[0]]
        except Exception as exc:
            logger.warning("qdrant.history_failed", email=email, error=str(exc))
            return []

    async def delete_sender(self, email: str) -> bool:
        """Remove a sender record from Qdrant."""
        await self._ensure_client()
        try:
            await self._client.delete(self.collection, points_selector=[email])
            logger.info("qdrant.delete_sender", email=email)
            return True
        except Exception as exc:
            logger.warning("qdrant.delete_failed", email=email, error=str(exc))
            return False

    async def health_check(self) -> bool:
        """Verify Qdrant connection is healthy."""
        try:
            await self._ensure_client()
            collections = await self._client.get_collections()
            return any(c.name == self.collection for c in collections.collections)
        except Exception:
            return False

    # ── Internal ──────────────────────────────────────────────

    async def _ensure_client(self) -> None:
        """Lazy-initialize the Qdrant client and create collection if needed."""
        if self._client is not None:
            return
        self._client = AsyncQdrantClient(url=self.url, timeout=self.timeout)
        # Create collection if not exists
        try:
            collections = await self._client.get_collections()
            exists = any(c.name == self.collection for c in collections.collections)
            if not exists:
                await self._client.create_collection(
                    collection_name=self.collection,
                    vectors_config=VectorParams(size=self.vector_size, distance=self.distance),
                )
                logger.info("qdrant.collection_created", collection=self.collection)
        except Exception as exc:
            logger.warning("qdrant.health_check_failed", error=str(exc))

    async def close(self) -> None:
        """Close the Qdrant client connection."""
        if self._client is not None:
            await self._client.close()
            self._client = None


def _now_iso() -> str:
    """Return current UTC time as ISO 8601 string."""
    from datetime import datetime
    return datetime.now(UTC).isoformat()
