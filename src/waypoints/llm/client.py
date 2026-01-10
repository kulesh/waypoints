"""LLM clients for Waypoints.

Uses Claude Agent SDK for all interactions - supports web auth.
"""

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
    query,
)

if TYPE_CHECKING:
    from waypoints.llm.metrics import MetricsCollector

logger = logging.getLogger(__name__)


# --- API Error Classification ---


class APIErrorType(Enum):
    """Types of API errors for classification."""

    RATE_LIMITED = "rate_limited"
    API_UNAVAILABLE = "api_unavailable"
    BUDGET_EXCEEDED = "budget_exceeded"
    UNKNOWN = "unknown"


# Patterns to match in error messages (case-insensitive)
RATE_LIMIT_PATTERNS = [
    "rate limit",
    "rate_limit",
    "ratelimit",
    "429",
    "too many requests",
    "throttl",
]

UNAVAILABLE_PATTERNS = [
    "overloaded",
    "503",
    "502",
    "504",
    "unavailable",
    "service error",
    "temporarily",
    "try again later",
    "capacity",
]

BUDGET_PATTERNS = [
    "budget",
    "spending limit",
    "billing",
    "credit",
    "quota exceeded",
    "usage limit",
    "daily limit",
]


def classify_api_error(error: Exception) -> APIErrorType:
    """Classify an API error by parsing the error message.

    Args:
        error: The exception to classify.

    Returns:
        The classified error type.
    """
    error_str = str(error).lower()

    # Check for budget exceeded first (most specific)
    for pattern in BUDGET_PATTERNS:
        if pattern in error_str:
            return APIErrorType.BUDGET_EXCEEDED

    # Check for rate limiting
    for pattern in RATE_LIMIT_PATTERNS:
        if pattern in error_str:
            return APIErrorType.RATE_LIMITED

    # Check for service unavailability
    for pattern in UNAVAILABLE_PATTERNS:
        if pattern in error_str:
            return APIErrorType.API_UNAVAILABLE

    return APIErrorType.UNKNOWN


def is_retryable_error(error_type: APIErrorType) -> bool:
    """Check if an error type is retryable.

    Budget exceeded is not retryable - user must wait for reset.
    """
    return error_type in (APIErrorType.RATE_LIMITED, APIErrorType.API_UNAVAILABLE)


# Retry configuration
MAX_RETRIES = 3
RETRY_DELAYS = [5, 15, 45]  # Exponential backoff: 5s, 15s, 45s


# --- Chat Client using Agent SDK (supports web auth) ---


class ChatClient:
    """Chat client using Claude Agent SDK - uses web auth automatically."""

    def __init__(
        self,
        metrics_collector: "MetricsCollector | None" = None,
        phase: str = "unknown",
    ) -> None:
        logger.info("ChatClient initialized (using Agent SDK with web auth)")
        self._metrics = metrics_collector
        self._phase = phase

    def stream_message(
        self,
        messages: list[dict[str, str]],
        system: str = "",
        max_tokens: int = 4096,
    ) -> Iterator["StreamChunk | StreamComplete"]:
        """Stream response chunks from Claude via Agent SDK.

        Yields StreamChunk for each text piece, then StreamComplete at the end
        with cost information.
        """
        # Format conversation as a single prompt for the agent
        prompt = self._format_messages_as_prompt(messages)
        logger.info(
            "stream_message called: prompt=%d chars, system=%d chars",
            len(prompt),
            len(system),
        )

        # Run async query in sync context
        start_time = time.perf_counter()
        cost: float | None = None
        success = True
        error_msg: str | None = None

        try:
            for result in self._run_agent_query(prompt, system):
                if isinstance(result, StreamComplete):
                    cost = result.cost_usd
                    yield result
                else:
                    yield result
        except Exception as e:
            logger.exception("Error in stream_message: %s", e)
            success = False
            error_msg = str(e)
            raise
        finally:
            # Record metrics
            elapsed_ms = int((time.perf_counter() - start_time) * 1000)
            if self._metrics is not None:
                from waypoints.llm.metrics import LLMCall

                call = LLMCall.create(
                    phase=self._phase,
                    cost_usd=cost or 0.0,
                    latency_ms=elapsed_ms,
                    success=success,
                    error=error_msg,
                )
                self._metrics.record(call)

    def _format_messages_as_prompt(self, messages: list[dict[str, str]]) -> str:
        """Format message history as a prompt for the agent."""
        if not messages:
            return ""

        # Format full conversation history for context
        parts: list[str] = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role == "user":
                parts.append(f"User: {content}")
            elif role == "assistant":
                parts.append(f"Assistant: {content}")

        return "\n\n".join(parts)

    def _run_agent_query(
        self, prompt: str, system: str
    ) -> Iterator["StreamChunk | StreamComplete"]:
        """Run agent query and yield text chunks, then StreamComplete."""
        import os

        # Clear invalid API key to force web auth
        env_backup = os.environ.pop("ANTHROPIC_API_KEY", None)

        # Create event loop for async operation
        loop = asyncio.new_event_loop()
        try:
            # Collect all chunks and cost from async generator
            async def collect_results() -> tuple[list[str], float | None]:
                chunks: list[str] = []
                cost: float | None = None
                options = ClaudeAgentOptions(
                    allowed_tools=[],  # No tools for simple Q&A
                    system_prompt=system if system else None,
                )
                logger.info("Starting agent query (using web auth)")
                async for message in query(prompt=prompt, options=options):
                    if isinstance(message, AssistantMessage):
                        for block in message.content:
                            if isinstance(block, TextBlock):
                                chunks.append(block.text)
                                logger.debug("Got chunk: %d chars", len(block.text))
                    elif isinstance(message, ResultMessage):
                        cost = message.total_cost_usd
                        logger.info("Query complete, cost: $%.4f", cost or 0)
                return chunks, cost

            chunks, cost = loop.run_until_complete(collect_results())
            logger.info("Got %d chunks total", len(chunks))

            # Yield chunks as StreamChunk
            full_text = ""
            for chunk in chunks:
                full_text += chunk
                yield StreamChunk(text=chunk)

            # Yield final StreamComplete with cost
            yield StreamComplete(full_text=full_text, cost_usd=cost)
        finally:
            loop.close()
            # Restore env var if it was set
            if env_backup is not None:
                os.environ["ANTHROPIC_API_KEY"] = env_backup


