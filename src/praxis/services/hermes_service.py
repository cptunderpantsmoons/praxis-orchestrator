"""Hermes: stateful memory & learning service (REQ-309).

Native async wrappers that provide the enterprise memory core. Hermes
orchestrates across Neo4j (structured relationships, corrections) and Qdrant
(semantic vector recall), using Umans LLM for synthesis and learning.

Three key operations (per DRD §2.2.1):

- ``recall(query, context)`` — hybrid search across Neo4j graph paths and
  Qdrant vector matches, synthesised into a concise context summary by
  ``umans-glm-5.2``.
- ``store(fact, metadata)`` — persists a new fact to Neo4j (as a ``Fact``
  node linked to ``Person``/``Organization``) and Qdrant (as an embedding).
- ``learn(correction_text)`` — uses ``umans-coder`` to parse unstructured
  correction text into structured facts, then upserts corrections to Neo4j.

All operations use the shared ``UmansConcurrencyRouter`` for LLM calls so
semaphore limits are respected globally.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

import structlog

from praxis.config import Settings, get_settings

logger = structlog.get_logger()


class HermesService:
    """Native async Hermes memory & learning service.

    Wraps Neo4j (graph) and Qdrant (vector) into a unified memory API.
    LLM synthesis/learning goes through the shared UmansConcurrencyRouter.

    The service is designed for dependency injection: callers (tests) can
    pass mock neo4j_client, qdrant_client, and router instances. In
    production, these are created lazily from settings.
    """

    # Model assignments per DRD §2.2.1
    RECALL_MODEL = "umans-glm-5.2"
    LEARN_MODEL = "umans-coder"

    def __init__(
        self,
        settings: Settings | None = None,
        neo4j_client: Any | None = None,
        qdrant_client: Any | None = None,
        router: Any | None = None,
        embedding_service: Any | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._neo4j = neo4j_client
        self._qdrant = qdrant_client
        self._router = router
        self._embedding_service = embedding_service
        self._owns_neo4j = neo4j_client is None
        self._owns_qdrant = qdrant_client is None

    # ── Public API ────────────────────────────────────────────

    async def recall(
        self,
        query: str,
        context: dict[str, Any] | None = None,
        limit: int = 5,
    ) -> dict[str, Any]:
        """Retrieve relevant memory across Neo4j + Qdrant and synthesise.

        Performs hybrid search:
        1. Neo4j: fetch sender history and corrections for the context's
           sender (if provided).
        2. Qdrant: semantic search for senders similar to the query.
        3. Umans LLM (umans-glm-5.2): synthesise the raw results into a
           concise context summary for the ReAct loop.

        Returns a dict with keys: ``context_summary``, ``graph_paths``,
        ``vector_matches``. On any backend failure, returns a degraded
        summary with whatever partial data was collected — never raises.
        """
        context = context or {}
        graph_paths: list[str] = []
        vector_matches: list[str] = []

        # 1. Neo4j graph recall
        sender = context.get("sender", "")
        if sender:
            neo4j = await self._get_neo4j()
            if neo4j is not None:
                try:
                    history = await neo4j.fetch_sender_history(sender, limit=limit)
                    for t in history:
                        graph_paths.append(
                            f"Thread: {t.subject} ({t.timestamp.isoformat()})"
                        )
                    corrections = await neo4j.fetch_relevant_corrections(
                        sender, context.get("intent", "general"), limit=limit
                    )
                    for c in corrections:
                        graph_paths.append(f"Correction: {c.rule} (conf={c.confidence})")
                except Exception as exc:
                    logger.warning("hermes.recall_neo4j_failed", error=str(exc))

        # 2. Qdrant vector recall
        qdrant = await self._get_qdrant()
        if qdrant is not None:
            try:

                es = await self._get_embedding_service()
                vector = await es.embed_sender(query)
                similar = await qdrant.get_similar_senders(vector, limit=limit)
                for s in similar:
                    email = s.get("payload", {}).get("email", "unknown")
                    score = s.get("score", 0.0)
                    vector_matches.append(f"{email} (similarity={score:.2f})")
            except Exception as exc:
                logger.warning("hermes.recall_qdrant_failed", error=str(exc))

        # 3. LLM synthesis
        context_summary = await self._synthesise_recall(
            query, graph_paths, vector_matches
        )

        logger.info(
            "hermes.recall",
            query=query[:80],
            graph_paths=len(graph_paths),
            vector_matches=len(vector_matches),
        )
        return {
            "context_summary": context_summary,
            "graph_paths": graph_paths,
            "vector_matches": vector_matches,
        }

    async def store(
        self,
        fact: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Persist a new fact to Neo4j and Qdrant.

        Creates a ``Fact`` node in Neo4j linked to the sender (if known)
        and upserts an embedding in Qdrant's sender collection.

        Returns a dict with ``stored_neo4j``, ``stored_qdrant``, ``fact_id``.
        """
        metadata = metadata or {}
        fact_id = str(uuid.uuid4())
        now = datetime.now(UTC).isoformat()
        stored_neo4j = False
        stored_qdrant = False

        # 1. Neo4j: create Fact node linked to Person
        sender = metadata.get("sender", "")
        neo4j = await self._get_neo4j()
        if neo4j is not None and sender:
            try:
                query = """
                MERGE (p:Person {email: $sender})
                CREATE (f:Fact {id: $fact_id, text: $fact, timestamp: $ts, source: $source})
                MERGE (p)-[:KNOWS_FACT]->(f)
                """
                if neo4j._driver is not None:
                    async with neo4j._driver.session() as session:
                        await session.run(
                            query,
                            sender=sender,
                            fact_id=fact_id,
                            fact=fact,
                            ts=now,
                            source=metadata.get("source", "hermes_store"),
                        )
                    stored_neo4j = True
                    logger.info("hermes.store_neo4j", fact_id=fact_id)
            except Exception as exc:
                logger.warning("hermes.store_neo4j_failed", error=str(exc))

        # 2. Qdrant: embed and upsert
        qdrant = await self._get_qdrant()
        if qdrant is not None:
            try:

                es = await self._get_embedding_service()
                vector = await es.embed_sender(fact)
                ok = await qdrant.upsert_sender(
                    sender or f"fact:{fact_id}",
                    vector,
                    metadata={"fact_id": fact_id, "fact": fact, "stored_at": now},
                )
                stored_qdrant = ok
                logger.info("hermes.store_qdrant", fact_id=fact_id, ok=ok)
            except Exception as exc:
                logger.warning("hermes.store_qdrant_failed", error=str(exc))

        logger.info(
            "hermes.store",
            fact_id=fact_id,
            neo4j=stored_neo4j,
            qdrant=stored_qdrant,
        )
        return {
            "stored_neo4j": stored_neo4j,
            "stored_qdrant": stored_qdrant,
            "fact_id": fact_id,
        }

    async def learn(self, correction_text: str) -> dict[str, Any]:
        """Parse unstructured correction text into structured facts.

        Uses ``umans-coder`` to extract structured correction rules from
        free-text feedback (e.g. "Correction: The client prefers PDF
        invoices"), then upserts each extracted correction to Neo4j.

        Returns a dict with ``extracted_facts`` (list of rule strings) and
        ``corrections_applied`` (count of successful Neo4j upserts).
        """
        extracted_facts = await self._extract_corrections(correction_text)
        corrections_applied = 0

        neo4j = await self._get_neo4j()
        if neo4j is not None:
            for i, fact in enumerate(extracted_facts):
                correction_id = str(uuid.uuid4())
                try:
                    ok = await neo4j.upsert_correction(
                        sender="system",
                        correction_id=correction_id,
                        category="hermes_learn",
                        rule=fact,
                        confidence=0.8,
                    )
                    if ok:
                        corrections_applied += 1
                except Exception as exc:
                    logger.warning(
                        "hermes.learn_upsert_failed",
                        correction_id=correction_id,
                        error=str(exc),
                    )

        logger.info(
            "hermes.learn",
            extracted=len(extracted_facts),
            applied=corrections_applied,
        )
        return {
            "extracted_facts": extracted_facts,
            "corrections_applied": corrections_applied,
        }

    async def close(self) -> None:
        """Release any owned resources."""
        if self._owns_qdrant and self._qdrant is not None:
            try:
                await self._qdrant.close()
            except Exception:
                pass
        if self._owns_neo4j and self._neo4j is not None:
            try:
                await self._neo4j.close()
            except Exception:
                pass

    # ── Internal helpers ──────────────────────────────────────

    async def _get_neo4j(self) -> Any | None:
        if self._neo4j is None:
            try:
                from praxis.services.neo4j_client import Neo4jContextClient

                self._neo4j = Neo4jContextClient(settings=self._settings)
                await self._neo4j.connect()
            except Exception as exc:
                logger.warning("hermes.neo4j_unavailable", error=str(exc))
                self._owns_neo4j = False
                return None
        return self._neo4j

    async def _get_qdrant(self) -> Any | None:
        if self._qdrant is None:
            try:
                from praxis.services.qdrant_client import QdrantSenderClient

                self._qdrant = QdrantSenderClient()
            except Exception as exc:
                logger.warning("hermes.qdrant_unavailable", error=str(exc))
                self._owns_qdrant = False
                return None
        return self._qdrant

    async def _get_embedding_service(self) -> Any:
        if self._embedding_service is None:
            from praxis.services.embedding_service import EmbeddingService

            self._embedding_service = EmbeddingService()
        return self._embedding_service

    async def _get_router(self) -> Any:
        if self._router is None:
            from praxis.router import UmansConcurrencyRouter

            self._router = UmansConcurrencyRouter(settings=self._settings)
        return self._router

    async def _synthesise_recall(
        self,
        query: str,
        graph_paths: list[str],
        vector_matches: list[str],
    ) -> str:
        """Use umans-glm-5.2 to synthesise raw results into a summary."""
        if not graph_paths and not vector_matches:
            return "No relevant memory found for this query."

        raw_context = json.dumps(
            {"query": query, "graph_paths": graph_paths, "vector_matches": vector_matches},
            indent=2,
        )

        prompt = (
            "You are a memory synthesis engine. Given the following raw memory "
            "results from a hybrid graph+vector search, produce a concise (2-4 "
            "sentence) context summary that the ReAct agent can use to respond "
            "to the user's email. Focus on actionable facts, corrections, and "
            "sender history. Do not mention the search mechanism.\n\n"
            f"RAW RESULTS:\n{raw_context}\n\n"
            "CONCISE SUMMARY:"
        )

        try:
            router = await self._get_router()
            from praxis.chat.wrappers import UmansChatModel

            model = UmansChatModel.create(self.RECALL_MODEL, router=router)
            result = model.ainvoke([("human", prompt)])
            response = result if not _is_coro(result) else await result
            content = response.content if isinstance(response.content, str) else str(response.content)
            return content.strip() or "Memory retrieved but synthesis produced no output."
        except Exception as exc:
            logger.warning("hermes.synthesis_failed", error=str(exc))
            # Fallback: concatenate raw results
            parts = graph_paths + vector_matches
            return "Retrieved memory (unsynthesised): " + " | ".join(parts[:5])

    async def _extract_corrections(self, correction_text: str) -> list[str]:
        """Use umans-coder to parse correction text into structured rules."""
        prompt = (
            "Extract structured correction rules from the following user feedback. "
            "Return a JSON array of strings, each being a concise rule.\n"
            "Example input: 'Correction: The client prefers PDF invoices and wants "
            "replies within 2 hours.'\n"
            'Example output: ["Client prefers PDF invoices", "Client expects replies '
            'within 2 hours"]\n\n'
            f"FEEDBACK:\n{correction_text}\n\n"
            "JSON ARRAY:"
        )

        try:
            router = await self._get_router()
            from praxis.chat.wrappers import UmansChatModel

            model = UmansChatModel.create(self.LEARN_MODEL, router=router)
            result = model.ainvoke([("human", prompt)])
            response = result if not _is_coro(result) else await result
            content = response.content if isinstance(response.content, str) else str(response.content)

            # Parse JSON array from response — LLMs sometimes wrap the
            # array in markdown code fences or add prose around it.
            import re

            # Strip markdown code fences if present
            content_clean = re.sub(r"```(?:json)?\s*", "", content).strip()
            match = re.search(r'\[.*?\]', content_clean, re.DOTALL)
            if match:
                try:
                    facts = json.loads(match.group())
                    return [str(f) for f in facts if f]
                except json.JSONDecodeError:
                    pass  # fall through to line-based parsing

            # Fallback: split by newlines if no JSON array found
            lines = [l.strip().lstrip("-•*123456789. )") for l in content.splitlines() if l.strip()]
            return lines[:10]
        except Exception as exc:
            logger.warning("hermes.extract_failed", error=str(exc))
            # Fallback: treat the whole text as a single correction
            return [correction_text.strip()]


def _is_coro(obj: Any) -> bool:
    import asyncio

    return asyncio.iscoroutine(obj)


__all__ = ["HermesService"]
