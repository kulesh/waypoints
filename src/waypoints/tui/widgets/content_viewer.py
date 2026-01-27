"""Content viewer widget with file-type-aware rendering."""

from pathlib import Path

from rich.syntax import Syntax
from textual.app import ComposeResult
from textual.widgets import Markdown, Static


class ContentViewer(Static):
    """Widget that renders content based on file type.

    Supports:
    - Markdown (.md): Rendered with Textual's Markdown widget
    - JSON/JSONL (.json, .jsonl): Syntax highlighted
    - Plain text: Raw display
    """

    DEFAULT_CSS = """
    ContentViewer {
        height: auto;
    }
    """

    def __init__(
        self,
        content: str,
        file_path: str | None = None,
        *,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        self._content = content
        self._file_path = file_path
        self._content_type = self._detect_content_type()
        super().__init__(name=name, id=id, classes=classes)

    def _detect_content_type(self) -> str:
        """Detect content type from file extension."""
        if not self._file_path:
            return "text"
        ext = Path(self._file_path).suffix.lower()
        if ext == ".md":
            return "markdown"
        elif ext in (".json", ".jsonl"):
            return "json"
        return "text"

    def compose(self) -> ComposeResult:
        """Compose the content based on detected type."""
        if self._content_type == "markdown":
            yield Markdown(self._content)
        elif self._content_type == "json":
            syntax = Syntax(self._content, "json", theme="monokai", line_numbers=False)
            yield Static(syntax)
        else:
            yield Static(self._content)
