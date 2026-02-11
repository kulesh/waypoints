"""Execution log widget for Fly screen."""

from __future__ import annotations

import re
from typing import Any

from rich.syntax import Syntax
from rich.text import Text
from textual.widgets import RichLog

# Regex patterns for markdown
CODE_BLOCK_PATTERN = re.compile(r"```(\w+)?\n(.*?)```", re.DOTALL)
BOLD_PATTERN = re.compile(r"\*\*(.+?)\*\*")
ITALIC_PATTERN = re.compile(r"(?<!\*)\*([^*]+?)\*(?!\*)")
INLINE_CODE_PATTERN = re.compile(r"`([^`]+)`")


def _markdown_to_rich_text(text: str, base_style: str = "") -> Text:
    """Convert markdown and Rich markup formatting to Rich Text.

    Handles:
    - Rich markup: [green]text[/], [bold]text[/], etc.
    - Markdown: **bold**, *italic*, `inline code`
    """
    # Check if text contains Rich markup tags - use Rich's built-in processing
    if "[" in text and "[/" in text:
        result = Text.from_markup(text)
        if base_style:
            result.stylize(base_style)
        return result

    # Otherwise, process markdown patterns
    result = Text()

    # Process the text character by character, tracking markdown patterns
    # This is a simplified approach - process patterns in order of precedence
    remaining = text
    while remaining:
        # Try to find the earliest markdown pattern
        bold_match = BOLD_PATTERN.search(remaining)
        italic_match = ITALIC_PATTERN.search(remaining)
        code_match = INLINE_CODE_PATTERN.search(remaining)

        # Find the earliest match
        matches_with_none: list[tuple[re.Match[str] | None, str]] = [
            (bold_match, "bold"),
            (italic_match, "italic"),
            (code_match, "code"),
        ]
        matches: list[tuple[re.Match[str], str]] = [
            (m, t) for m, t in matches_with_none if m is not None
        ]

        if not matches:
            # No more patterns - add remaining text
            result.append(remaining, style=base_style)
            break

        # Get earliest match
        earliest_match, match_type = min(matches, key=lambda x: x[0].start())

        # Add text before the match
        if earliest_match.start() > 0:
            result.append(remaining[: earliest_match.start()], style=base_style)

        # Add the formatted text
        inner_text = earliest_match.group(1)
        if match_type == "bold":
            style = f"{base_style} bold" if base_style else "bold"
            result.append(inner_text, style=style)
        elif match_type == "italic":
            style = f"{base_style} italic" if base_style else "italic"
            result.append(inner_text, style=style)
        elif match_type == "code":
            result.append(inner_text, style="cyan")

        # Continue with remaining text
        remaining = remaining[earliest_match.end() :]

    return result


class ExecutionLog(RichLog):
    """Rich log for execution output with syntax highlighting."""

    DEFAULT_CSS = """
    ExecutionLog {
        height: 1fr;
        padding: 1;
        background: $surface;
        scrollbar-gutter: stable;
        scrollbar-size: 1 1;
        scrollbar-size-vertical: 1;
        scrollbar-size-horizontal: 1;
        scrollbar-background: $surface;
        scrollbar-background-hover: $surface;
        scrollbar-background-active: $surface;
        scrollbar-color: $surface-lighten-2;
        scrollbar-color-hover: $surface-lighten-3;
        scrollbar-color-active: $surface-lighten-3;
        scrollbar-corner-color: $surface;
        link-color: cyan;
        link-style: underline;
    }
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(highlight=True, markup=True, wrap=True, **kwargs)

    def write_log(self, message: str, level: str = "info") -> None:
        """Add a log entry with Rich formatting."""
        # Apply level-based styling
        style_map = {
            "info": "",
            "success": "green",
            "error": "red bold",
            "command": "yellow",
            "heading": "bold cyan",
        }
        style = style_map.get(level, "")

        # Process message for code blocks and markdown
        formatted = self._format_message(message, style)
        if isinstance(formatted, list):
            for item in formatted:
                self.write(item)
        else:
            self.write(formatted)

    def _format_message(
        self, message: str, default_style: str
    ) -> Text | list[Text | Syntax]:
        """Format message, extracting code blocks for syntax highlighting."""
        # Check for code blocks
        matches = list(CODE_BLOCK_PATTERN.finditer(message))
        if not matches:
            # No code blocks - convert markdown and return styled text
            return _markdown_to_rich_text(message, default_style)

        # Has code blocks - split and format
        result: list[Text | Syntax] = []
        last_end = 0

        for match in matches:
            # Add text before code block (with markdown conversion)
            if match.start() > last_end:
                before_text = message[last_end : match.start()].strip()
                if before_text:
                    result.append(_markdown_to_rich_text(before_text, default_style))

            # Add syntax-highlighted code block
            lang = match.group(1) or "text"
            code = match.group(2).strip()
            result.append(
                Syntax(
                    code,
                    lang,
                    theme="monokai",
                    line_numbers=False,
                    word_wrap=True,
                )
            )
            last_end = match.end()

        # Add remaining text after last code block
        if last_end < len(message):
            after_text = message[last_end:].strip()
            if after_text:
                result.append(_markdown_to_rich_text(after_text, default_style))

        return result

    def log_command(self, command: str) -> None:
        """Log a command being executed."""
        self.write(Text(f"$ {command}", style="yellow bold"))

    def log_success(self, message: str) -> None:
        """Log a success message."""
        self.write(Text(f"✓ {message}", style="green bold"))

    def log_error(self, message: str) -> None:
        """Log an error message."""
        self.write(Text(f"✗ {message}", style="red bold"))

    def log_heading(self, message: str) -> None:
        """Log a heading/section marker."""
        self.write(Text(f"── {message} ──", style="cyan bold"))

    def clear_log(self) -> None:
        """Clear all log entries."""
        self.clear()
