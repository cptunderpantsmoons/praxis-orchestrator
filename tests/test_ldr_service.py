"""Tests for the LDR (Local Deep Research) service and tool dispatch."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from praxis.models.schemas import LDRResult

# ── LdrService tests ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_research_returns_summary_and_sources():
    """LdrService.research returns the LDR response with summary, findings, sources."""
    from praxis.services.ldr_service import LdrService

    svc = LdrService(base_url="http://fake:5001", api_key="test_key")

    # Mock the httpx client
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "success": True,
        "result": {
            "summary": "Bitcoin halving reduces mining rewards by 50%.",
            "findings": ["Finding 1", "Finding 2"],
            "sources": [{"title": "Coinbase", "url": "https://coinbase.com"}],
            "iterations": 1,
        },
    }
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.is_closed = False
    mock_client.post = AsyncMock(return_value=mock_response)
    svc._client = mock_client

    result = await svc.research("What is Bitcoin halving?", mode="quick")

    assert "summary" in result
    assert "Bitcoin halving" in result["summary"]
    assert len(result["findings"]) == 2
    assert len(result["sources"]) == 1

    await svc.close()


@pytest.mark.asyncio
async def test_research_caps_searches_per_section():
    """LdrService.research caps searches_per_section at 2."""
    from praxis.services.ldr_service import LdrService

    svc = LdrService(base_url="http://fake:5001", api_key="test_key")

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"success": True, "result": {"summary": "test"}}
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.is_closed = False
    mock_client.post = AsyncMock(return_value=mock_response)
    svc._client = mock_client

    # Request 10 searches_per_section — should be capped to 2
    await svc.research("test query", mode="full", searches_per_section=10)

    call_args = mock_client.post.call_args
    payload = call_args.kwargs["json"]
    assert payload["searches_per_section"] == 2

    await svc.close()


@pytest.mark.asyncio
async def test_health_returns_false_on_connection_error():
    """LdrService.health returns False when the wrapper is unreachable."""
    from praxis.services.ldr_service import LdrService

    svc = LdrService(base_url="http://nonexistent:5001", api_key="test_key")

    # Mock httpx to raise a connection error
    mock_client = AsyncMock()
    mock_client.is_closed = False
    mock_client.get = AsyncMock(side_effect=Exception("Connection refused"))
    svc._client = mock_client

    result = await svc.health()
    assert result is False

    await svc.close()


# ── Tool dispatch tests ────────────────────────────────────────

@pytest.mark.asyncio
async def test_execute_tool_deep_research():
    """_execute_tool dispatches deep_research and returns LDRResult."""
    from praxis.graph.nodes import _execute_tool

    mock_ldr = AsyncMock()
    mock_ldr.research = AsyncMock(return_value={
        "summary": "Research summary here.",
        "findings": ["fact 1", "fact 2"],
        "sources": [{"title": "Source 1"}],
    })

    name, result = await _execute_tool(
        {"name": "deep_research", "query": "What is AI?", "mode": "quick"},
        ldr_service=mock_ldr,
    )

    assert name == "deep_research"
    assert isinstance(result, LDRResult)
    assert result.success is True
    assert result.query == "What is AI?"
    assert result.summary == "Research summary here."
    assert len(result.findings) == 2
    assert len(result.sources) == 1
    assert result.mode == "quick"


@pytest.mark.asyncio
async def test_execute_tool_deep_research_without_service():
    """_execute_tool returns graceful degradation when ldr_service is None."""
    from praxis.graph.nodes import _execute_tool

    name, result = await _execute_tool(
        {"name": "deep_research", "query": "test"},
        ldr_service=None,
    )

    assert name == "deep_research"
    assert isinstance(result, LDRResult)
    assert result.success is False
    assert "unavailable" in result.summary.lower()


@pytest.mark.asyncio
async def test_execute_tool_deep_research_on_failure():
    """_execute_tool returns error result when LDR research raises."""
    from praxis.graph.nodes import _execute_tool

    mock_ldr = AsyncMock()
    mock_ldr.research = AsyncMock(side_effect=RuntimeError("LDR crashed"))

    name, result = await _execute_tool(
        {"name": "deep_research", "query": "test"},
        ldr_service=mock_ldr,
    )

    assert name == "deep_research"
    assert isinstance(result, LDRResult)
    assert result.success is False
    assert "Research failed" in result.summary


# ── Schema validation ──────────────────────────────────────────

def test_ldr_result_schema():
    """LDRResult validates with required fields."""
    result = LDRResult(
        query="test query",
        summary="A summary",
        findings=["fact 1"],
        sources=[{"title": "src"}],
        mode="quick",
    )
    assert result.tool_name == "deep_research"
    assert result.success is True
    assert result.query == "test query"
    assert len(result.findings) == 1


def test_ldr_result_defaults():
    """LDRResult defaults are sensible."""
    result = LDRResult(query="test", summary="summary")
    assert result.findings == []
    assert result.sources == []
    assert result.mode == "quick"
    assert result.success is True


def test_ldr_result_accepts_dict_findings():
    """LDRResult accepts dict findings (as returned by LDR API)."""
    result = LDRResult(
        query="test",
        summary="summary",
        findings=[{"phase": "Iteration 1", "question": "What is X?"}],
        sources=[{"title": "src", "url": "http://example.com"}],
    )
    assert result.success is True
    assert len(result.findings) == 1
    assert isinstance(result.findings[0], dict)
