"""Tests for OpenAI provider behavior."""

import asyncio
from types import SimpleNamespace

from waypoints.llm.providers.base import StreamComplete
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


def test_agent_query_resumes_session_across_calls() -> None:
    class FakePromptTokenDetails:
        cached_tokens = 0

    class FakeUsage:
        prompt_tokens = 50
        completion_tokens = 10
        prompt_tokens_details = FakePromptTokenDetails()

    class FakeCompletions:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []
            self._response_texts = ["first response", "second response"]

        async def create(self, **kwargs: object):
            self.calls.append(kwargs)
            text = self._response_texts[len(self.calls) - 1]

            class FakeMessage:
                content = text
                tool_calls = None

                def model_dump(self) -> dict[str, object]:
                    return {"role": "assistant", "content": text}

            class FakeChoice:
                message = FakeMessage()
                finish_reason = "stop"

            class FakeResponse:
                choices = [FakeChoice()]
                usage = FakeUsage()

            return FakeResponse()

    OpenAIProvider._resumable_sessions.clear()
    OpenAIProvider._resumable_order.clear()

    fake_completions = FakeCompletions()
    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=fake_completions))

    provider_first = OpenAIProvider(api_key="test-key")
    provider_first._get_async_client = lambda: fake_client  # type: ignore[method-assign]
    provider_second = OpenAIProvider(api_key="test-key")
    provider_second._get_async_client = lambda: fake_client  # type: ignore[method-assign]

    async def run_query(
        provider: OpenAIProvider,
        prompt: str,
        resume_session_id: str | None = None,
    ) -> str | None:
        session_id: str | None = None
        async for chunk in provider.agent_query(
            prompt=prompt,
            system_prompt="system",
            allowed_tools=["Read"],
            cwd="/tmp/project",
            resume_session_id=resume_session_id,
            phase="fly",
            waypoint_id="WP-001",
        ):
            if isinstance(chunk, StreamComplete):
                session_id = chunk.session_id
        return session_id

    first_session_id = asyncio.run(run_query(provider_first, "first prompt"))
    assert first_session_id is not None

    second_session_id = asyncio.run(
        run_query(
            provider_second,
            "second prompt",
            resume_session_id=first_session_id,
        )
    )
    assert second_session_id == first_session_id
    assert len(fake_completions.calls) == 2

    second_messages = fake_completions.calls[1]["messages"]
    assert isinstance(second_messages, list)
    user_prompts = [
        message.get("content")
        for message in second_messages
        if isinstance(message, dict) and message.get("role") == "user"
    ]
    assert user_prompts == ["first prompt", "second prompt"]
    assistant_outputs = [
        message.get("content")
        for message in second_messages
        if isinstance(message, dict) and message.get("role") == "assistant"
    ]
    assert "first response" in assistant_outputs
