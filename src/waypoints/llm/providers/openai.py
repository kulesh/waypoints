"""OpenAI LLM provider."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections.abc import AsyncIterator, Iterator
from typing import TYPE_CHECKING, Any

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

# Tool definitions for OpenAI function calling
TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the contents of a file at the given path.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Absolute path to the file to read",
                    },
                },
                "required": ["file_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write content to a file, creating it if it doesn't exist.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Absolute path to the file to write",
                    },
                    "content": {
                        "type": "string",
                        "description": "Content to write to the file",
                    },
                },
                "required": ["file_path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Edit a file by replacing old_string with new_string.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Absolute path to the file to edit",
                    },
                    "old_string": {
                        "type": "string",
                        "description": "The exact string to find and replace",
                    },
                    "new_string": {
                        "type": "string",
                        "description": "The string to replace old_string with",
                    },
                },
                "required": ["file_path", "old_string", "new_string"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "Execute a bash command and return its output.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The bash command to execute",
                    },
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "glob",
            "description": "Find files matching a glob pattern.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Glob pattern (e.g., '**/*.py')",
                    },
                    "path": {
                        "type": "string",
                        "description": "Directory to search in (default: cwd)",
                    },
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep",
            "description": "Search for a pattern in files.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Regex pattern to search for",
                    },
                    "path": {
                        "type": "string",
                        "description": "File or directory to search in",
                    },
                    "glob": {
                        "type": "string",
                        "description": "Glob pattern to filter files",
                    },
                },
                "required": ["pattern"],
            },
        },
    },
]

# Map tool names to their implementations
TOOL_NAME_MAP = {
    "read_file": "Read",
    "write_file": "Write",
    "edit_file": "Edit",
    "bash": "Bash",
    "glob": "Glob",
    "grep": "Grep",
}


class OpenAIProvider(LLMProvider):
    """OpenAI provider using the openai Python SDK.

    Requires an API key (from settings, env var, or passed directly).
    """

    provider_name = "openai"

    def __init__(
        self,
        model: str = "gpt-5.2",
        api_key: str | None = None,
    ) -> None:
        """Initialize OpenAI provider.

        Args:
            model: Model identifier (e.g., "gpt-4o", "gpt-5.2").
            api_key: Optional API key. If None, uses OPENAI_API_KEY env var.
        """
        super().__init__(model=model, api_key=api_key)
        logger.info("OpenAIProvider initialized (model=%s)", model)

    def _get_client(self) -> Any:
        """Get an OpenAI client instance."""
        from openai import OpenAI

        api_key = self.api_key or os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise ValueError(
                "OpenAI API key required. Set OPENAI_API_KEY env var or configure "
                "in settings."
            )
        return OpenAI(api_key=api_key)

    def _get_async_client(self) -> Any:
        """Get an async OpenAI client instance."""
        from openai import AsyncOpenAI

        api_key = self.api_key or os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise ValueError(
                "OpenAI API key required. Set OPENAI_API_KEY env var or configure "
                "in settings."
            )
        return AsyncOpenAI(api_key=api_key)

    def stream_message(
        self,
        messages: list[dict[str, str]],
        system: str = "",
        max_tokens: int = 4096,
        metrics_collector: "MetricsCollector | None" = None,
        phase: str = "unknown",
    ) -> Iterator[StreamChunk | StreamComplete]:
        """Stream response chunks from OpenAI."""
        logger.info(
            "stream_message: %d messages, system=%d chars",
            len(messages),
            len(system),
        )

        start_time = time.perf_counter()
        cost: float | None = None
        success = True
        error_msg: str | None = None

        try:
            client = self._get_client()

            # Build messages list
            api_messages: list[dict[str, str]] = []
            if system:
                api_messages.append({"role": "system", "content": system})
            api_messages.extend(messages)

            # Stream completion
            full_text = ""
            stream = client.chat.completions.create(
                model=self.model,
                messages=api_messages,
                max_tokens=max_tokens,
                stream=True,
            )

            for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    text = chunk.choices[0].delta.content
                    full_text += text
                    yield StreamChunk(text=text)

            # OpenAI doesn't provide cost in streaming responses
            # Would need to calculate from token counts
            yield StreamComplete(full_text=full_text, cost_usd=None)

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
        """Run an agentic query with tool use.

        Implements a tool loop: sends message, executes tool calls,
        sends results back, continues until completion.
        """
        start_time = time.perf_counter()
        error_msg: str | None = None
        last_error: Exception | None = None
        full_text = ""

        # Filter tools based on allowed_tools
        tools: list[dict[str, Any]] = []
        if allowed_tools:
            allowed_set = {t.lower() for t in allowed_tools}
            allowed_mapped = {TOOL_NAME_MAP.get(t, t).lower() for t in allowed_tools}
            all_allowed = allowed_set | allowed_mapped
            for tool_def in TOOL_DEFINITIONS:
                func_info = tool_def.get("function", {})
                tool_name = str(func_info.get("name", ""))
                mapped_name = TOOL_NAME_MAP.get(tool_name, tool_name)
                tool_lower = tool_name.lower()
                mapped_lower = mapped_name.lower()
                if tool_lower in all_allowed or mapped_lower in all_allowed:
                    tools.append(tool_def)

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

            has_yielded = False

            try:
                client = self._get_async_client()

                # Build initial messages
                messages: list[dict[str, Any]] = []
                if system_prompt:
                    messages.append({"role": "system", "content": system_prompt})
                messages.append({"role": "user", "content": prompt})

                # Tool loop
                max_iterations = 50  # Prevent infinite loops
                for _ in range(max_iterations):
                    # Make API call
                    response = await client.chat.completions.create(
                        model=self.model,
                        messages=messages,
                        tools=tools if tools else None,
                        max_tokens=16000,
                    )

                    choice = response.choices[0]
                    message = choice.message

                    # Handle text content
                    if message.content:
                        full_text += message.content
                        has_yielded = True
                        yield StreamChunk(text=message.content)

                    # Check for tool calls
                    if message.tool_calls:
                        # Add assistant message to history
                        messages.append(message.model_dump())

                        # Process each tool call
                        for tool_call in message.tool_calls:
                            tool_name = tool_call.function.name
                            try:
                                arguments = json.loads(tool_call.function.arguments)
                            except json.JSONDecodeError:
                                arguments = {}

                            # Yield tool use for logging/display
                            has_yielded = True
                            # Execute the tool (host-side) and surface output
                            result = execute_tool(tool_name, arguments, cwd)
                            yield StreamToolUse(
                                tool_name=TOOL_NAME_MAP.get(tool_name, tool_name),
                                tool_input=arguments,
                                tool_output=result,
                            )

                            # Add tool result to messages
                            messages.append({
                                "role": "tool",
                                "tool_call_id": tool_call.id,
                                "content": result,
                            })

                        # Continue loop to get next response
                        continue

                    # No tool calls and finish_reason indicates done
                    if choice.finish_reason in ("stop", "length"):
                        break

                # Success
                yield StreamComplete(full_text=full_text, cost_usd=None)

                elapsed_ms = int((time.perf_counter() - start_time) * 1000)
                if metrics_collector is not None:
                    from waypoints.llm.metrics import LLMCall

                    call = LLMCall.create(
                        phase=phase,
                        waypoint_id=waypoint_id,
                        cost_usd=0.0,  # Would need token counting for cost
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

                    elapsed_ms = int((time.perf_counter() - start_time) * 1000)
                    if metrics_collector is not None:
                        from waypoints.llm.metrics import LLMCall

                        call = LLMCall.create(
                            phase=phase,
                            waypoint_id=waypoint_id,
                            cost_usd=0.0,
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

        # All retries exhausted
        if last_error:
            error_msg = str(last_error)
            elapsed_ms = int((time.perf_counter() - start_time) * 1000)
            if metrics_collector is not None:
                from waypoints.llm.metrics import LLMCall

                call = LLMCall.create(
                    phase=phase,
                    waypoint_id=waypoint_id,
                    cost_usd=0.0,
                    latency_ms=elapsed_ms,
                    model=self.model,
                    success=False,
                    error=error_msg,
                )
                metrics_collector.record(call)
            raise last_error
