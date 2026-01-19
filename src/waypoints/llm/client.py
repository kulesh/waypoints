"""LLM clients for Waypoints.

Supports multiple providers (Anthropic, OpenAI) with a unified interface.
"""

import logging
from collections.abc import AsyncIterator, Iterator
from typing import TYPE_CHECKING

# Re-export types from providers for backwards compatibility
from waypoints.llm.providers.base import (
    APIErrorType,
    LLMProvider,
    StreamChunk,
    StreamComplete,
    StreamToolUse,
    classify_api_error,
    extract_reset_time,
    is_retryable_error,
)

if TYPE_CHECKING:
    from waypoints.llm.metrics import MetricsCollector

logger = logging.getLogger(__name__)

__all__ = [
    "APIErrorType",
    "ChatClient",
    "LLMProvider",
    "StreamChunk",
    "StreamComplete",
    "StreamToolUse",
    "agent_query",
    "classify_api_error",
    "extract_reset_time",
    "get_provider",
    "is_retryable_error",
]


def get_provider(
    provider: str | None = None,
    model: str | None = None,
) -> LLMProvider:
    """Get a configured LLM provider.

    Args:
        provider: Override provider ("anthropic" or "openai").
                  If None, uses settings.llm_provider.
        model: Override model string.
               If None, uses settings.llm_model.

    Returns:
        Configured LLMProvider instance.

    Raises:
        ValueError: If provider is unknown or configuration is invalid.
    """
    from waypoints.config.settings import settings

    provider_name = provider or settings.llm_provider
    model_name = model or settings.llm_model

    if provider_name == "openai":
        from waypoints.llm.providers.openai import OpenAIProvider

        return OpenAIProvider(
            model=model_name,
            api_key=settings.openai_api_key,
        )
    elif provider_name == "anthropic":
        from waypoints.llm.providers.anthropic import AnthropicProvider

        return AnthropicProvider(
            model=model_name,
            api_key=settings.anthropic_api_key,
            use_web_auth=settings.use_web_auth,
        )
    else:
        raise ValueError(f"Unknown LLM provider: {provider_name}")


class ChatClient:
    """Chat client for LLM interactions.

    This is the primary interface for simple chat/Q&A. Delegates to the
    configured provider.

    For backwards compatibility, this class maintains the same interface
    as the original Claude-specific implementation.
    """

    def __init__(
        self,
        metrics_collector: "MetricsCollector | None" = None,
        phase: str = "unknown",
        provider: str | None = None,
        model: str | None = None,
    ) -> None:
        """Initialize ChatClient.

        Args:
            metrics_collector: Optional metrics collector for tracking costs.
            phase: Phase name for metrics (e.g., "ideation-qa", "fly").
            provider: Override provider (uses settings if None).
            model: Override model (uses settings if None).
        """
        self._provider = get_provider(provider=provider, model=model)
        self._metrics = metrics_collector
        self._phase = phase
        logger.info(
            "ChatClient initialized (provider=%s, model=%s)",
            self._provider.provider_name,
            self._provider.model,
        )

    def stream_message(
        self,
        messages: list[dict[str, str]],
        system: str = "",
        max_tokens: int = 4096,
    ) -> Iterator[StreamChunk | StreamComplete]:
        """Stream response chunks from the LLM.

        Args:
            messages: Conversation history as list of {role, content} dicts.
            system: System prompt.
            max_tokens: Maximum tokens in response.

        Yields:
            StreamChunk for each text piece, then StreamComplete at end.
        """
        return self._provider.stream_message(
            messages=messages,
            system=system,
            max_tokens=max_tokens,
            metrics_collector=self._metrics,
            phase=self._phase,
        )


async def agent_query(
    prompt: str,
    system_prompt: str | None = None,
    allowed_tools: list[str] | None = None,
    cwd: str | None = None,
    metrics_collector: "MetricsCollector | None" = None,
    phase: str = "fly",
    waypoint_id: str | None = None,
    provider: str | None = None,
    model: str | None = None,
) -> AsyncIterator[StreamChunk | StreamToolUse | StreamComplete]:
    """Run an agentic query with tool use.

    Use this for tasks that require file operations, bash commands, etc.
    For simple Q&A, use ChatClient instead.

    Includes automatic retry with exponential backoff for transient API errors
    (rate limits, service unavailability). Retries only happen before streaming
    starts - once data has been yielded, errors are propagated immediately.

    Args:
        prompt: The prompt to send.
        system_prompt: Optional system prompt.
        allowed_tools: List of tool names to allow.
        cwd: Working directory for tools.
        metrics_collector: Optional collector for recording metrics.
        phase: Phase name for metrics (default "fly").
        waypoint_id: Optional waypoint ID for per-waypoint metrics.
        provider: Override provider (uses settings if None).
        model: Override model (uses settings if None).

    Yields:
        StreamChunk: For each text piece from the assistant
        StreamToolUse: When the agent calls a tool (name and input)
        StreamComplete: At the end with full text and cost
    """
    llm_provider = get_provider(provider=provider, model=model)

    async for result in llm_provider.agent_query(
        prompt=prompt,
        system_prompt=system_prompt,
        allowed_tools=allowed_tools,
        cwd=cwd,
        metrics_collector=metrics_collector,
        phase=phase,
        waypoint_id=waypoint_id,
    ):
        yield result


# Backwards compatibility alias
AnthropicClient = ChatClient
