"""Tests for OpenAI provider behavior."""

import asyncio
from types import SimpleNamespace

from waypoints.llm.providers.openai import OpenAIProvider


def test_agent_query_uses_prompt_cache_params() -> None:
    class FakePromptTokenDetails:
        cached_tokens = 25

    class FakeUsage:
        prompt_tokens = 120
        completion_tokens = 30
        prompt_tokens_details = FakePromptTokenDetails()

    class FakeMessage:
        content = "done"
        tool_calls = None

    class FakeChoice:
        message = FakeMessage()
        finish_reason = "stop"

    class FakeResponse:
        choices = [FakeChoice()]
        usage = FakeUsage()

    class FakeCompletions:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        async def create(self, **kwargs: object) -> FakeResponse:
            self.calls.append(kwargs)
            return FakeResponse()

    fake_completions = FakeCompletions()
    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=fake_completions))

    provider = OpenAIProvider(api_key="test-key")
    provider._get_async_client = lambda: fake_client  # type: ignore[method-assign]

    async def run_query() -> None:
        async for _ in provider.agent_query(
            prompt="test prompt",
            system_prompt="system",
            allowed_tools=["Read"],
            cwd="/tmp/project",
            resume_session_id="session-123",
            phase="fly",
            waypoint_id="WP-001",
        ):
            pass

    asyncio.run(run_query())

    assert len(fake_completions.calls) == 1
    call = fake_completions.calls[0]
    assert call["prompt_cache_retention"] == "24h"
    assert str(call["prompt_cache_key"]).startswith("waypoints:fly:agent:")
