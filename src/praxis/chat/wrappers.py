"""LangChain-compatible chat model wrappers for Umans models.

Each wrapper extends ``langchain_core.language_models.chat_models.BaseChatModel``
and routes every request through the shared ``UmansConcurrencyRouter`` to
enforce per-model-family semaphore limits.

Usage::

    router = UmansConcurrencyRouter()
    model = UmansChatModel.create("umans-flash", router=router)
    response = await model.ainvoke([HumanMessage(content="Hello")])
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import structlog
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    ChatMessage,
    HumanMessage,
    SystemMessage,
)
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import ConfigDict

from praxis.config import get_settings
from praxis.models.umans import get_model_config
from praxis.router import UmansConcurrencyRouter

logger = structlog.get_logger()

# LangChain message type -> OpenAI API role
_ROLE_MAP: dict[type[BaseMessage], str] = {
    HumanMessage: "user",
    AIMessage: "assistant",
    SystemMessage: "system",
    ChatMessage: "function",
}


class UmansChatModel(BaseChatModel):
    """LangChain-compatible chat model backed by the Umans API.

    All invocations acquire a semaphore from the ``UmansConcurrencyRouter``
    before making the HTTP call, ensuring strict concurrency enforcement.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    # Model identification
    model_name: str = "umans-flash"
    family: str = "qwen"

    # Generation parameters
    max_tokens: int = 4096
    temperature: float = 0.0
    context_window: int = 131_072

    # Dependencies — use Any type to avoid Pydantic is_instance_of validation
    # rejecting MagicMock during tests; validated at call time instead.
    router: Any = None

    # ── Factory ──────────────────────────────────────────────────

    @classmethod
    def create(
        cls,
        model_name: str,
        router: UmansConcurrencyRouter,
        **overrides: Any,
    ) -> UmansChatModel:
        """Create a wrapper from the model registry + shared router.

        Context windows are read from environment settings so the GLM-5.2
        1,000,000-token context can be adjusted without code changes.
        """
        config = get_model_config(model_name)
        settings = get_settings()

        # Allow env-var override of context windows
        context_window = config.context_window
        if config.family == "glm":
            context_window = settings.umans_glm_context_window
        elif config.family == "kimi":
            context_window = settings.umans_kimi_context_window
        elif config.family == "qwen":
            context_window = settings.umans_qwen_context_window

        return cls(
            model_name=model_name,
            family=config.family,
            max_tokens=config.max_tokens,
            temperature=config.temperature,
            context_window=context_window,
            router=router,
            **overrides,
        )

    # ── BaseChatModel implementation ─────────────────────────────

    @property
    def _llm_type(self) -> str:
        return "umans"

    @property
    def _identifying_params(self) -> dict[str, Any]:
        return {
            "model_name": self.model_name,
            "family": self.family,
            "context_window": self.context_window,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
        }

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        """Synchronous generation — runs the async path via asyncio.run."""
        return asyncio.run(self._agenerate(messages, stop=stop, **kwargs))

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        """Async generation — routes through the concurrency router."""
        if self.router is None:
            msg = (
                "UmansChatModel requires a UmansConcurrencyRouter. "
                "Pass one via create() or the router= parameter."
            )
            raise RuntimeError(msg)

        api_messages = [self._convert_message(m) for m in messages]

        payload_kwargs: dict[str, Any] = {
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
        }
        if stop:
            payload_kwargs["stop"] = stop
        payload_kwargs.update(kwargs)

        # Retry with exponential backoff on transient failures.
        max_retries = 3
        base_delay = 1.0
        last_exception: Exception | None = None
        for attempt in range(max_retries):
            try:
                response = await self.router.invoke(
                    model_name=self.model_name,
                    messages=api_messages,
                    **payload_kwargs,
                )
                return self._create_chat_result(response)
            except (httpx.HTTPStatusError, httpx.TimeoutException, httpx.NetworkError) as exc:
                last_exception = exc
                if attempt == max_retries - 1:
                    break
                delay = base_delay * (2 ** attempt)
                logger.warning(
                    "umans.generate_retry",
                    model=self.model_name,
                    attempt=attempt + 1,
                    max_retries=max_retries,
                    error=str(exc),
                    delay=delay,
                )
                await asyncio.sleep(delay)
            except Exception as exc:
                last_exception = exc
                break

        raise last_exception or RuntimeError("Umans generation failed")

    # ── Helpers ───────────────────────────────────────────────────

    @staticmethod
    def _convert_message(message: BaseMessage) -> dict[str, str]:
        """Convert a LangChain message to the OpenAI API dict format."""
        role = _ROLE_MAP.get(type(message), "user")
        if isinstance(message, ChatMessage):
            role = message.role
        content = message.content if isinstance(message.content, str) else str(message.content)
        return {"role": role, "content": content}

    @staticmethod
    def _create_chat_result(response: dict[str, Any]) -> ChatResult:
        """Parse the Umans (OpenAI-compatible) response into a ChatResult."""
        choices = response.get("choices", [])
        if not choices:
            msg = "Umans API returned no choices"
            raise ValueError(msg)

        choice = choices[0]
        msg_data = choice.get("message", {})
        content = msg_data.get("content", "") or ""
        # If content is None or non-string (e.g. tool_calls-only response), default to empty string
        if not isinstance(content, str):
            content = ""
        finish_reason = choice.get("finish_reason")

        ai_message = AIMessage(content=content)
        generation_info: dict[str, Any] | None = (
            {"finish_reason": finish_reason} if finish_reason else None
        )
        generation = ChatGeneration(
            message=ai_message,
            generation_info=generation_info,
        )

        token_usage = response.get("usage", {})
        llm_output: dict[str, Any] = {"model": response.get("model", "")}
        if token_usage:
            llm_output["token_usage"] = token_usage

        return ChatResult(generations=[generation], llm_output=llm_output)
