"""File preview modal for viewing file contents with syntax highlighting.

Reusable modal that can be used across the app to preview files
before optionally opening them in an external editor.
"""

import os
import subprocess
from pathlib import Path

from rich.syntax import Syntax
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import ScrollableContainer, Vertical
from textual.screen import ModalScreen
from textual.widgets import Static

# TUI editors that need app suspension
TUI_EDITORS = {"vim", "nvim", "vi", "emacs", "nano", "micro", "helix", "ne", "joe"}


def _get_language_from_path(file_path: Path) -> str:
    """Infer language from file extension for syntax highlighting."""
    ext_map = {
        ".py": "python",
        ".js": "javascript",
        ".ts": "typescript",
        ".tsx": "tsx",
        ".jsx": "jsx",
        ".rs": "rust",
        ".go": "go",
        ".rb": "ruby",
        ".java": "java",
        ".kt": "kotlin",
        ".c": "c",
        ".cpp": "cpp",
        ".h": "c",
        ".hpp": "cpp",
        ".cs": "csharp",
        ".swift": "swift",
        ".php": "php",
        ".sh": "bash",
        ".bash": "bash",
        ".zsh": "zsh",
        ".fish": "fish",
        ".json": "json",
        ".jsonl": "json",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".toml": "toml",
        ".xml": "xml",
        ".html": "html",
        ".css": "css",
        ".scss": "scss",
        ".sass": "sass",
        ".less": "less",
        ".md": "markdown",
        ".sql": "sql",
        ".dockerfile": "dockerfile",
        ".tf": "terraform",
        ".ex": "elixir",
        ".exs": "elixir",
        ".erl": "erlang",
        ".hs": "haskell",
        ".ml": "ocaml",
        ".lua": "lua",
        ".r": "r",
        ".R": "r",
        ".jl": "julia",
        ".nim": "nim",
        ".zig": "zig",
        ".v": "v",
        ".vue": "vue",
        ".svelte": "svelte",
    }
    suffix = file_path.suffix.lower()
    return ext_map.get(suffix, "text")


