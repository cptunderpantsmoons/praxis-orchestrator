"""LangChain-compatible chat model wrappers for Umans models.

Each wrapper routes inference requests through the :class:`UmansConcurrencyRouter`,
enforcing per-model-family semaphore limits while presenting a standard
LangChain ``BaseChatModel`` interface for use with LangGraph.
"""

from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from praxis.router.concurrency import UmansConcurrencyRouter

# LangChain message type → OpenAI/Umans role
_ROLE_MAP: dict[str, str] = {
    "human": "user",
    "ai": "assistant",
    "system": "system",
    "tool": "tool",
    "function": "function",
}


class UmansChatModel(BaseChatModel):
    """LangChain-compatible chat model that routes through UmansConcurrencyRouter.

    All inference requests acquire the per-model-family semaphore before
    making the HTTP call, ensuring concurrency limits are strictly enforced.

    Use the factory functions :func:`create_kimi_model`,
    :func:`create_qwen_model`, and :func:`create_glm_model` for convenience.
    """

    model_name: str
    router: UmansConcurrencyRouter
    temperature: float = 0.0
    max_tokens: int | None = None

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        raise NotImplementedError(
            "UmansChatModel is async-only. Use agenerate() or ainvoke()."
        )

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        # Convert LangChain messages to OpenAI-compatible dicts
        lc_messages = [
            {"role": _ROLE_MAP.get(msg.type, msg.type), "content": msg.content}
            for msg in messages
        ]

        extra: dict[str, Any] = {"temperature": self.temperature}
        if self.max_tokens is not None:
            extra["max_tokens"] = self.max_tokens
        if stop is not None:
            extra["stop"] = stop
        extra.update(kwargs)

        response = await self.router.invoke(
            model=self.model_name,
            messages=lc_messages,
            **extra,
        )

        choices = response.get("choices", [{}])
        content = choices[0].get("message", {}).get("content", "")
        ai_message = AIMessage(content=content)
        generation = ChatGeneration(message=ai_message)
        return ChatResult(generations=[generation])

    @property
    def _llm_type(self) -> str:
        return "umans"


# ── Factory functions ────────────────────────────────────────────────


def create_kimi_model(router: UmansConcurrencyRouter, **kwargs: Any) -> UmansChatModel:
    """Create a wrapper for ``umans-coder`` (Kimi family, limit 4)."""
    return UmansChatModel(model_name="umans-coder", router=router, **kwargs)


def create_qwen_model(router: UmansConcurrencyRouter, **kwargs: Any) -> UmansChatModel:
    """Create a wrapper for ``umans-flash`` (Qwen family, limit 8)."""
    return UmansChatModel(model_name="umans-flash", router=router, **kwargs)


def create_glm_model(router: UmansConcurrencyRouter, **kwargs: Any) -> UmansChatModel:
    """Create a wrapper for ``umans-glm-5.2`` (GLM family, limit 4)."""
    return UmansChatModel(model_name="umans-glm-5.2", router=router, **kwargs)
