"""Tests for prompt caching helpers."""

from waypoints.llm.prompt_cache import build_prompt_cache_key


def test_prompt_cache_key_is_stable_for_same_context() -> None:
    key1 = build_prompt_cache_key(
        provider="openai",
        model="gpt-5.2",
        phase="fly",
        cwd="/tmp/project-a",
        mode="agent",
    )
    key2 = build_prompt_cache_key(
        provider="openai",
        model="gpt-5.2",
        phase="fly",
        cwd="/tmp/project-a",
        mode="agent",
    )

    assert key1 == key2


def test_prompt_cache_key_changes_with_project() -> None:
    key1 = build_prompt_cache_key(
        provider="openai",
        model="gpt-5.2",
        phase="fly",
        cwd="/tmp/project-a",
        mode="agent",
    )
    key2 = build_prompt_cache_key(
        provider="openai",
        model="gpt-5.2",
        phase="fly",
        cwd="/tmp/project-b",
        mode="agent",
    )

    assert key1 != key2
