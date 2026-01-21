"""Shared tool execution for LLM providers."""
from __future__ import annotations

from pathlib import Path
from typing import Any


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
    import subprocess

    try:
        if name == "read_file":
            path = Path(arguments["file_path"])
            if not path.is_absolute() and cwd:
                path = Path(cwd) / path
            if not path.exists():
                return f"Error: File not found: {path}"
            return path.read_text()

        if name == "write_file":
            path = Path(arguments["file_path"])
            if not path.is_absolute() and cwd:
                path = Path(cwd) / path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(arguments["content"])
            return f"Successfully wrote to {path}"

        if name == "edit_file":
            path = Path(arguments["file_path"])
            if not path.is_absolute() and cwd:
                path = Path(cwd) / path
            if not path.exists():
                return f"Error: File not found: {path}"
            content = path.read_text()
            old_string = arguments["old_string"]
            new_string = arguments["new_string"]
            if old_string not in content:
                return "Error: old_string not found in file"
            new_content = content.replace(old_string, new_string, 1)
            path.write_text(new_content)
            return f"Successfully edited {path}"

        if name == "bash":
            command = arguments["command"]
            raw_timeout = arguments.get("timeout")
            timeout: float = 120.0
            if isinstance(raw_timeout, (int, float)):
                timeout = raw_timeout / 1000 if raw_timeout > 1000 else raw_timeout
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                cwd=cwd,
                timeout=timeout,
            )
            output = result.stdout
            if result.stderr:
                output += f"\nSTDERR:\n{result.stderr}"
            if result.returncode != 0:
                output += f"\nExit code: {result.returncode}"
            return output or "(no output)"

        if name == "glob":
            pattern = arguments["pattern"]
            search_path = Path(arguments.get("path", cwd or "."))
            if not search_path.is_absolute() and cwd:
                search_path = Path(cwd) / search_path
            matches = list(search_path.glob(pattern))
            return "\n".join(str(m) for m in matches[:100]) or "(no matches)"

        if name == "grep":
            pattern = arguments["pattern"]
            search_path = Path(arguments.get("path", cwd or "."))
            if not search_path.is_absolute() and cwd:
                search_path = Path(cwd) / search_path
            glob_pattern = arguments.get("glob", "**/*")

            results: list[str] = []
            regex = re.compile(pattern)

            if search_path.is_file():
                files = [search_path]
            else:
                files = list(search_path.glob(glob_pattern))

            for f in files[:50]:  # Limit files searched
                if f.is_file():
                    try:
                        content = f.read_text()
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
