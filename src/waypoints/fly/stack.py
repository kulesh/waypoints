"""Technology stack detection for waypoint projects.

Detects project stack from manifest files and provides stack-specific
validation commands for linting, testing, type checking, and formatting.
"""

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class StackType(Enum):
    """Known technology stacks with validation tooling."""

    PYTHON = "python"
    TYPESCRIPT = "typescript"
    JAVASCRIPT = "javascript"
    GO = "go"
    RUST = "rust"
    SWIFT = "swift"


@dataclass
class ValidationCommand:
    """A validation command for a stack."""

    name: str  # "linting", "tests", etc.
    command: str  # "ruff check .", "pytest", etc.
    category: str  # "lint", "test", "type", "format"
    optional: bool = False
    cwd: Path | None = None


@dataclass
class StackConfig:
    """Validation configuration for a detected stack."""

    stack_type: StackType
    commands: list[ValidationCommand] = field(default_factory=list)
    root_path: Path | None = None


# Default validation commands for each stack
STACK_COMMANDS: dict[StackType, list[ValidationCommand]] = {
    StackType.PYTHON: [
        ValidationCommand("linting", "ruff check .", "lint"),
        ValidationCommand("tests", "pytest -v", "test"),
        ValidationCommand("type checking", "mypy .", "type"),
        ValidationCommand("formatting", "ruff format --check .", "format"),
    ],
    StackType.TYPESCRIPT: [
        ValidationCommand("linting", "npm run lint", "lint"),
        ValidationCommand("tests", "npm test", "test"),
        ValidationCommand("type checking", "npx tsc --noEmit", "type"),
    ],
    StackType.JAVASCRIPT: [
        ValidationCommand("linting", "npm run lint", "lint"),
        ValidationCommand("tests", "npm test", "test"),
    ],
    StackType.GO: [
        ValidationCommand("tests", "go test ./...", "test"),
        ValidationCommand("vetting", "go vet ./...", "lint"),
    ],
    StackType.RUST: [
        ValidationCommand("tests", "cargo test", "test"),
        ValidationCommand("linting", "cargo clippy -- -D warnings", "lint"),
        ValidationCommand("formatting", "cargo fmt --check", "format"),
    ],
    StackType.SWIFT: [
        ValidationCommand("build", "swift build", "build"),
        ValidationCommand("tests", "swift test", "test"),
    ],
}


def _detect_stacks_at(directory: Path) -> list[StackConfig]:
    """Detect technology stacks in a single directory.

    Args:
        directory: Path to check for manifest files.

    Returns:
        List of detected stacks with root_path set to directory.
    """
    configs: list[StackConfig] = []

    # Python
    python_markers = ["pyproject.toml", "setup.py", "requirements.txt"]
    if any((directory / f).exists() for f in python_markers):
        configs.append(
            StackConfig(
                StackType.PYTHON, list(STACK_COMMANDS[StackType.PYTHON]), directory
            )
        )

    # TypeScript/JavaScript
    if (directory / "package.json").exists():
        if (directory / "tsconfig.json").exists():
            configs.append(
                StackConfig(
                    StackType.TYPESCRIPT,
                    list(STACK_COMMANDS[StackType.TYPESCRIPT]),
                    directory,
                )
            )
        else:
            configs.append(
                StackConfig(
                    StackType.JAVASCRIPT,
                    list(STACK_COMMANDS[StackType.JAVASCRIPT]),
                    directory,
                )
            )

    # Go
    if (directory / "go.mod").exists():
        configs.append(
            StackConfig(StackType.GO, list(STACK_COMMANDS[StackType.GO]), directory)
        )

    # Rust
    if (directory / "Cargo.toml").exists():
        configs.append(
            StackConfig(StackType.RUST, list(STACK_COMMANDS[StackType.RUST]), directory)
        )

    # Swift Package Manager
    if (directory / "Package.swift").exists():
        configs.append(
            StackConfig(
                StackType.SWIFT, list(STACK_COMMANDS[StackType.SWIFT]), directory
            )
        )

    return configs


def detect_stack(project_path: Path) -> list[StackConfig]:
    """Detect technology stack(s) from project manifest files.

    Checks for manifest files at the project root first. If none are found,
    searches immediate (depth-1) non-hidden subdirectories. This handles
    projects where an LLM places source in a nested directory like
    ``project/subdir/Cargo.toml``.

    Args:
        project_path: Path to the project directory.

    Returns:
        List of detected stacks with root_path indicating where each
        manifest was found.
    """
    configs = _detect_stacks_at(project_path)
    if configs:
        return configs

    # Search immediate subdirectories (depth=1), skip hidden dirs
    for child in sorted(project_path.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        configs.extend(_detect_stacks_at(child))

    return configs


def detect_stack_from_spec(spec_content: str) -> list[StackType]:
    """Extract tech stack hints from product spec.

    Looks for technology keywords in the spec content.
    Used as fallback when no project files exist yet (greenfield).

    Args:
        spec_content: The product specification text.

    Returns:
        List of detected stack types.
    """
    stacks: list[StackType] = []
    spec_lower = spec_content.lower()

    # Python keywords
    python_keywords = ["python", "django", "flask", "fastapi", "pytest", "pip"]
    if any(kw in spec_lower for kw in python_keywords):
        stacks.append(StackType.PYTHON)

    # TypeScript keywords
    ts_keywords = ["typescript", "react", "next.js", "nextjs", "angular", "vue"]
    if any(kw in spec_lower for kw in ts_keywords):
        stacks.append(StackType.TYPESCRIPT)

    # JavaScript (only if not already TypeScript)
    js_keywords = ["javascript", "node.js", "nodejs", "express"]
    if StackType.TYPESCRIPT not in stacks:
        if any(kw in spec_lower for kw in js_keywords):
            stacks.append(StackType.JAVASCRIPT)

    # Go keywords
    go_keywords = ["golang", "go language", " go ", "gin", "echo framework"]
    if any(kw in spec_lower for kw in go_keywords):
        stacks.append(StackType.GO)

    # Rust keywords
    rust_keywords = ["rust", "cargo", "tokio", "actix"]
    if any(kw in spec_lower for kw in rust_keywords):
        stacks.append(StackType.RUST)

    # Swift keywords (package-oriented workflows)
    swift_keywords = ["swift package", "package.swift", "swiftpm", "swift test"]
    if any(kw in spec_lower for kw in swift_keywords):
        stacks.append(StackType.SWIFT)

    return stacks


def build_validation_section(
    stack_configs: list[StackConfig],
    validation_overrides: dict[str, str] | None = None,
) -> str:
    """Build validation commands section for inclusion in prompts.

    Args:
        stack_configs: List of detected stack configurations.
        validation_overrides: Optional dict mapping category to override command.

    Returns:
        Formatted string for prompt inclusion.
    """
    if not stack_configs:
        return "Interpret checklist items based on the project's technology stack."

    overrides = validation_overrides or {}
    lines = ["Run these validation commands before marking complete:\n"]

    for config in stack_configs:
        lines.append(f"**{config.stack_type.value.title()}:**")
        for cmd in config.commands:
            # Check for user override
            actual_cmd = overrides.get(cmd.category, cmd.command)
            optional_note = " (optional)" if cmd.optional else ""
            lines.append(f"- {cmd.name}: `{actual_cmd}`{optional_note}")
        lines.append("")

    lines.append("If any command fails, fix the issues before proceeding.")
    return "\n".join(lines)
