"""Async Neo4j client for graph context queries.

During Phase 2 this is a thin wrapper around the official async Neo4j driver.
The checkpointing store lives in PostgreSQL; Neo4j is used only for enterprise
graph context (sender history, corrections) in Phases 3 and 4.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

import structlog
from neo4j import AsyncGraphDatabase

from praxis.config import Settings, get_settings
from praxis.models.schemas import CorrectionSummary, ThreadSummary

if TYPE_CHECKING:
    from praxis.features.sender_style import SenderStyle

logger = structlog.get_logger()


class Neo4jContextClient:
    """Async client for retrieving enterprise-memorised context."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._driver: Any | None = None

    async def connect(self) -> None:
        """Create the async Neo4j driver."""
        self._driver = AsyncGraphDatabase.driver(
            self._settings.neo4j_uri,
            auth=(self._settings.neo4j_user, self._settings.neo4j_password),
        )
        logger.info("neo4j.connected", uri=self._settings.neo4j_uri)

    async def close(self) -> None:
        """Close the async Neo4j driver."""
        if self._driver is not None:
            await self._driver.close()
            logger.info("neo4j.closed")

    async def fetch_sender_history(
        self,
        sender_email: str,
        limit: int = 5,
    ) -> list[ThreadSummary]:
        """Return the most recent thread summaries for a sender.

        If the database is unavailable or the sender has no history, return an
        empty list so the graph can continue gracefully.
        """
        if self._driver is None:
            return []

        query = """
        MATCH (p:Person {email: $sender_email})-[:SENT_EMAIL]->(t:Thread)
        RETURN t.message_id AS message_id,
               t.subject AS subject,
               t.body AS body,
               t.timestamp AS timestamp
        ORDER BY t.timestamp DESC
        LIMIT $limit
        """
        try:
            async with self._driver.session() as session:
                result = await session.run(query, sender_email=sender_email, limit=limit)
                records = [record.data() async for record in result]
        except Exception as exc:  # pragma: no cover - driver failure fallback
            logger.warning("neo4j.history_failed", error=str(exc))
            return []

        summaries: list[ThreadSummary] = []
        for record in records:
            ts = record.get("timestamp")
            if isinstance(ts, str):
                ts = datetime.fromisoformat(ts)
            elif ts is None:
                ts = datetime.now()
            summaries.append(
                ThreadSummary(
                    thread_id=record.get("message_id", ""),
                    subject=record.get("subject", ""),
                    last_message_summary=(record.get("body") or "")[:400],
                    timestamp=ts,
                )
            )
        return summaries

    async def fetch_relevant_corrections(
        self,
        sender_email: str,
        intent_category: str,
        limit: int = 5,
    ) -> list[CorrectionSummary]:
        """Return relevant corrections for the sender + current intent.

        In Phase 2 the graph schema may not yet exist locally (Docker optional),
        so this returns an empty list when the driver is not connected.
        """
        if self._driver is None:
            return []

        query = """
        MATCH (p:Person {email: $sender_email})-[:HAS_CORRECTION]->(c:Correction)
        WHERE c.applied_count < 50
        RETURN c.id AS correction_id,
               c.category AS category,
               c.structured_rule AS rule,
               c.confidence AS confidence,
               c.applied_count AS applied_count
        ORDER BY c.confidence DESC, c.timestamp DESC
        LIMIT $limit
        """
        try:
            async with self._driver.session() as session:
                result = await session.run(
                    query,
                    sender_email=sender_email,
                    intent_category=intent_category,
                    limit=limit,
                )
                records = [record.data() async for record in result]
        except Exception as exc:  # pragma: no cover - schema/driver fallback
            logger.warning("neo4j.corrections_failed", error=str(exc))
            return []

        return [
            CorrectionSummary(
                correction_id=record.get("correction_id", ""),
                category=record.get("category", ""),
                rule=record.get("rule", ""),
                confidence=record.get("confidence", 0.0) or 0.0,
                applied_count=record.get("applied_count", 0) or 0,
            )
            for record in records
        ]

    async def upsert_correction(
        self,
        sender: str,
        correction_id: str,
        category: str,
        rule: str,
        confidence: float,
    ) -> bool:
        """Store or update a correction in Neo4j.

        Args:
            sender: Sender email address.
            correction_id: Unique correction identifier.
            category: Correction category (e.g., "intent_correction").
            rule: The correction rule text.
            confidence: Confidence score (0.0 - 1.0).

        Returns:
            True if stored successfully, False on failure.
        """
        if self._driver is None:
            return False
        query = """
        MERGE (p:Person {email: $sender})
        MERGE (c:Correction {id: $correction_id})
        ON CREATE SET c.category = $category, c.structured_rule = $rule,
                      c.confidence = $confidence, c.timestamp = timestamp()
        ON MATCH SET c.category = $category, c.structured_rule = $rule,
                     c.confidence = $confidence
        MERGE (p)-[:HAS_CORRECTION]->(c)
        """
        try:
            async with self._driver.session() as session:
                await session.run(
                    query,
                    sender=sender,
                    correction_id=correction_id,
                    category=category,
                    rule=rule,
                    confidence=confidence,
                )
            logger.info("neo4j.upsert_correction", correction_id=correction_id)
            return True
        except Exception as exc:
            logger.warning("neo4j.upsert_failed", correction_id=correction_id, error=str(exc))
            return False


    async def fetch_sender_style(self, sender_email: str) -> "SenderStyle | None":
        """Fetch the stored SenderStyle for the given email, or None.

        Falls back to None when the driver is not connected or the sender has
        no Style node, mirroring the other fetch_* methods' resilience.
        """
        if self._driver is None:
            return None

        query = """
        MATCH (s:Sender {email: $email})-[:HAS_STYLE]->(st:Style)
        RETURN st.tone AS tone, st.signature AS signature,
               st.language AS language, st.greeting AS greeting,
               st.updated_at AS updated_at
        LIMIT 1
        """
        try:
            async with self._driver.session() as session:
                result = await session.run(query, email=sender_email)
                records = [record.data() async for record in result]
        except Exception as exc:  # pragma: no cover - schema/driver fallback
            logger.warning("neo4j.style_fetch_failed", sender=sender_email, error=str(exc))
            return None

        if not records:
            return None
        from praxis.features.sender_style import SenderStyle

        r = records[0]
        ts = r.get("updated_at")
        if isinstance(ts, str):
            try:
                ts = datetime.fromisoformat(ts)
            except ValueError:
                ts = datetime.now()
        elif ts is None:
            ts = datetime.now()
        return SenderStyle(
            tone=r.get("tone") or "professional",
            signature=r.get("signature") or "— PRAXIS",
            language=r.get("language"),
            greeting=r.get("greeting"),
            updated_at=ts,
        )

    async def upsert_sender_style(self, sender_email: str, style: "SenderStyle") -> None:
        """Upsert the SenderStyle for the given email.

        No-op when the driver is not connected (the brief returns None).
        """
        if self._driver is None:
            return None

        query = """
        MERGE (s:Sender {email: $email})
        MERGE (s)-[:HAS_STYLE]->(st:Style)
        SET st.tone = $tone,
            st.signature = $signature,
            st.language = $language,
            st.greeting = $greeting,
            st.updated_at = $updated_at
        """
        try:
            async with self._driver.session() as session:
                await session.run(
                    query,
                    email=sender_email,
                    tone=style.tone,
                    signature=style.signature,
                    language=style.language,
                    greeting=style.greeting,
                    updated_at=style.updated_at.isoformat(),
                )
            logger.info("neo4j.upsert_sender_style", sender=sender_email)
        except Exception as exc:
            logger.warning(
                "neo4j.sender_style_upsert_failed",
                sender=sender_email,
                error=str(exc),
            )


__all__ = ["Neo4jContextClient"]
