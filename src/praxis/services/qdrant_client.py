"""Async Qdrant client for sender vector history and embeddings.

Wraps qdrant-client AsyncQdrantClient with application-specific methods
for upserting, searching, and managing sender embeddings.
"""

from __future__ import annotations

import uuid
from datetime import UTC
from typing import Any

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


def _normalize_email(raw: str) -> str:
    """Extract the bare email address from an RFC 5322 sender string.

    Handles ``"Name <user@example.com>"`` and ``user@example.com`` forms,
    lower-casing the result for stable, case-insensitive identity. Returns the
    trimmed input if it does not look like an address.
    """
    if not raw:
        return ""
    addr = raw.strip()
    # "Display Name <addr@domain>" → addr@domain
    if "<" in addr and ">" in addr:
        start = addr.rfind("<") + 1
        end = addr.rfind(">")
        if start < end:
            addr = addr[start:end].strip()
    return addr.strip().lower()


def _point_id(email: str) -> str:
    """Return a deterministic UUID5 (as string) for a sender email.

    Qdrant point IDs must be unsigned integers or UUIDs; arbitrary strings
    (e.g. ``"AgentMail <gareth@agentmail.to>"``) are rejected with HTTP 400.
    """
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, _normalize_email(email)))


class QdrantSenderClient:
    """Async wrapper around AsyncQdrantClient for sender embeddings."""

    def __init__(
        self,
        url: str | None = None,
        api_key: str | None = None,
        collection: str = "praxis_senders",
        # Must match ``praxis.services.embedding_service.DEFAULT_DIM`` (384 for
        # ``BAAI/bge-small-en-v1.5``). Was 1536 historically — that mismatched
        # the actual vector size and caused every upsert to 400.
        vector_size: int = 384,
        distance: Distance = Distance.COSINE,
        timeout: float = 5.0,
    ) -> None:
        from praxis.config import get_settings
        settings = get_settings()
        self.url = url or settings.qdrant_url
        self.api_key = api_key or settings.qdrant_api_key
        self.collection = collection
        self.vector_size = vector_size
        self.distance = distance
        self.timeout = timeout
        self._client: AsyncQdrantClient | None = None

    # ── Public API ────────────────────────────────────────────

    async def upsert_sender(
        self,
        email: str,
        embedding: list[float],
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Store or update a sender's embedding in Qdrant.

        Args:
            email: Sender's email address; used as the Qdrant point id.
            embedding: Vector representation of the sender.
            metadata: Optional payload fields. The service always sets
                ``first_seen`` (only on insert) and ``last_seen`` (every
                call). If ``metadata`` contains ``total_emails``, it is
                stored verbatim; otherwise it is set to 1 on first upsert
                and incremented on subsequent calls when an existing
                point is found.

        Returns:
            True on success, False on any backend error.
        """
        await self._ensure_client()
        now_iso = _now_iso()
        # Normalise the address: "AgentMail <gareth@agentmail.to>" → "gareth@agentmail.to".
        # The raw sender header is what flows in from the webhook; store/look up by
        # the bare lower-cased address and use a deterministic UUID as the Qdrant
        # point id (Qdrant rejects arbitrary strings as point ids with HTTP 400).
        email = _normalize_email(email)
        point_id = _point_id(email)
        # Fetch existing point (if any) to track first_seen + total_emails
        existing_metadata: dict[str, Any] = {}
        try:
            existing = await self._client.retrieve(
                collection_name=self.collection,
                ids=[point_id],
                with_payload=True,
                with_vectors=False,
            )
            if existing and existing[0].payload:
                existing_metadata = dict(existing[0].payload.get("metadata") or {})
        except Exception:
            # If retrieve fails, proceed with empty metadata; last_seen
            # will be set, but first_seen will also be 'now'.
            existing_metadata = {}

        total_emails = int(existing_metadata.get("total_emails", 0)) + 1
        first_seen = existing_metadata.get("first_seen", now_iso)

        merged_metadata: dict[str, Any] = {
            "first_seen": first_seen,
            "last_seen": now_iso,
            "total_emails": total_emails,
            "sender_domain": email.split("@", 1)[-1] if "@" in email else "",
        }
        if metadata:
            merged_metadata.update(metadata)

        point = PointStruct(
            id=point_id,
            vector=embedding,
            payload={
                "email": email,
                "metadata": merged_metadata,
            },
        )
        try:
            await self._client.upsert(self.collection, points=[point])
            logger.info(
                "qdrant.upsert_sender",
                email=email,
                total_emails=total_emails,
            )
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
            # AsyncQdrantClient uses query_points (not search) for vector search
            results = await self._client.query_points(
                collection_name=self.collection,
                query=query_embedding,
                limit=limit,
                with_payload=True,
            )
            return [
                {
                    "payload": p.payload or {},
                    "email": (p.payload or {}).get("email", "unknown"),
                    "score": p.score or 0.0,
                    "metadata": (p.payload or {}).get("metadata", {}),
                }
                for p in results.points
            ]
        except Exception as exc:
            logger.warning("qdrant.search_failed", error=str(exc))
            return []

    async def get_sender_history(self, email: str, limit: int = 5) -> list[dict]:
        """Retrieve recent triage results for a sender."""
        await self._ensure_client()
        normalized = _normalize_email(email)
        try:
            points, _next_offset = await self._client.scroll(
                collection_name=self.collection,
                scroll_filter=Filter(must=[FieldCondition(key="email", match=MatchValue(value=normalized))]),
                limit=limit,
            )
            # ``points`` is a list of ``Record`` objects, each with ``.payload``
            return [p.payload for p in points]
        except Exception as exc:
            logger.warning("qdrant.history_failed", email=normalized, error=str(exc))
            return []

    async def delete_sender(self, email: str) -> bool:
        """Remove a sender record from Qdrant."""
        await self._ensure_client()
        point_id = _point_id(email)
        try:
            await self._client.delete(self.collection, points_selector=[point_id])
            logger.info("qdrant.delete_sender", email=_normalize_email(email))
            return True
        except Exception as exc:
            logger.warning("qdrant.delete_failed", email=_normalize_email(email), error=str(exc))
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
        client_kwargs: dict[str, Any] = {"url": self.url, "timeout": self.timeout}
        if self.api_key:
            client_kwargs["api_key"] = self.api_key
        self._client = AsyncQdrantClient(**client_kwargs)
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
