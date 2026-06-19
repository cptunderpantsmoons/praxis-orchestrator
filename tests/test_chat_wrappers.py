"""Tests for LangChain-compatible chat model wrappers.

Verifies the GLM-5.2 1,000,000-token context window adjustment and
confirms that wrappers route through the concurrency router.
"""

import httpx
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from praxis.chat.wrappers import UmansChatModel
from praxis.router import UmansConcurrencyRouter

# ── Context Window Configuration ───────────────────────────────


def test_glm_context_window_is_1m(test_settings, mock_transport):
    """GLM-5.2 chat model is configured with 1,000,000 token context window."""
    client = httpx.AsyncClient(transport=mock_transport, base_url="http://test")
    router = UmansConcurrencyRouter(settings=test_settings, http_client=client)
    model = UmansChatModel.create("umans-glm-5.2", router=router)

    assert model.context_window == 1_000_000
    assert model.model_name == "umans-glm-5.2"
    assert model.family == "glm"


def test_qwen_context_window(test_settings, mock_transport):
    """Qwen (umans-flash) chat model has correct context window."""
    client = httpx.AsyncClient(transport=mock_transport, base_url="http://test")
    router = UmansConcurrencyRouter(settings=test_settings, http_client=client)
    model = UmansChatModel.create("umans-flash", router=router)

    assert model.context_window == 131_072
    assert model.family == "qwen"


def test_kimi_context_window(test_settings, mock_transport):
    """Kimi (umans-coder) chat model has correct context window."""
    client = httpx.AsyncClient(transport=mock_transport, base_url="http://test")
    router = UmansConcurrencyRouter(settings=test_settings, http_client=client)
    model = UmansChatModel.create("umans-coder", router=router)

    assert model.context_window == 131_072
    assert model.family == "kimi"


def test_chat_model_llm_type():
    """The LLM type is 'umans'."""
    model = UmansChatModel(model_name="umans-flash", family="qwen")
    assert model._llm_type == "umans"


def test_chat_model_identifying_params():
    """Identifying params include model name, family, and context window."""
    model = UmansChatModel(
        model_name="umans-glm-5.2",
        family="glm",
        context_window=1_000_000,
    )
    params = model._identifying_params
    assert params["model_name"] == "umans-glm-5.2"
    assert params["family"] == "glm"
    assert params["context_window"] == 1_000_000


# ── Async Invocation ───────────────────────────────────────────


async def test_chat_model_routes_through_router(
    test_settings,
    mock_transport,
    mock_umans_response,
):
    """ainvoke() routes through the concurrency router and returns an AIMessage."""
    client = httpx.AsyncClient(transport=mock_transport, base_url="http://test")
    router = UmansConcurrencyRouter(settings=test_settings, http_client=client)
    model = UmansChatModel.create("umans-flash", router=router)

    response = await model.ainvoke([HumanMessage(content="Hello")])

    assert isinstance(response, AIMessage)
    assert response.content == "Test response"
    assert router.get_peak("umans-flash") == 1


async def test_chat_model_with_system_message(
    test_settings,
    mock_transport,
    mock_umans_response,
):
    """ainvoke() correctly converts system messages to API format."""
    client = httpx.AsyncClient(transport=mock_transport, base_url="http://test")
    router = UmansConcurrencyRouter(settings=test_settings, http_client=client)
    model = UmansChatModel.create("umans-coder", router=router)

    response = await model.ainvoke(
        [
            SystemMessage(content="You are a helpful assistant."),
            HumanMessage(content="Hello"),
        ]
    )

    assert isinstance(response, AIMessage)
    assert response.content == "Test response"
    assert router.get_peak("umans-coder") == 1


async def test_chat_model_raises_without_router():
    """Calling ainvoke() without a router raises RuntimeError."""
    model = UmansChatModel(model_name="umans-flash", family="qwen")

    import pytest

    with pytest.raises(RuntimeError, match="requires a UmansConcurrencyRouter"):
        await model.ainvoke([HumanMessage(content="Hello")])