class FilePreviewModal(ModalScreen[bool]):
    """Modal showing file content with syntax highlighting.

    Args:
        file_path: Path to the file to preview
        line: Optional line number to highlight and center on
        context_lines: Number of lines to show around the target line

    Returns:
        True if file was opened in editor, False otherwise
    """

    BINDINGS = [
        Binding("e", "open_editor", "Open in Editor", show=True),
        Binding("escape", "close", "Close", show=True),
        Binding("q", "close", "Close", show=False),
    ]

    DEFAULT_CSS = """
    FilePreviewModal {
        align: center middle;
    }

    FilePreviewModal > Vertical {
        width: 90%;
        max-width: 120;
        height: 80%;
        background: $surface;
        border: thick $primary;
    }

    FilePreviewModal .modal-title {
        height: 3;
        padding: 1 2;
        background: $surface-lighten-1;
        border-bottom: solid $surface-lighten-2;
    }

    FilePreviewModal .file-path {
        text-style: bold;
    }

    FilePreviewModal .line-info {
        color: $text-muted;
    }

    FilePreviewModal .content-container {
        height: 1fr;
        overflow-y: auto;
        padding: 0 1;
    }

    FilePreviewModal .modal-footer {
        height: 3;
        padding: 1 2;
        background: $surface-lighten-1;
        border-top: solid $surface-lighten-2;
        color: $text-muted;
    }

    FilePreviewModal .error-message {
        color: $error;
        padding: 2;
        text-align: center;
    }
    """

    def __init__(
        self,
        file_path: Path,
        line: int | None = None,
        context_lines: int | None = None,
    ) -> None:
        super().__init__()
        self.file_path = file_path
        self.highlight_line = line
        self.context_lines = context_lines
        self._content: str | None = None
        self._error: str | None = None

    def compose(self) -> ComposeResult:
        # Try to read the file
        try:
            self._content = self.file_path.read_text()
        except FileNotFoundError:
            self._error = f"File not found: {self.file_path}"
        except PermissionError:
            self._error = f"Permission denied: {self.file_path}"
        except Exception as e:
            self._error = f"Error reading file: {e}"

        with Vertical():
            # Title bar
            with Vertical(classes="modal-title"):
                yield Static(str(self.file_path), classes="file-path")
                if self.highlight_line:
                    yield Static(f"Line {self.highlight_line}", classes="line-info")

            # Content area
            with ScrollableContainer(classes="content-container"):
                if self._error:
                    yield Static(self._error, classes="error-message")
                else:
                    yield Static(self._build_file_content(), id="file-content")

            # Footer
            yield Static(
                "[e] Open in Editor    [Esc/q] Close",
                classes="modal-footer",
            )

    def _build_file_content(self) -> Syntax | Text:
        """Render file content with syntax highlighting."""
        if self._content is None:
            return Text("No content")

        language = _get_language_from_path(self.file_path)

        # Determine line range to show
        start_line = 1
        if self.context_lines is not None and self.highlight_line:
            start_line = max(1, self.highlight_line - self.context_lines)
            end_line = self.highlight_line + self.context_lines
            lines = self._content.split("\n")
            content = "\n".join(lines[start_line - 1 : end_line])
        else:
            content = self._content

        # Create syntax-highlighted content
        highlight_lines = None
        if self.highlight_line:
            # Adjust for context if showing subset
            if self.context_lines is not None:
                adjusted_line = self.highlight_line - start_line + 1
                highlight_lines = {adjusted_line}
            else:
                highlight_lines = {self.highlight_line}

        return Syntax(
            content,
            language,
            theme="monokai",
            line_numbers=True,
            start_line=start_line,
            highlight_lines=highlight_lines,
            word_wrap=False,
        )

    def on_mount(self) -> None:
        """Scroll to highlighted line after mounting."""
        if self.highlight_line and self._content and not self._error:
            # Schedule scroll after render
            self.call_after_refresh(self._scroll_to_line)

    def _scroll_to_line(self) -> None:
        """Scroll the container to show the highlighted line."""
        try:
            container = self.query_one(".content-container", ScrollableContainer)
            # Estimate line height and scroll position
            # Each line is roughly 1 unit high in the terminal
            if self.highlight_line:
                target_y = max(0, self.highlight_line - 10)  # Show some context above
                container.scroll_to(y=target_y, animate=False)
        except Exception:
            pass  # Ignore scroll errors

    def action_close(self) -> None:
        """Close the modal without opening editor."""
        self.dismiss(False)

    def action_open_editor(self) -> None:
        """Open file in $EDITOR, handling both GUI and TUI editors."""
        editor = os.environ.get("EDITOR", "vim")
        line = self.highlight_line or 1
        editor_name = Path(editor).stem

        # Build editor command with line number
        if editor_name in ("vim", "nvim", "vi"):
            args = [editor, f"+{line}", str(self.file_path)]
        elif editor_name == "emacs":
            args = [editor, f"+{line}", str(self.file_path)]
        elif editor_name in ("nano", "micro"):
            args = [editor, f"+{line}", str(self.file_path)]
        elif editor_name == "helix":
            args = [editor, f"{self.file_path}:{line}"]
        elif editor_name in ("code", "cursor", "zed", "subl", "sublime"):
            # GUI editors use file:line format
            args = [editor, f"{self.file_path}:{line}"]
        else:
            # Default: try file:line format
            args = [editor, f"{self.file_path}:{line}"]

        if editor_name in TUI_EDITORS:
            # TUI editors: suspend app, run editor (blocking), resume
            self.dismiss(True)  # Dismiss first to clean up modal
            self.app.call_later(self._run_tui_editor, args)
        else:
            # GUI editors: non-blocking
            subprocess.Popen(args)
            self.dismiss(True)

    def _run_tui_editor(self, args: list[str]) -> None:
        """Run a TUI editor with app suspension."""
        with self.app.suspend():
            subprocess.run(args)
