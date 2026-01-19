"""Tests for shared LLM tool execution helpers."""

from waypoints.llm.tools import execute_tool


def test_execute_tool_bash_echo() -> None:
    """Bash tool execution returns command output."""
    result = execute_tool("bash", {"command": "echo hello"}, cwd=None)
    assert "hello" in result
