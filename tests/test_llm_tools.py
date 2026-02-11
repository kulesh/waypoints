"""Tests for shared LLM tool execution helpers."""

from pathlib import Path
from sys import executable

from waypoints.llm.tools import allowed_tools_for_role, execute_tool


def test_execute_tool_bash_echo() -> None:
    """Bash tool execution returns command output."""
    result = execute_tool("bash", {"command": "echo hello"}, cwd=None)
    assert "hello" in result


def test_execute_tool_bash_timeout_reports_timeout_and_exit_code() -> None:
    """Timed-out bash command should report timeout and exit code."""
    command = f'"{executable}" -c "import time; time.sleep(2)"'
    result = execute_tool("bash", {"command": command, "timeout": 0.1}, cwd=None)
    assert "Command timed out after" in result
    assert "Timeout lifecycle:" in result
    assert "Exit code:" in result


def test_read_file_denies_sessions_path(tmp_path: Path) -> None:
    """Read tool denies blocked project metadata directories."""
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True)
    blocked_file = sessions_dir / "run.jsonl"
    blocked_file.write_text("{}", encoding="utf-8")

    result = execute_tool(
        "read_file",
        {"file_path": str(blocked_file)},
        cwd=str(tmp_path),
    )

    assert "Error: Access denied:" in result
    assert "sessions" in result


def test_read_file_denies_outside_project(tmp_path: Path) -> None:
    """Read tool denies absolute paths outside project root."""
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("nope", encoding="utf-8")
    try:
        result = execute_tool(
            "read_file",
            {"file_path": str(outside)},
            cwd=str(tmp_path),
        )
        assert "Error: Access denied:" in result
        assert "outside project root" in result
    finally:
        outside.unlink(missing_ok=True)


def test_read_file_allows_workspace_code(tmp_path: Path) -> None:
    """Read tool still works for normal source files in the project."""
    src_file = tmp_path / "src" / "main.py"
    src_file.parent.mkdir(parents=True)
    src_file.write_text("print('ok')", encoding="utf-8")

    result = execute_tool(
        "read_file",
        {"file_path": str(src_file)},
        cwd=str(tmp_path),
    )

    assert "print('ok')" in result


def test_read_file_denies_memory_path(tmp_path: Path) -> None:
    """.waypoints/memory should remain blocked from agent tool access."""
    memory_dir = tmp_path / ".waypoints" / "memory"
    memory_dir.mkdir(parents=True)
    memory_file = memory_dir / "project-index.v1.json"
    memory_file.write_text("{}", encoding="utf-8")

    result = execute_tool(
        "read_file",
        {"file_path": str(memory_file)},
        cwd=str(tmp_path),
    )

    assert "Error: Access denied:" in result
    assert ".waypoints" in result


def test_read_file_denies_stack_specific_dependency_dir(tmp_path: Path) -> None:
    """Stack-aware policy should block dependency roots like node_modules."""
    (tmp_path / "package.json").write_text('{"name":"demo"}\n', encoding="utf-8")
    dependency_dir = tmp_path / "node_modules"
    dependency_dir.mkdir()
    blocked_file = dependency_dir / "left-pad.js"
    blocked_file.write_text("module.exports={};", encoding="utf-8")

    result = execute_tool(
        "read_file",
        {"file_path": str(blocked_file)},
        cwd=str(tmp_path),
    )

    assert "Error: Access denied:" in result
    assert "node_modules" in result


def test_verifier_role_cannot_write_files(tmp_path: Path) -> None:
    """Verifier role must fail fast on mutating tool attempts."""
    target = tmp_path / "src" / "new_file.py"

    result = execute_tool(
        "write_file",
        {"file_path": str(target), "content": "print('no')"},
        cwd=str(tmp_path),
        tool_role="verifier",
    )

    assert "Error: Access denied:" in result
    assert "verifier role" in result
    assert not target.exists()


def test_allowed_tools_for_role_verifier_excludes_mutations() -> None:
    verifier_tools = allowed_tools_for_role("verifier")
    assert "read_file" in verifier_tools
    assert "write_file" not in verifier_tools
    assert "edit_file" not in verifier_tools
