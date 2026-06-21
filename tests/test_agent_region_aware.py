"""Tests for region-aware agent selection and delegation."""


# ── Region detection tests ─────────────────────────────────────

def test_detect_region_china():
    """detect_region recognizes China signals."""
    from praxis.services.agent_registry import detect_region

    assert detect_region("China Market Localization Strategist") == "China"
    assert detect_region("Baidu SEO Specialist") == "China"


def test_detect_region_france():
    """detect_region recognizes French signals."""
    from praxis.services.agent_registry import detect_region

    assert detect_region("French Consulting Market") == "France"
    assert detect_region("France Tax Strategist") == "France"


def test_detect_region_uk():
    """detect_region recognizes UK signals, including multi-word phrases."""
    from praxis.services.agent_registry import detect_region

    assert detect_region("United Kingdom Sales Coach") == "United Kingdom"
    assert detect_region("British PR Specialist") == "United Kingdom"


def test_detect_region_australia_domain():
    """detect_region recognizes Australia from domain hints if present."""
    from praxis.services.agent_registry import detect_region

    assert detect_region("Australia Accountant") == "Australia"


def test_detect_region_unknown():
    """detect_region returns empty for no signal."""
    from praxis.services.agent_registry import detect_region

    assert detect_region("Generic Financial Analyst") == ""


def test_registry_detects_region_for_agents():
    """AgentRegistry assigns region to region-specific agents."""
    from praxis.services.agent_registry import AgentRegistry

    registry = AgentRegistry()
    registry.load()

    china_agent = registry.get("marketing-china-market-localization-strategist")
    assert china_agent is not None
    assert china_agent.region == "China"

    french_agent = registry.get("specialized-french-consulting-market")
    assert french_agent is not None
    assert french_agent.region == "France"

    korean_agent = registry.get("specialized-korean-business-navigator")
    assert korean_agent is not None
    assert korean_agent.region == "South Korea"


# ── Region-aware search/listing tests ────────────────────────────

def test_search_boosts_region_specific_agents():
    """AgentRegistry.search boosts agents that match the requested region."""
    from praxis.services.agent_registry import AgentRegistry

    registry = AgentRegistry()
    registry.load()

    # Searching for SEO with region=China should boost Baidu SEO Specialist
    results = registry.search("seo", region="China", limit=10)
    assert len(results) > 0
    # The top result should be a China-specific SEO agent if any
    top_regions = [a.region for a in results[:5]]
    assert any(r == "China" for r in top_regions), f"no China agent in top 5: {top_regions}"


def test_for_region_includes_exact_match_and_global():
    """AgentRegistry.for_region returns region-specific and region-agnostic agents."""
    from praxis.services.agent_registry import AgentRegistry

    registry = AgentRegistry()
    registry.load()

    china_agents = registry.for_region("China", limit=50)
    assert len(china_agents) > 0
    regions = {a.region for a in china_agents}
    assert regions <= {"China", "", "Global"}, f"unexpected regions: {regions}"

    # Should include the China market specialist
    assert any(a.region == "China" for a in china_agents)


# ── Region-aware delegator tests ─────────────────────────────────

def test_build_system_prompt_includes_region_instruction():
    """AgentDelegator._build_system_prompt includes region-aware guidance."""
    from praxis.services.agent_delegator import AgentDelegator
    from praxis.services.agent_registry import AgentRegistry

    registry = AgentRegistry()
    registry.load()
    delegator = AgentDelegator(registry=registry)

    persona = registry.get("finance-financial-analyst")
    system_prompt = delegator._build_system_prompt(persona, region="Australia")

    assert "REGION/LOCALIZATION INSTRUCTION" in system_prompt
    assert "user is in Australia" in system_prompt
    assert "Australia" in system_prompt


def test_build_system_prompt_warns_when_region_unknown():
    """AgentDelegator._build_system_prompt asks to clarify region when unknown."""
    from praxis.services.agent_delegator import AgentDelegator
    from praxis.services.agent_registry import AgentRegistry

    registry = AgentRegistry()
    registry.load()
    delegator = AgentDelegator(registry=registry)

    persona = registry.get("finance-financial-analyst")
    system_prompt = delegator._build_system_prompt(persona, region=None)

    assert "user's region is unknown" in system_prompt
    assert "clarify" in system_prompt.lower()


def test_list_agents_returns_region_field():
    """AgentDelegator.list_agents returns region for each agent."""
    from praxis.services.agent_delegator import AgentDelegator
    from praxis.services.agent_registry import AgentRegistry

    registry = AgentRegistry()
    registry.load()
    delegator = AgentDelegator(registry=registry)

    agents = delegator.list_agents(division="marketing", region="China", limit=10)
    assert any(a.get("region") == "China" for a in agents)
    assert all("region" in a for a in agents)


def test_list_agents_by_region_only():
    """AgentDelegator.list_agents can list agents applicable to a region."""
    from praxis.services.agent_delegator import AgentDelegator
    from praxis.services.agent_registry import AgentRegistry

    registry = AgentRegistry()
    registry.load()
    delegator = AgentDelegator(registry=registry)

    agents = delegator.list_agents(region="China", limit=20)
    assert len(agents) > 0
    assert any(a.get("region") == "China" for a in agents)
