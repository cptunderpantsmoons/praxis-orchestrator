"""Umans model registry and configuration.

Defines the Umans models, their concurrency limits, context windows,
and routing families. The GLM-5.2 model is configured with a 1,000,000-token
context window per project requirements.

Semaphore limits enforced by UmansConcurrencyRouter:
    Kimi family (umans-coder)     -> 4 concurrent calls
    GLM  family (umans-glm-5.2)  -> 4 concurrent calls
    Qwen family (umans-flash)     -> 8 concurrent calls
    Embed family (umans-embed-*)  -> 8 concurrent calls
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class UmansModelConfig:
    """Static configuration for a single Umans inference model."""

    name: str
    family: str  # "kimi", "glm", "qwen", "embed"
    concurrency_limit: int
    context_window: int
    max_tokens: int
    temperature: float
    description: str


# ── Model Registry ─────────────────────────────────────────────

UMANS_MODELS: dict[str, UmansModelConfig] = {
    "umans-flash": UmansModelConfig(
        name="umans-flash",
        family="qwen",
        concurrency_limit=8,
        context_window=131_072,
        max_tokens=8192,
        temperature=0.0,
        description="High-throughput model for triage, PII extraction, AP tools",
    ),
    "umans-coder": UmansModelConfig(
        name="umans-coder",
        family="kimi",
        concurrency_limit=4,
        context_window=131_072,
        max_tokens=4096,
        temperature=0.0,
        description="Reasoning model for ReAct orchestration and complex synthesis",
    ),
    "umans-glm-5.2": UmansModelConfig(
        name="umans-glm-5.2",
        family="glm",
        concurrency_limit=4,
        context_window=1_000_000,  # 1M token context per requirement
        max_tokens=8192,
        temperature=0.3,
        description="Memory model for Hermes state management and learning loops",
    ),
    "umans-qwen3.6-35b-a3b": UmansModelConfig(
        name="umans-qwen3.6-35b-a3b",
        family="qwen",
        concurrency_limit=8,
        context_window=131_072,
        max_tokens=8192,
        temperature=0.0,
        description="Qwen 3.6 35B-A3B (Qwen family, alternative to umans-flash)",
    ),
    # Note: umans-embed-* models are NOT in the Umans /v1/models list as of
    # 2026-06-20. The OpenAI-compatible /v1/embeddings endpoint returns 404.
    # The embedding_node in the graph handles this gracefully (logs warning,
    # continues). Re-enable embed model when Umans adds the endpoint.
}


def get_model_config(model_name: str) -> UmansModelConfig:
    """Look up a model's configuration by name.

    Args:
        model_name: One of the keys in UMANS_MODELS.

    Returns:
        The frozen UmansModelConfig for the requested model.

    Raises:
        ValueError: If the model name is not registered.
    """
    if model_name not in UMANS_MODELS:
        available = ", ".join(UMANS_MODELS)
        msg = f"Unknown Umans model: {model_name!r}. Available: {available}"
        raise ValueError(msg)
    return UMANS_MODELS[model_name]