# --- Agent Client (for agentic tasks with tools) ---


@dataclass
class StreamChunk:
    """A chunk of streamed text from agent."""

    text: str


@dataclass
class StreamToolUse:
    """Agent is using a tool."""

    tool_name: str
    tool_input: dict[str, Any]


@dataclass
class StreamComplete:
    """Agent stream completion with metadata."""

    full_text: str
    cost_usd: float | None = None


async def agent_query(
    prompt: str,
    system_prompt: str | None = None,
    allowed_tools: list[str] | None = None,
    cwd: str | None = None,
    metrics_collector: "MetricsCollector | None" = None,
    phase: str = "fly",
    waypoint_id: str | None = None,
) -> AsyncIterator[StreamChunk | StreamToolUse | StreamComplete]:
    """
    Run an agentic query using Claude Agent SDK.

    Use this for tasks that require file operations, bash commands, etc.
    For simple Q&A, use ChatClient instead.

    Includes automatic retry with exponential backoff for transient API errors
    (rate limits, service unavailability). Retries only happen before streaming
    starts - once data has been yielded, errors are propagated immediately.

    Yields:
        StreamChunk: For each text piece from the assistant
        StreamToolUse: When the agent calls a tool (name and input)
        StreamComplete: At the end with full text and cost

    Args:
        prompt: The prompt to send.
        system_prompt: Optional system prompt.
        allowed_tools: List of tool names to allow.
        cwd: Working directory for tools.
        metrics_collector: Optional collector for recording metrics.
        phase: Phase name for metrics (default "fly").
        waypoint_id: Optional waypoint ID for per-waypoint metrics.
    """
    # Use env parameter to clear API key and force web auth
    options = ClaudeAgentOptions(
        allowed_tools=allowed_tools or [],
        system_prompt=system_prompt,
        cwd=cwd,
        env={"ANTHROPIC_API_KEY": ""},  # Force web auth
    )

    start_time = time.perf_counter()
    error_msg: str | None = None
    final_cost: float | None = None
    last_error: Exception | None = None

    for attempt in range(MAX_RETRIES + 1):
        # Wait before retry (not on first attempt)
        if attempt > 0:
            delay = RETRY_DELAYS[min(attempt - 1, len(RETRY_DELAYS) - 1)]
            logger.warning(
                "Retrying agent_query after %ds (attempt %d/%d): %s",
                delay,
                attempt + 1,
                MAX_RETRIES + 1,
                last_error,
            )
            await asyncio.sleep(delay)

        full_text = ""
        cost: float | None = None
        has_yielded = False

        try:
            async for message in query(prompt=prompt, options=options):
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            full_text += block.text
                            has_yielded = True
                            yield StreamChunk(text=block.text)
                        elif isinstance(block, ToolUseBlock):
                            has_yielded = True
                            yield StreamToolUse(
                                tool_name=block.name,
                                tool_input=block.input,
                            )
                elif isinstance(message, ResultMessage):
                    cost = message.total_cost_usd

            # Success - yield completion and record metrics
            final_cost = cost
            yield StreamComplete(full_text=full_text, cost_usd=cost)

            # Record successful metrics
            elapsed_ms = int((time.perf_counter() - start_time) * 1000)
            if metrics_collector is not None:
                from waypoints.llm.metrics import LLMCall

                call = LLMCall.create(
                    phase=phase,
                    waypoint_id=waypoint_id,
                    cost_usd=cost or 0.0,
                    latency_ms=elapsed_ms,
                    success=True,
                    error=None,
                )
                metrics_collector.record(call)
            return  # Success - exit the retry loop

        except Exception as e:
            last_error = e
            error_type = classify_api_error(e)

            # Don't retry if we've already yielded data (can't un-yield)
            # Don't retry non-retryable errors (budget exceeded, unknown)
            if has_yielded or not is_retryable_error(error_type):
                error_msg = str(e)
                final_cost = cost

                # Record failed metrics
                elapsed_ms = int((time.perf_counter() - start_time) * 1000)
                if metrics_collector is not None:
                    from waypoints.llm.metrics import LLMCall

                    call = LLMCall.create(
                        phase=phase,
                        waypoint_id=waypoint_id,
                        cost_usd=cost or 0.0,
                        latency_ms=elapsed_ms,
                        success=False,
                        error=error_msg,
                    )
                    metrics_collector.record(call)
                raise

            # Retryable error before any yields - will retry
            logger.warning(
                "Transient API error (%s): %s. Will retry.",
                error_type.value,
                e,
            )

    # All retries exhausted
    if last_error:
        error_msg = str(last_error)

        # Record final failure metrics
        elapsed_ms = int((time.perf_counter() - start_time) * 1000)
        if metrics_collector is not None:
            from waypoints.llm.metrics import LLMCall

            call = LLMCall.create(
                phase=phase,
                waypoint_id=waypoint_id,
                cost_usd=final_cost or 0.0,
                latency_ms=elapsed_ms,
                success=False,
                error=error_msg,
            )
            metrics_collector.record(call)
        raise last_error


# Backwards compatibility alias
AnthropicClient = ChatClient
