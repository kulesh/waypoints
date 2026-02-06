"""Evidence parsing — shared constants and functions for execution evidence.

Extracts evidence-related parsing logic shared between the executor
(streaming loop) and receipt_finalizer (finalization pipeline). Both
modules import from here; the executor re-exports for backward compat.
"""

import re
from dataclasses import dataclass

# Pattern to detect acceptance criterion verification markers in agent output
# Model outputs nested elements for reliable parsing:
#   <acceptance-criterion><index>N</index><status>verified|failed</status>
#   <text>...</text><evidence>...</evidence></acceptance-criterion>
CRITERION_PATTERN = re.compile(
    r"<acceptance-criterion>\s*"
    r"<index>(\d+)</index>\s*"
    r"<status>(verified|failed)</status>\s*"
    r"<text>(.*?)</text>\s*"
    r"<evidence>(.*?)</evidence>\s*"
    r"</acceptance-criterion>",
    re.DOTALL,
)

# Pattern to detect validation evidence markers in agent output
# Model outputs these when running tests, linting, formatting
VALIDATION_PATTERN = re.compile(
    r"<validation>\s*"
    r"<command>(.*?)</command>\s*"
    r"<exit-code>(\d+)</exit-code>\s*"
    r"<output>(.*?)</output>\s*"
    r"</validation>",
    re.DOTALL,
)


@dataclass
class FileOperation:
    """A file operation performed by the agent."""

    tool_name: str  # "Edit", "Write", "Read", "Bash", "Glob", "Grep"
    file_path: str | None
    line_number: int | None = None


def extract_file_operation(
    tool_name: str, tool_input: dict[str, object]
) -> FileOperation | None:
    """Extract file operation from tool input.

    Args:
        tool_name: Name of the tool (Edit, Write, Read, Bash, etc.)
        tool_input: The tool input dict containing parameters

    Returns:
        FileOperation if a file path was found, None otherwise
    """
    if tool_name in ("Edit", "Write", "Read"):
        path = tool_input.get("file_path")
        if isinstance(path, str):
            return FileOperation(tool_name=tool_name, file_path=path)
    elif tool_name == "Glob":
        pattern = tool_input.get("pattern")
        if isinstance(pattern, str):
            return FileOperation(tool_name=tool_name, file_path=pattern)
    elif tool_name == "Grep":
        path = tool_input.get("path")
        if isinstance(path, str):
            return FileOperation(tool_name=tool_name, file_path=path)
    elif tool_name == "Bash":
        command = tool_input.get("command")
        if isinstance(command, str):
            display = command[:60] + "..." if len(command) > 60 else command
            return FileOperation(tool_name=tool_name, file_path=display)
    return None


def normalize_command(command: str) -> str:
    """Normalize a shell command for matching."""
    return " ".join(command.strip().split())


def parse_tool_output(output: str) -> tuple[str, str, int]:
    """Parse tool output into stdout, stderr, and exit code."""
    if not output:
        return "", "", 0

    exit_code = 0
    exit_match = re.search(r"\nExit code: (\d+)\s*$", output)
    if exit_match:
        exit_code = int(exit_match.group(1))
        output = output[: exit_match.start()]

    stdout = output
    stderr = ""
    if "\nSTDERR:\n" in output:
        stdout, stderr = output.split("\nSTDERR:\n", 1)

    return stdout.rstrip(), stderr.rstrip(), exit_code


def detect_validation_category(command: str) -> str | None:
    """Detect validation category from command string.

    Args:
        command: The shell command that was run

    Returns:
        Category name (tests, linting, formatting) or None if not recognized
    """
    cmd_lower = command.lower()

    # Test commands
    if any(
        pattern in cmd_lower
        for pattern in ["test", "pytest", "jest", "mocha", "go test", "cargo test"]
    ):
        return "tests"

    if "ruff format" in cmd_lower or "ruff fmt" in cmd_lower:
        return "formatting"

    # Linting commands
    if any(
        pattern in cmd_lower
        for pattern in ["clippy", "ruff", "eslint", "lint", "pylint", "flake8"]
    ):
        return "linting"

    # Formatting commands
    if any(
        pattern in cmd_lower for pattern in ["fmt", "format", "prettier", "rustfmt"]
    ):
        return "formatting"

    # Type checking commands
    if any(pattern in cmd_lower for pattern in ["mypy", "tsc", "typecheck", "pyright"]):
        return "type checking"

    return None
