"""Tests for the AgentRegistry, AgentDelegator, and tool dispatch."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from praxis.models.schemas import AgentDelegationResult, AgentListingResult

# ── AgentRegistry tests ────────────────────────────────────────

def test_registry_loads_agents():
    """AgentRegistry loads agent files from the library."""
    from praxis.services.agent_registry import AgentRegistry

    registry = AgentRegistry()
    count = registry.load()
    assert count > 200  # We have 248 agents
    assert len(registry.divisions) >= 17


def test_registry_get_agent():
    """AgentRegistry.get() returns the correct agent by slug."""
    from praxis.services.agent_registry import AgentRegistry

    registry = AgentRegistry()
    registry.load()

    agent = registry.get("finance-financial-analyst")
    assert agent is not None
    assert agent.name == "Financial Analyst"
    assert agent.division == "finance"
    assert "financial" in agent.system_prompt.lower()
    assert len(agent.system_prompt) > 500


def test_registry_get_nonexistent_returns_none():
    """AgentRegistry.get() returns None for unknown slug."""
    from praxis.services.agent_registry import AgentRegistry

    registry = AgentRegistry()
    registry.load()
    assert registry.get("nonexistent-agent") is None


def test_registry_list_division():
    """AgentRegistry.list_division() returns agents in a division."""
    from praxis.services.agent_registry import AgentRegistry

    registry = AgentRegistry()
    registry.load()

    finance_agents = registry.list_division("finance")
    assert len(finance_agents) >= 4
    assert any(a.name == "Financial Analyst" for a in finance_agents)


def test_registry_search_by_keyword():
    """AgentRegistry.search() finds agents by keyword."""
    from praxis.services.agent_registry import AgentRegistry

    registry = AgentRegistry()
    registry.load()

    # Search for "invoice"
    results = registry.search("invoice")
    assert len(results) > 0
    # Should find the accounts-payable-agent
    assert any("payable" in a.slug or "invoice" in a.description.lower() for a in results)

    # Search for "security"
    results = registry.search("security")
    assert len(results) > 0
    assert any(a.division == "security" for a in results)


def test_registry_divisions_summary():
    """AgentRegistry.divisions_summary() returns division counts."""
    from praxis.services.agent_registry import AgentRegistry

    registry = AgentRegistry()
    registry.load()

    summary = registry.divisions_summary()
    assert len(summary) >= 17
    assert any(d["division"] == "finance" for d in summary)
    assert any(d["division"] == "engineering" for d in summary)


def test_agent_persona_has_system_prompt():
    """Each loaded agent has a non-empty system prompt."""
    from praxis.services.agent_registry import AgentRegistry

    registry = AgentRegistry()
    registry.load()

    for slug in ["finance-financial-analyst", "accounts-payable-agent",
                 "engineering-technical-writer"]:
        agent = registry.get(slug)
        assert agent is not None, f"Agent {slug} not found"
        assert len(agent.system_prompt) > 200, f"Agent {slug} has short system prompt"
        assert agent.description, f"Agent {slug} has no description"


# ── AgentDelegator tests ───────────────────────────────────────

@pytest.mark.asyncio
async def test_delegator_delegate_to_finance_analyst():
    """AgentDelegator.delegate() calls LLM with the agent's system prompt."""
    from praxis.services.agent_delegator import AgentDelegator
    from praxis.services.agent_registry import AgentRegistry

    registry = AgentRegistry()
    registry.load()

    mock_router = AsyncMock()
    mock_router.ainvoke = AsyncMock(return_value=MagicMock(content="Buy low, sell high."))

    delegator = AgentDelegator(registry=registry, router=mock_router)
    result = await delegator.delegate(
        "finance-financial-analyst",
        "What is the DCF valuation method?",
    )

    assert result["success"] is True
    assert result["agent_name"] == "Financial Analyst"
    assert "Buy low, sell high" in result["response"]
    # Verify the router was called with the agent's system prompt
    mock_router.ainvoke.assert_called_once()
    call_args = mock_router.ainvoke.call_args
    messages = call_args.kwargs.get("messages") or call_args[0][0]
    # First message should be SystemMessage with the agent's prompt
    assert "Financial Analyst" in str(messages[0].content) or "financial" in str(messages[0].content).lower()


@pytest.mark.asyncio
async def test_delegator_unknown_agent():
    """AgentDelegator returns error for unknown agent."""
    from praxis.services.agent_delegator import AgentDelegator
    from praxis.services.agent_registry import AgentRegistry

    registry = AgentRegistry()
    registry.load()
    delegator = AgentDelegator(registry=registry, router=AsyncMock())

    result = await delegator.delegate("nonexistent-agent", "task")
    assert result["success"] is False
    assert "not found" in result["response"].lower()


