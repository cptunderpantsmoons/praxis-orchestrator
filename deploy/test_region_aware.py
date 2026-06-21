import asyncio
import sys

sys.path.insert(0, "/app/src")

async def main():
    from praxis.graph.nodes import _execute_tool
    from praxis.services.agent_registry import AgentRegistry
    from praxis.services.agent_delegator import AgentDelegator
    from praxis.router import UmansConcurrencyRouter
    from praxis.config import get_settings
    
    settings = get_settings()
    router = UmansConcurrencyRouter(settings=settings)
    registry = AgentRegistry()
    registry.load()
    delegator = AgentDelegator(registry=registry, router=router)
    
    # Test 1: list_agents by keyword + region boosts China SEO
    print(">>> list_agents(keyword='seo', region='China')")
    name, result = await _execute_tool(
        {"name": "list_agents", "keyword": "seo", "region": "China"},
        agent_delegator=delegator,
    )
    for a in result.agents[:5]:
        print(f"  {a.get('emoji','')} {a.get('slug','')} [{a.get('region','')}]")
    
    # Test 2: region-aware delegation to AP agent for Australia
    print("\n>>> delegate_to_agent('accounts-payable-agent', region='Australia')")
    name, result = await _execute_tool(
        {
            "name": "delegate_to_agent",
            "agent_slug": "accounts-payable-agent",
            "task": "What is a tax invoice requirement for small businesses? Be brief.",
            "region": "Australia",
        },
        agent_delegator=delegator,
    )
    print(f"Agent: {result.agent_name}")
    print(f"Success: {result.success}")
    print(f"Region: {result.region}")
    print(f"Response ({len(result.response)} chars):")
    print(result.response[:700])
    
    # Test 3: region-aware delegation for UK
    print("\n>>> delegate_to_agent('finance-tax-strategist', region='United Kingdom')")
    name, result = await _execute_tool(
        {
            "name": "delegate_to_agent",
            "agent_slug": "finance-tax-strategist",
            "task": "What is the VAT threshold? Keep it under 3 sentences.",
            "region": "United Kingdom",
        },
        agent_delegator=delegator,
    )
    print(f"Agent: {result.agent_name}")
    print(f"Success: {result.success}")
    print(f"Region: {result.region}")
    print(f"Response ({len(result.response)} chars):")
    print(result.response[:500])

asyncio.run(main())
