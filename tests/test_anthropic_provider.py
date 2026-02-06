"""Tests for Anthropic provider session continuation behavior."""

import asyncio

from waypoints.llm.providers.anthropic import AnthropicProvider
from waypoints.llm.providers.base import StreamComplete


def test_agent_query_passes_resume_and_returns_session_id(
    monkeypatch,
) -> None:
    class FakeTextBlock:
        def __init__(self, text: str) -> None:
            self.text = text

    class FakeAssistantMessage:
        def __init__(self, content, usage) -> None:
            self.content = content
            self.usage = usage

    class FakeResultMessage:
        def __init__(self) -> None:
            self.total_cost_usd = 0.0
            self.usage = {"input_tokens": 5, "output_tokens": 3}
            self.input_tokens = None
            self.output_tokens = None
            self.total_input_tokens = None
            self.total_output_tokens = None
            self.session_id = "session-xyz"

    captured = {"options": None}

    async def fake_query(*, prompt, options):
        _ = prompt
        captured["options"] = options
        yield FakeAssistantMessage(
            content=[FakeTextBlock("hello")],
            usage={"input_tokens": 1, "output_tokens": 1},
        )
        yield FakeResultMessage()

    monkeypatch.setattr(
        "waypoints.llm.providers.anthropic.AssistantMessage",
        FakeAssistantMessage,
    )
    monkeypatch.setattr(
        "waypoints.llm.providers.anthropic.ResultMessage",
        FakeResultMessage,
    )
    monkeypatch.setattr(
        "waypoints.llm.providers.anthropic.TextBlock",
        FakeTextBlock,
    )
    monkeypatch.setattr(
        "waypoints.llm.providers.anthropic.ToolUseBlock",
        type("FakeToolUseBlock", (), {}),
    )
    monkeypatch.setattr("waypoints.llm.providers.anthropic.query", fake_query)

    provider = AnthropicProvider(use_web_auth=True)

    async def run_query() -> list[StreamComplete]:
        completes: list[StreamComplete] = []
        async for item in provider.agent_query(
            prompt="test",
            system_prompt="system",
            allowed_tools=["Read"],
            cwd="/tmp/project",
            resume_session_id="session-prev",
        ):
            if isinstance(item, StreamComplete):
                completes.append(item)
        return completes

    completes = asyncio.run(run_query())
    assert len(completes) == 1
    assert completes[0].session_id == "session-xyz"

    options = captured["options"]
    assert options is not None
    assert options.continue_conversation is True
    assert options.resume == "session-prev"
