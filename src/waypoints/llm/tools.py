"""Shared tool execution for LLM providers."""

from pathlib import Path
from typing import Any

from waypoints.memory import (
    IMMUTABLE_BLOCKED_TOP_LEVEL_DIRS,
    load_or_build_project_memory,
)
from waypoints.runtime import CommandEvent, TimeoutDomain, get_command_runner

LEGACY_BLOCKED_TOP_LEVEL_DIRS = frozenset(
    {
        ".git",
        ".waypoints",
        "sessions",
        "receipts",
        "target",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "node_modules",
        "dist",
    }
)


def _access_denied(message: str) -> str:
    """Create a normalized tool error for blocked paths."""
    return f"Error: Access denied: {message}"


def _resolve_tool_path(raw_path: str | Path, cwd: str | None) -> Path:
    """Resolve a potentially-relative tool path against the working directory."""
    path = Path(raw_path)
    if not path.is_absolute() and cwd:
        path = Path(cwd) / path
    return path.resolve()


def _resolve_blocked_top_level_dirs(cwd: str | None) -> frozenset[str]:
    """Resolve blocked top-level directories from project memory."""
    if cwd is None:
        return frozenset(IMMUTABLE_BLOCKED_TOP_LEVEL_DIRS)

    project_root = Path(cwd).resolve()
    try:
        memory = load_or_build_project_memory(project_root)
        return frozenset(memory.index.blocked_top_level_dirs)
    except OSError:
        pass
    except Exception:
        pass

    return frozenset(IMMUTABLE_BLOCKED_TOP_LEVEL_DIRS | LEGACY_BLOCKED_TOP_LEVEL_DIRS)


def _check_path_policy(
    path: Path,
    cwd: str | None,
    blocked_top_level_dirs: frozenset[str] | None = None,
) -> str | None:
    """Validate tool path policy for project confinement and denylisted dirs."""
    if cwd is None:
        return None

    project_root = Path(cwd).resolve()
    blocked_dirs = blocked_top_level_dirs or _resolve_blocked_top_level_dirs(cwd)
    if not path.is_relative_to(project_root):
        return f"{path} is outside project root {project_root}"

    try:
        relative_path = path.relative_to(project_root)
    except ValueError:
        return f"{path} is outside project root {project_root}"

    if relative_path.parts and relative_path.parts[0] in blocked_dirs:
        blocked_root = relative_path.parts[0]
        return f"{path} is under blocked directory '{blocked_root}'"

    return None


def execute_tool(name: str, arguments: dict[str, Any], cwd: str | None) -> str:
    """Execute a tool and return the result as a string.

    Args:
        name: Tool name (read_file, write_file, edit_file, bash, glob, grep).
        arguments: Tool arguments.
        cwd: Working directory for relative paths.

    Returns:
        Result string to send back to the model and/or log.
    """
    import re

    command_runner = get_command_runner()

    def _format_timeout_events(events: list[CommandEvent]) -> str:
        if not events:
            return ""
        lines = ["Timeout lifecycle:"]
        for event in events:
            detail = f" - {event.detail}" if event.detail else ""
            lines.append(
                "  "
                f"[attempt {event.attempt}] {event.event_type} "
                f"(budget={event.timeout_seconds:g}s){detail}"
            )
        return "\n".join(lines)

    try:
        blocked_dirs = _resolve_blocked_top_level_dirs(cwd)
        if name == "read_file":
            path = _resolve_tool_path(arguments["file_path"], cwd)
            if (error := _check_path_policy(path, cwd, blocked_dirs)) is not None:
                return _access_denied(error)
            if not path.exists():
                return f"Error: File not found: {path}"
            return path.read_text(encoding="utf-8")

        if name == "write_file":
            path = _resolve_tool_path(arguments["file_path"], cwd)
            if (error := _check_path_policy(path, cwd, blocked_dirs)) is not None:
                return _access_denied(error)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(arguments["content"], encoding="utf-8")
            return f"Successfully wrote to {path}"

        if name == "edit_file":
            path = _resolve_tool_path(arguments["file_path"], cwd)
            if (error := _check_path_policy(path, cwd, blocked_dirs)) is not None:
                return _access_denied(error)
            if not path.exists():
                return f"Error: File not found: {path}"
            content = path.read_text(encoding="utf-8")
            old_string = arguments["old_string"]
            new_string = arguments["new_string"]
            if old_string not in content:
                return "Error: old_string not found in file"
            new_content = content.replace(old_string, new_string, 1)
            path.write_text(new_content, encoding="utf-8")
            return f"Successfully edited {path}"

        if name == "bash":
            command = arguments["command"]
            raw_timeout = arguments.get("timeout")
            timeout: float | None = None
            if isinstance(raw_timeout, (int, float)):
                timeout = raw_timeout / 1000 if raw_timeout > 1000 else raw_timeout

            timeout_events: list[CommandEvent] = []
            result = command_runner.run(
                command=command,
                domain=TimeoutDomain.LLM_TOOL_BASH,
                cwd=cwd,
                shell=True,
                requested_timeout_seconds=timeout,
                on_event=timeout_events.append,
            )
            output = result.stdout
            if result.stderr:
                output += f"\nSTDERR:\n{result.stderr}"
            if result.timed_out:
                output += (
                    "\nError: Command timed out "
                    f"after {result.final_attempt.timeout_seconds:g}s"
                )
            if result.signal_sequence:
                output += f"\nSignals: {' -> '.join(result.signal_sequence)}"
            event_summary = _format_timeout_events(timeout_events)
            if event_summary:
                output += f"\n{event_summary}"
            if result.effective_exit_code != 0:
                output += f"\nExit code: {result.effective_exit_code}"
            return output or "(no output)"

        if name == "glob":
            pattern = arguments["pattern"]
            search_path = _resolve_tool_path(arguments.get("path", cwd or "."), cwd)
            if (
                error := _check_path_policy(search_path, cwd, blocked_dirs)
            ) is not None:
                return _access_denied(error)
            matches = list(search_path.glob(pattern))
            visible_matches: list[str] = []
            for match in matches:
                resolved_match = match.resolve()
                if _check_path_policy(resolved_match, cwd, blocked_dirs) is not None:
                    continue
                visible_matches.append(str(match))
                if len(visible_matches) >= 100:
                    break
            return "\n".join(visible_matches) or "(no matches)"

        if name == "grep":
            pattern = arguments["pattern"]
            search_path = _resolve_tool_path(arguments.get("path", cwd or "."), cwd)
            if (
                error := _check_path_policy(search_path, cwd, blocked_dirs)
            ) is not None:
                return _access_denied(error)
            glob_pattern = arguments.get("glob", "**/*")

            results: list[str] = []
            regex = re.compile(pattern)

            if search_path.is_file():
                files = [search_path]
            else:
                files = list(search_path.glob(glob_pattern))

            for f in files[:50]:  # Limit files searched
                if f.is_file():
                    if _check_path_policy(f.resolve(), cwd, blocked_dirs) is not None:
                        continue
                    try:
                        content = f.read_text(encoding="utf-8")
                        for i, line in enumerate(content.split("\n"), 1):
                            if regex.search(line):
                                results.append(f"{f}:{i}:{line}")
                                if len(results) >= 100:
                                    break
                    except (UnicodeDecodeError, PermissionError):
                        continue
                if len(results) >= 100:
                    break

            return "\n".join(results) or "(no matches)"

        return f"Error: Unknown tool: {name}"

    except Exception as e:  # pragma: no cover - guard rail
        return f"Error executing {name}: {e}"
