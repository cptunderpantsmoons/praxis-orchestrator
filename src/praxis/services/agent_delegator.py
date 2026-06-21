"""Agent delegator — delegates tasks to specialized agency-agents.

Each delegation is a one-shot LLM call:
1. Look up the agent persona in the registry
2. Build a system prompt from the agent's markdown body
3. Make a single LLM call with the system prompt + task
4. Return the response to the caller

The delegator uses the existing UmansConcurrencyRouter to stay within
concurrency limits.

Region awareness: the delegator injects the user's region into the system
prompt and user message so that specialists adapt their advice to local
laws, regulations, taxes, and business norms.
"""
from __future__ import annotations

import logging
from typing import Any

from praxis.services.agent_registry import AgentPersona, AgentRegistry

logger = logging.getLogger(__name__)

# Model for delegation calls — use a fast model for quick expert consultations
# umans-flash is in the Qwen family with higher concurrency limits and faster response
DELEGATION_MODEL = "umans-flash"


class AgentDelegator:
    """Delegates tasks to specialized agency-agents via one-shot LLM calls.

    Usage:
        registry = AgentRegistry()
        registry.load()
        delegator = AgentDelegator(registry, router=umans_router)
        result = await delegator.delegate(
            "finance-financial-analyst",
            "Analyze this cash flow...",
            region="Australia",
        )
    """

    def __init__(
        self,
        registry: AgentRegistry,
        router: Any | None = None,
    ) -> None:
        self.registry = registry
        self.router = router

    async def delegate(
        self,
        agent_slug: str,
        task: str,
        context: str = "",
        region: str | None = None,
    ) -> dict[str, Any]:
        """Delegate a task to a specialized agent.

        Args:
            agent_slug: Agent identifier (e.g. 'finance-financial-analyst').
            task: The task/question to delegate.
            context: Optional additional context (email content, document text, etc.).
            region: Optional user region for region-aware advice (e.g. 'Australia').

        Returns:
            Dict with keys: agent_slug, agent_name, response, success, region.
        """
        persona = self.registry.get(agent_slug)
        if persona is None:
            return {
                "agent_slug": agent_slug,
                "agent_name": "",
                "response": f"Agent '{agent_slug}' not found. Use list_agents to see available agents.",
                "success": False,
                "region": region or "",
            }

        # Build the system prompt with region-aware instruction
        system_prompt = self._build_system_prompt(persona, region=region)

        # Build the user message with region and context
        user_message = task
        region_header = self._region_header(region)
        if context:
            user_message = f"{region_header}Context:\n{context}\n\nTask:\n{task}"
        elif region:
            user_message = f"{region_header}{task}"

        # Make the LLM call
        try:
            response = await self._call_llm(system_prompt, user_message)
            logger.info(
                "agent.delegated slug=%s name=%s region=%s response_chars=%d",
                agent_slug, persona.name, region or "unknown", len(response),
            )
            return {
                "agent_slug": agent_slug,
                "agent_name": persona.name,
                "response": response,
                "success": True,
                "region": region or "",
            }
        except Exception as exc:
            import traceback
            logger.warning(
                "agent.delegation_failed slug=%s error=%s\n%s",
                agent_slug, exc, traceback.format_exc(),
            )
            return {
                "agent_slug": agent_slug,
                "agent_name": persona.name,
                "response": f"Delegation failed: {type(exc).__name__}: {exc}",
                "success": False,
                "region": region or "",
            }

    def _build_system_prompt(self, persona: AgentPersona, region: str | None = None) -> str:
        """Build the system prompt for the LLM call.

        Truncates to 4000 chars to keep response times within timeout limits.
        Injects region-aware guidance so the agent adapts advice to the user's
        jurisdiction.
        """
        # Truncate to first 4000 chars — keeps identity, mission, and rules
        prompt_parts = [persona.system_prompt[:4000]]

        # Region-awareness instruction
        if region:
            region_text = (
                f"\n\n---\n\nREGION/LOCALIZATION INSTRUCTION: The user is in {region}. "
                f"Adapt your advice, examples, regulations, tax rules, and business norms "
                f"to be appropriate for {region}. If a recommendation depends on "
                f"jurisdiction-specific rules (e.g. tax law, securities regulation, "
                f"employment law), mention that it applies to {region}. "
                f"If you are not certain about {region} specifics, say so transparently "
                f"and provide internationally applicable guidance with a caveat."
            )
        else:
            region_text = (
                "\n\n---\n\nREGION/LOCALIZATION INSTRUCTION: The user's region is unknown. "
                "Provide guidance that is generally applicable internationally, but "
                "clearly flag where laws, regulations, taxes, or business norms vary by "
                "jurisdiction. If the user's question depends on a specific region, ask "
                "them to clarify their country/region for tailored advice."
            )
        prompt_parts.append(region_text)

        # Closing instruction
        prompt_parts.append(
            "\n\n---\n\nYou are being consulted as a specialist. Provide a focused, "
            "actionable response to the task below. Be concise but thorough. "
            "If the task is outside your expertise, say so clearly."
        )

        return "".join(prompt_parts)

    def _region_header(self, region: str | None) -> str:
        """Return a small region header to prepend to the user message."""
        if region:
            return f"Region: {region}\n\n"
        return ""

    async def _call_llm(self, system_prompt: str, user_message: str) -> str:
        """Make a one-shot LLM call using the Umans concurrency router."""
        from langchain_core.messages import HumanMessage, SystemMessage

        from praxis.chat.wrappers import UmansChatModel

        if self.router is None:
            raise RuntimeError("No UmansConcurrencyRouter available")

        model = UmansChatModel.create(DELEGATION_MODEL, router=self.router)

        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_message),
        ]

        response = await model.ainvoke(messages)

        # Extract text from the response
        if hasattr(response, "content"):
            return str(response.content)
        elif isinstance(response, str):
            return response
        else:
            return str(response)

    def list_agents(
        self,
        division: str | None = None,
        keyword: str | None = None,
        region: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """List available agents, optionally filtered by division, keyword, and region.

        When region is provided, region-specific agents for that region are boosted,
        and region-agnostic agents are also included.

        Returns a list of dicts with slug, name, emoji, division, description, region.
        """
        if division:
            agents = self.registry.list_division(division)
        elif keyword:
            agents = self.registry.search(keyword, region=region, limit=limit * 2)
        elif region:
            agents = self.registry.for_region(region, limit=limit * 2)
        else:
            agents = self.registry.list_all()[:limit]

        # Deduplicate and limit
        seen = set()
        unique = []
        for a in agents:
            if a.slug not in seen:
                seen.add(a.slug)
                unique.append(a)
            if len(unique) >= limit:
                break

        return [
            {
                "slug": a.slug,
                "name": a.name,
                "emoji": a.emoji,
                "division": a.division,
                "description": a.description[:120],
                "region": a.region,
            }
            for a in unique
        ]

    def get_divisions(self) -> list[dict[str, Any]]:
        """Get a summary of all divisions with agent counts."""
        return self.registry.divisions_summary()
