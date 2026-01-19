"""Anthropic LLM provider using Claude Agent SDK."""

import asyncio
import logging
import os
import time
from collections.abc import AsyncIterator, Iterator
from typing import TYPE_CHECKING

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
    query,
)

from waypoints.llm.providers.base import (
    MAX_RETRIES,
    RETRY_DELAYS,
    LLMProvider,
    StreamChunk,
    StreamComplete,
    StreamToolUse,
    classify_api_error,
    is_retryable_error,
)
from waypoints.llm.tools import execute_tool

if TYPE_CHECKING:
    from waypoints.llm.metrics import MetricsCollector

logger = logging.getLogger(__name__)


class AnthropicProvider(LLMProvider):
    """Anthropic provider using Claude Agent SDK.

    Supports both web auth (default) and API key authentication.
    """

    provider_name = "anthropic"

    def __init__(
        self,
        model: str = "claude-sonnet-4-5-20241022",
        api_key: str | None = None,
        use_web_auth: bool = True,
    ) -> None:
        """Initialize Anthropic provider.

        Args:
            model: Model identifier (for metrics tracking).
            api_key: Optional API key. If None and use_web_auth=True, uses web auth.
            use_web_auth: Whether to use web auth (default True).
        """
        super().__init__(model=model, api_key=api_key)
        self.use_web_auth = use_web_auth
        logger.info(
            "AnthropicProvider initialized (model=%s, web_auth=%s)",
            model,
            use_web_auth,
        )

    def stream_message(
        self,
        messages: list[dict[str, str]],
        system: str = "",
        max_tokens: int = 4096,
        metrics_collector: "MetricsCollector | None" = None,
        phase: str = "unknown",
    ) -> Iterator[StreamChunk | StreamComplete]:
        """Stream response chunks from Claude via Agent SDK."""
        prompt = self._format_messages_as_prompt(messages)
        logger.info(
            "stream_message: prompt=%d chars, system=%d chars",
            len(prompt),
            len(system),
        )

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
            elapsed_ms = int((time.perf_counter() - start_time) * 1000)
            if metrics_collector is not None:
                from waypoints.llm.metrics import LLMCall

                call = LLMCall.create(
                    phase=phase,
                    cost_usd=cost or 0.0,
                    latency_ms=elapsed_ms,
                    model=self.model,
                    success=success,
                    error=error_msg,
                )
                metrics_collector.record(call)

    def _format_messages_as_prompt(self, messages: list[dict[str, str]]) -> str:
        """Format message history as a prompt for the agent."""
        if not messages:
            return ""

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
    ) -> Iterator[StreamChunk | StreamComplete]:
        """Run agent query and yield text chunks, then StreamComplete."""
        env_backup: str | None = None

        if self.use_web_auth:
            # Clear API key to force web auth (always, when web auth is enabled)
            env_backup = os.environ.pop("ANTHROPIC_API_KEY", None)
        elif self.api_key:
            # Use provided API key (only when NOT using web auth)
            env_backup = os.environ.get("ANTHROPIC_API_KEY")
            os.environ["ANTHROPIC_API_KEY"] = self.api_key

        loop = asyncio.new_event_loop()
        try:

            async def collect_results() -> tuple[list[str], float | None]:
                chunks: list[str] = []
                cost: float | None = None
                options = ClaudeAgentOptions(
                    allowed_tools=[],
                    system_prompt=system if system else None,
                )
                logger.info("Starting agent query")
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

            full_text = ""
            for chunk in chunks:
                full_text += chunk
                yield StreamChunk(text=chunk)

            yield StreamComplete(full_text=full_text, cost_usd=cost)
        finally:
            loop.close()
            # Restore env var if we removed it
            if env_backup is not None:
                os.environ["ANTHROPIC_API_KEY"] = env_backup

    async def agent_query(
        self,
        prompt: str,
        system_prompt: str | None = None,
        allowed_tools: list[str] | None = None,
        cwd: str | None = None,
        metrics_collector: "MetricsCollector | None" = None,
        phase: str = "fly",
        waypoint_id: str | None = None,
    ) -> AsyncIterator[StreamChunk | StreamToolUse | StreamComplete]:
        """Run an agentic query with tool use."""
        env_config: dict[str, str] = {}
        if self.use_web_auth:
            env_config["ANTHROPIC_API_KEY"] = ""  # Force web auth (always)
        elif self.api_key:
            env_config["ANTHROPIC_API_KEY"] = self.api_key

        options = ClaudeAgentOptions(
            allowed_tools=allowed_tools or [],
            system_prompt=system_prompt,
            cwd=cwd,
            env=env_config,
        )

        start_time = time.perf_counter()
        error_msg: str | None = None
        final_cost: float | None = None
        last_error: Exception | None = None

        for attempt in range(MAX_RETRIES + 1):
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
                                tool_output = execute_tool(
                                    block.name, block.input, cwd
                                )
                                yield StreamToolUse(
                                    tool_name=block.name,
                                    tool_input=block.input,
                                    tool_output=tool_output,
                                )
                    elif isinstance(message, ResultMessage):
                        cost = message.total_cost_usd

                final_cost = cost
                yield StreamComplete(full_text=full_text, cost_usd=cost)

                elapsed_ms = int((time.perf_counter() - start_time) * 1000)
                if metrics_collector is not None:
                    from waypoints.llm.metrics import LLMCall

                    call = LLMCall.create(
                        phase=phase,
                        waypoint_id=waypoint_id,
                        cost_usd=cost or 0.0,
                        latency_ms=elapsed_ms,
                        model=self.model,
                        success=True,
                        error=None,
                    )
                    metrics_collector.record(call)
                return

            except Exception as e:
                last_error = e
                error_type = classify_api_error(e)

                if has_yielded or not is_retryable_error(error_type):
                    error_msg = str(e)
                    final_cost = cost

                    elapsed_ms = int((time.perf_counter() - start_time) * 1000)
                    if metrics_collector is not None:
                        from waypoints.llm.metrics import LLMCall

                        call = LLMCall.create(
                            phase=phase,
                            waypoint_id=waypoint_id,
                            cost_usd=cost or 0.0,
                            latency_ms=elapsed_ms,
                            model=self.model,
                            success=False,
                            error=error_msg,
                        )
                        metrics_collector.record(call)
                    raise

                logger.warning(
                    "Transient API error (%s): %s. Will retry.",
                    error_type.value,
                    e,
                )

        if last_error:
            error_msg = str(last_error)
            elapsed_ms = int((time.perf_counter() - start_time) * 1000)
            if metrics_collector is not None:
                from waypoints.llm.metrics import LLMCall

                call = LLMCall.create(
                    phase=phase,
                    waypoint_id=waypoint_id,
                    cost_usd=final_cost or 0.0,
                    latency_ms=elapsed_ms,
                    model=self.model,
                    success=False,
                    error=error_msg,
                )
                metrics_collector.record(call)
            raise last_error
