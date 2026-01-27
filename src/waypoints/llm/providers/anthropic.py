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

# Map the model-facing tool names to the executor's expected handlers
TOOL_NAME_MAP: dict[str, str] = {
    "read": "read_file",
    "read_file": "read_file",
    "write": "write_file",
    "write_file": "write_file",
    "edit": "edit_file",
    "edit_file": "edit_file",
    "bash": "bash",
    "glob": "glob",
    "grep": "grep",
}


def _extract_token_usage(message: ResultMessage) -> tuple[int | None, int | None]:
    """Best-effort extraction of token usage from SDK result messages."""
    tokens_in = getattr(message, "total_input_tokens", None)
    tokens_out = getattr(message, "total_output_tokens", None)

    if tokens_in is None:
        tokens_in = getattr(message, "input_tokens", None)
    if tokens_out is None:
        tokens_out = getattr(message, "output_tokens", None)

    usage = getattr(message, "usage", None)
    if usage is not None:
        tokens_in = tokens_in or getattr(usage, "input_tokens", None)
        tokens_out = tokens_out or getattr(usage, "output_tokens", None)

    return (
        int(tokens_in) if tokens_in is not None else None,
        int(tokens_out) if tokens_out is not None else None,
    )


def _extract_usage_from_message(message: object) -> tuple[int | None, int | None]:
    """Extract token usage from messages that expose a usage attribute."""
    usage = getattr(message, "usage", None)
    if usage is None:
        return None, None

    tokens_in = getattr(usage, "input_tokens", None)
    tokens_out = getattr(usage, "output_tokens", None)

    if tokens_in is None and isinstance(usage, dict):
        tokens_in = usage.get("input_tokens")
    if tokens_out is None and isinstance(usage, dict):
        tokens_out = usage.get("output_tokens")

    if tokens_in is None:
        tokens_in = getattr(usage, "prompt_tokens", None)
    if tokens_out is None:
        tokens_out = getattr(usage, "completion_tokens", None)

    if tokens_in is None and isinstance(usage, dict):
        tokens_in = usage.get("prompt_tokens")
    if tokens_out is None and isinstance(usage, dict):
        tokens_out = usage.get("completion_tokens")

    return (
        int(tokens_in) if tokens_in is not None else None,
        int(tokens_out) if tokens_out is not None else None,
    )


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
        tokens_in: int | None = None
        tokens_out: int | None = None
        success = True
        error_msg: str | None = None

        try:
            for result in self._run_agent_query(prompt, system):
                if isinstance(result, StreamComplete):
                    cost = result.cost_usd
                    tokens_in = result.tokens_in
                    tokens_out = result.tokens_out
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
                    cost_usd=cost,
                    latency_ms=elapsed_ms,
                    model=self.model,
                    success=success,
                    error=error_msg,
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
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

            async def collect_results() -> (
                tuple[list[str], float | None, int | None, int | None]
            ):
                chunks: list[str] = []
                cost: float | None = None
                tokens_in: int | None = None
                tokens_out: int | None = None
                options = ClaudeAgentOptions(
                    allowed_tools=[],
                    system_prompt=system if system else None,
                )
                logger.info("Starting agent query")
                async for message in query(prompt=prompt, options=options):
                    if isinstance(message, AssistantMessage):
                        usage_in, usage_out = _extract_usage_from_message(message)
                        if usage_in is not None:
                            tokens_in = (tokens_in or 0) + usage_in
                        if usage_out is not None:
                            tokens_out = (tokens_out or 0) + usage_out
                        for block in message.content:
                            if isinstance(block, TextBlock):
                                chunks.append(block.text)
                                logger.debug("Got chunk: %d chars", len(block.text))
                    elif isinstance(message, ResultMessage):
                        cost = message.total_cost_usd
                        logger.debug(
                            (
                                "ResultMessage usage=%r input=%r output=%r "
                                "total_input=%r total_output=%r"
                            ),
                            getattr(message, "usage", None),
                            getattr(message, "input_tokens", None),
                            getattr(message, "output_tokens", None),
                            getattr(message, "total_input_tokens", None),
                            getattr(message, "total_output_tokens", None),
                        )
                        total_in, total_out = _extract_token_usage(message)
                        if total_in is not None:
                            tokens_in = total_in
                        if total_out is not None:
                            tokens_out = total_out
                        logger.info("Query complete, cost: $%.4f", cost or 0)
                return chunks, cost, tokens_in, tokens_out

            chunks, cost, tokens_in, tokens_out = loop.run_until_complete(
                collect_results()
            )
            logger.info("Got %d chunks total", len(chunks))

            full_text = ""
            for chunk in chunks:
                full_text += chunk
                yield StreamChunk(text=chunk)

            yield StreamComplete(
                full_text=full_text,
                cost_usd=cost,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
            )
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
        final_tokens_in: int | None = None
        final_tokens_out: int | None = None
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
            tokens_in: int | None = None
            tokens_out: int | None = None
            has_yielded = False

            try:
                async for message in query(prompt=prompt, options=options):
                    if isinstance(message, AssistantMessage):
                        usage_in, usage_out = _extract_usage_from_message(message)
                        if usage_in is not None:
                            tokens_in = (tokens_in or 0) + usage_in
                        if usage_out is not None:
                            tokens_out = (tokens_out or 0) + usage_out
                        for block in message.content:
                            if isinstance(block, TextBlock):
                                full_text += block.text
                                has_yielded = True
                                yield StreamChunk(text=block.text)
                            elif isinstance(block, ToolUseBlock):
                                has_yielded = True
                                tool_name = TOOL_NAME_MAP.get(
                                    block.name.lower(), block.name
                                )
                                tool_output = execute_tool(tool_name, block.input, cwd)
                                yield StreamToolUse(
                                    tool_name=block.name,
                                    tool_input=block.input,
                                    tool_output=tool_output,
                                )
                    elif isinstance(message, ResultMessage):
                        cost = message.total_cost_usd
                        logger.debug(
                            (
                                "ResultMessage usage=%r input=%r output=%r "
                                "total_input=%r total_output=%r"
                            ),
                            getattr(message, "usage", None),
                            getattr(message, "input_tokens", None),
                            getattr(message, "output_tokens", None),
                            getattr(message, "total_input_tokens", None),
                            getattr(message, "total_output_tokens", None),
                        )
                        total_in, total_out = _extract_token_usage(message)
                        if total_in is not None:
                            tokens_in = total_in
                        if total_out is not None:
                            tokens_out = total_out

                final_cost = cost
                final_tokens_in = tokens_in
                final_tokens_out = tokens_out
                yield StreamComplete(
                    full_text=full_text,
                    cost_usd=cost,
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                )

                elapsed_ms = int((time.perf_counter() - start_time) * 1000)
                if metrics_collector is not None:
                    from waypoints.llm.metrics import LLMCall

                    call = LLMCall.create(
                        phase=phase,
                        waypoint_id=waypoint_id,
                        cost_usd=cost,
                        latency_ms=elapsed_ms,
                        model=self.model,
                        success=True,
                        error=None,
                        tokens_in=tokens_in,
                        tokens_out=tokens_out,
                    )
                    metrics_collector.record(call)
                return

            except Exception as e:
                # The SDK can raise generic runtime errors (e.g., cancel scope) when the
                # CLI exits with code 1. Try to enrich/normalize for classification.
                err_str = str(e)
                if isinstance(e, RuntimeError) and "cancel scope" in err_str:
                    # Preserve quota/rate-limit hints from accumulated text if present
                    if "out of extra usage" in full_text.lower():
                        err_str = (
                            err_str + " | quota exhaustion detected in stream:"
                            " out of extra usage"
                        )
                    e = RuntimeError(err_str)

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
                            cost_usd=cost,
                            latency_ms=elapsed_ms,
                            model=self.model,
                            success=False,
                            error=error_msg,
                            tokens_in=tokens_in,
                            tokens_out=tokens_out,
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
                    cost_usd=final_cost,
                    latency_ms=elapsed_ms,
                    model=self.model,
                    success=False,
                    error=error_msg,
                    tokens_in=final_tokens_in,
                    tokens_out=final_tokens_out,
                )
                metrics_collector.record(call)
            raise last_error
