"""LLM clients for Waypoints.

Uses Claude Agent SDK for all interactions - supports web auth.
"""

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    TextBlock,
    query,
)

if TYPE_CHECKING:
    from waypoints.llm.metrics import MetricsCollector

logger = logging.getLogger(__name__)


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
) -> AsyncIterator[StreamChunk | StreamComplete]:
    """
    Run an agentic query using Claude Agent SDK.

    Use this for tasks that require file operations, bash commands, etc.
    For simple Q&A, use ChatClient instead.

    Yields StreamChunk for each text piece, then StreamComplete at the end.

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
    full_text = ""
    cost: float | None = None
    success = True
    error_msg: str | None = None

    try:
        async for message in query(prompt=prompt, options=options):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        full_text += block.text
                        yield StreamChunk(text=block.text)
            elif isinstance(message, ResultMessage):
                cost = message.total_cost_usd

        yield StreamComplete(full_text=full_text, cost_usd=cost)
    except Exception as e:
        success = False
        error_msg = str(e)
        raise
    finally:
        # Record metrics
        elapsed_ms = int((time.perf_counter() - start_time) * 1000)
        if metrics_collector is not None:
            from waypoints.llm.metrics import LLMCall

            call = LLMCall.create(
                phase=phase,
                waypoint_id=waypoint_id,
                cost_usd=cost or 0.0,
                latency_ms=elapsed_ms,
                success=success,
                error=error_msg,
            )
            metrics_collector.record(call)


# Backwards compatibility alias
AnthropicClient = ChatClient
