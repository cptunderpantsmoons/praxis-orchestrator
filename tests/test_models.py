"""Tests for LangChain-compatible Umans chat model wrappers and router internals.

Brings coverage above the 85% QG1 threshold by testing:
- Model wrapper ainvoke with message conversion
- Factory functions for all three model families
- Sync _generate raises NotImplementedError
- Router _get_client, close, and _call_api paths
"""

import httpx
import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from praxis.router.concurrency import UmansConcurrencyRouter
from praxis.router.models import (
    UmansChatModel,
    create_glm_model,
    create_kimi_model,
    create_qwen_model,
)

# ── LangChain model wrapper tests ────────────────────────────────────


@pytest.mark.asyncio
async def test_kimi_model_ainvoke():
    """Kimi model wrapper routes through the concurrency router."""
    router = UmansConcurrencyRouter("http://test", "key")

    async def mock_call_api(model, messages, **kwargs):
        assert model == "umans-coder"
        return {"choices": [{"message": {"content": "Kimi response", "role": "assistant"}}]}

    router._call_api = mock_call_api

    model = create_kimi_model(router)
    result = await model.ainvoke([HumanMessage(content="Hello")])

    assert isinstance(result, AIMessage)
    assert result.content == "Kimi response"


@pytest.mark.asyncio
async def test_qwen_model_ainvoke():
    """Qwen model wrapper routes through the concurrency router."""
    router = UmansConcurrencyRouter("http://test", "key")

    async def mock_call_api(model, messages, **kwargs):
        assert model == "umans-flash"
        return {"choices": [{"message": {"content": "Qwen response", "role": "assistant"}}]}

    router._call_api = mock_call_api

    model = create_qwen_model(router)
    result = await model.ainvoke([HumanMessage(content="Hello")])

    assert isinstance(result, AIMessage)
    assert result.content == "Qwen response"


@pytest.mark.asyncio
async def test_glm_model_ainvoke():
    """GLM model wrapper routes through the concurrency router."""
    router = UmansConcurrencyRouter("http://test", "key")

    async def mock_call_api(model, messages, **kwargs):
        assert model == "umans-glm-5.2"
        return {"choices": [{"message": {"content": "GLM response", "role": "assistant"}}]}

    router._call_api = mock_call_api

    model = create_glm_model(router)
    result = await model.ainvoke([HumanMessage(content="Hello")])

    assert isinstance(result, AIMessage)
    assert result.content == "GLM response"


@pytest.mark.asyncio
async def test_model_passes_temperature():
    """Model wrapper passes configured temperature to the API call."""
    router = UmansConcurrencyRouter("http://test", "key")
    received: dict = {}

    async def mock_call_api(model, messages, **kwargs):
        received.update(kwargs)
        return {"choices": [{"message": {"content": "ok"}}]}

    router._call_api = mock_call_api

    model = create_qwen_model(router, temperature=0.7)
    await model.ainvoke([HumanMessage(content="Hello")])

    assert received.get("temperature") == 0.7


@pytest.mark.asyncio
async def test_model_passes_system_message():
    """System messages are correctly converted to the 'system' role."""
    router = UmansConcurrencyRouter("http://test", "key")
    received_messages: list = []

    async def mock_call_api(model, messages, **kwargs):
        received_messages.extend(messages)
        return {"choices": [{"message": {"content": "ok"}}]}

    router._call_api = mock_call_api

    model = create_kimi_model(router)
    await model.ainvoke(
        [
            SystemMessage(content="You are a helpful assistant."),
            HumanMessage(content="Hello"),
        ]
    )

    assert len(received_messages) == 2
    assert received_messages[0]["role"] == "system"
    assert received_messages[1]["role"] == "user"


def test_sync_generate_raises():
    """Sync _generate raises NotImplementedError (async-only model)."""
    router = UmansConcurrencyRouter("http://test", "key")
    model = create_kimi_model(router)

    with pytest.raises(NotImplementedError, match="async-only"):
        model._generate([HumanMessage(content="Hello")])


def test_llm_type():
    """_llm_type property returns 'umans'."""
    router = UmansConcurrencyRouter("http://test", "key")
    model = create_kimi_model(router)
    assert model._llm_type == "umans"


def test_model_is_umans_chat_model():
    """Factory functions return UmansChatModel instances."""
    router = UmansConcurrencyRouter("http://test", "key")
    assert isinstance(create_kimi_model(router), UmansChatModel)
    assert isinstance(create_qwen_model(router), UmansChatModel)
    assert isinstance(create_glm_model(router), UmansChatModel)


# ── Router internal method tests ─────────────────────────────────────


@pytest.mark.asyncio
async def test_get_client_creates_lazy():
    """_get_client creates an httpx.AsyncClient lazily."""
    router = UmansConcurrencyRouter("http://test", "key")
    assert router._client is None  # Initially None

    client = await router._get_client()
    assert client is not None
    assert not client.is_closed
    await client.aclose()


@pytest.mark.asyncio
async def test_close_releases_owned_client():
    """close() closes the HTTP client when owned by the router."""
    router = UmansConcurrencyRouter("http://test", "key")
    client = await router._get_client()
    assert not client.is_closed

    await router.close()
    assert client.is_closed


@pytest.mark.asyncio
async def test_close_does_not_close_injected_client():
    """close() does NOT close a client that was injected from outside."""
    injected = httpx.AsyncClient()
    router = UmansConcurrencyRouter("http://test", "key", client=injected)

    await router.close()
    assert not injected.is_closed  # Should still be open
    await injected.aclose()


@pytest.mark.asyncio
async def test_call_api_with_mock_transport():
    """_call_api makes an actual HTTP request via the injected client."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "transport mock"}}]},
        )

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    router = UmansConcurrencyRouter("http://api.test", "key", client=client)

    result = await router._call_api(
        "umans-flash", [{"role": "user", "content": "hi"}]
    )

    assert result["choices"][0]["message"]["content"] == "transport mock"
    await client.aclose()


@pytest.mark.asyncio
async def test_invoke_routes_through_call_api():
    """invoke() correctly calls _call_api with model and messages."""
    router = UmansConcurrencyRouter("http://test", "key")
    received: dict = {}

    async def mock_call_api(model, messages, **kwargs):
        received["model"] = model
        received["messages"] = messages
        received["kwargs"] = kwargs
        return {"choices": [{"message": {"content": "ok"}}]}

    router._call_api = mock_call_api

    result = await router.invoke(
        "umans-coder",
        [{"role": "user", "content": "test"}],
        temperature=0.5,
    )

    assert received["model"] == "umans-coder"
    assert received["messages"] == [{"role": "user", "content": "test"}]
    assert received["kwargs"]["temperature"] == 0.5
    assert result["choices"][0]["message"]["content"] == "ok"


@pytest.mark.asyncio
async def test_invoke_active_count_resets_after_completion():
    """active_counts returns to zero after all requests complete."""
    router = UmansConcurrencyRouter("http://test", "key")

    async def mock_call_api(model, messages, **kwargs):
        await __import__("asyncio").sleep(0.01)
        return {"choices": [{"message": {"content": "ok"}}]}

    router._call_api = mock_call_api

    await router.invoke("umans-flash", [{"role": "user", "content": "test"}])

    assert router.active_counts["qwen"] == 0


def test_reset_settings():
    """reset_settings() clears the cached settings singleton."""
    from praxis.config import get_settings, reset_settings

    s1 = get_settings()
    reset_settings()
    s2 = get_settings()

    assert s1 is not s2  # Different instances after reset
    assert s1.app_name == s2.app_name  # Same values