@pytest.mark.asyncio
async def test_delegator_with_context():
    """AgentDelegator passes context to the LLM."""
    from praxis.services.agent_delegator import AgentDelegator
    from praxis.services.agent_registry import AgentRegistry

    registry = AgentRegistry()
    registry.load()

    mock_router = AsyncMock()
    mock_router.ainvoke = AsyncMock(return_value=MagicMock(content="Analysis complete."))

    delegator = AgentDelegator(registry=registry, router=mock_router)
    result = await delegator.delegate(
        "finance-financial-analyst",
        task="Analyze this cash flow",
        context="Revenue: $1M, Expenses: $800K",
    )

    assert result["success"] is True
    # Verify context was included in the user message
    call_args = mock_router.ainvoke.call_args
    messages = call_args.kwargs.get("messages") or call_args[0][0]
    user_msg = str(messages[-1].content)
    assert "$1M" in user_msg or "Revenue" in user_msg


def test_delegator_list_agents_all():
    """AgentDelegator.list_agents() returns a list of agents."""
    from praxis.services.agent_delegator import AgentDelegator
    from praxis.services.agent_registry import AgentRegistry

    registry = AgentRegistry()
    registry.load()
    delegator = AgentDelegator(registry=registry)

    agents = delegator.list_agents(limit=5)
    assert len(agents) == 5
    assert all("slug" in a and "name" in a for a in agents)


def test_delegator_list_agents_by_division():
    """AgentDelegator.list_agents() filters by division."""
    from praxis.services.agent_delegator import AgentDelegator
    from praxis.services.agent_registry import AgentRegistry

    registry = AgentRegistry()
    registry.load()
    delegator = AgentDelegator(registry=registry)

    agents = delegator.list_agents(division="finance")
    assert len(agents) >= 4
    assert all(a["division"] == "finance" for a in agents)


def test_delegator_list_agents_by_keyword():
    """AgentDelegator.list_agents() searches by keyword."""
    from praxis.services.agent_delegator import AgentDelegator
    from praxis.services.agent_registry import AgentRegistry

    registry = AgentRegistry()
    registry.load()
    delegator = AgentDelegator(registry=registry)

    agents = delegator.list_agents(keyword="invoice")
    assert len(agents) > 0


# ── Schema validation ──────────────────────────────────────────

def test_agent_delegation_result_schema():
    """AgentDelegationResult validates with required fields."""
    result = AgentDelegationResult(
        agent_slug="finance-financial-analyst",
        agent_name="Financial Analyst",
        response="The DCF method is...",
    )
    assert result.tool_name == "delegate_to_agent"
    assert result.success is True
    assert result.agent_name == "Financial Analyst"


def test_agent_listing_result_schema():
    """AgentListingResult validates with required fields."""
    result = AgentListingResult(
        division="finance",
        agents=[{"slug": "test", "name": "Test"}],
    )
    assert result.tool_name == "list_agents"
    assert result.success is True
    assert len(result.agents) == 1


# ── Tool dispatch tests ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_execute_tool_delegate_to_agent_with_region():
    """Tool dispatch passes region to the delegator."""
    from praxis.graph.nodes import _execute_tool
    from praxis.models.schemas import AgentDelegationResult

    mock_delegator = AsyncMock()
    mock_delegator.delegate = AsyncMock(return_value={
        "agent_slug": "accounts-payable-agent",
        "agent_name": "Accounts Payable Agent",
        "response": "In the UK, VAT invoices must include a VAT number.",
        "success": True,
        "region": "United Kingdom",
    })

    name, result = await _execute_tool(
        {
            "name": "delegate_to_agent",
            "agent_slug": "accounts-payable-agent",
            "task": "What must an invoice include?",
            "region": "United Kingdom",
        },
        agent_delegator=mock_delegator,
    )

    assert name == "delegate_to_agent"
    assert isinstance(result, AgentDelegationResult)
    assert result.success is True
    assert result.region == "United Kingdom"
    assert "VAT" in result.response


@pytest.mark.asyncio
async def test_execute_tool_list_agents_with_region():
    """Tool dispatch passes region to list_agents."""
    from praxis.graph.nodes import _execute_tool
    from praxis.models.schemas import AgentListingResult

    mock_delegator = AsyncMock()
    mock_delegator.list_agents = MagicMock(return_value=[
        {"slug": "marketing-baidu-seo-specialist", "name": "Baidu SEO Specialist", "region": "China"},
    ])

    name, result = await _execute_tool(
        {"name": "list_agents", "keyword": "seo", "region": "China"},
        agent_delegator=mock_delegator,
    )

    assert name == "list_agents"
    assert isinstance(result, AgentListingResult)
    assert result.region == "China"
    assert len(result.agents) == 1
