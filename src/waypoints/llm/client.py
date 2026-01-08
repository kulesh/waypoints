"""LLM clients for Waypoints.

Uses Claude Agent SDK for all interactions - supports web auth.
"""

import asyncio
import logging
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    TextBlock,
    query,
)

logger = logging.getLogger(__name__)


# --- Chat Client using Agent SDK (supports web auth) ---


class ChatClient:
    """Chat client using Claude Agent SDK - uses web auth automatically."""

    def __init__(self) -> None:
        logger.info("ChatClient initialized (using Agent SDK with web auth)")

    def stream_message(
        self,
        messages: list[dict[str, str]],
        system: str = "",
        max_tokens: int = 4096,
    ) -> Iterator[str]:
        """Stream response chunks from Claude via Agent SDK."""
        # Format conversation as a single prompt for the agent
        prompt = self._format_messages_as_prompt(messages)
        logger.info(
            "stream_message called: prompt=%d chars, system=%d chars",
            len(prompt),
            len(system),
        )

        # Run async query in sync context
        try:
            for chunk in self._run_agent_query(prompt, system):
                yield chunk
        except Exception as e:
            logger.exception("Error in stream_message: %s", e)
            raise

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

    def _run_agent_query(self, prompt: str, system: str) -> Iterator[str]:
        """Run agent query and yield text chunks."""
        import os

        # Clear invalid API key to force web auth
        env_backup = os.environ.pop("ANTHROPIC_API_KEY", None)

        # Create event loop for async operation
        loop = asyncio.new_event_loop()
        try:
            # Collect all chunks from async generator
            async def collect_chunks() -> list[str]:
                chunks: list[str] = []
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
                        logger.info(
                            "Query complete, cost: $%.4f", message.total_cost_usd or 0
                        )
                return chunks

            chunks = loop.run_until_complete(collect_chunks())
            logger.info("Got %d chunks total", len(chunks))
            for chunk in chunks:
                yield chunk
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
) -> AsyncIterator[StreamChunk | StreamComplete]:
    """
    Run an agentic query using Claude Agent SDK.

    Use this for tasks that require file operations, bash commands, etc.
    For simple Q&A, use ChatClient instead.

    Yields StreamChunk for each text piece, then StreamComplete at the end.
    """
    # Use env parameter to clear API key and force web auth
    options = ClaudeAgentOptions(
        allowed_tools=allowed_tools or [],
        system_prompt=system_prompt,
        cwd=cwd,
        env={"ANTHROPIC_API_KEY": ""},  # Force web auth
    )

    full_text = ""
    cost: float | None = None

    async for message in query(prompt=prompt, options=options):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    full_text += block.text
                    yield StreamChunk(text=block.text)
        elif isinstance(message, ResultMessage):
            cost = message.total_cost_usd

    yield StreamComplete(full_text=full_text, cost_usd=cost)


# Backwards compatibility alias
AnthropicClient = ChatClient
