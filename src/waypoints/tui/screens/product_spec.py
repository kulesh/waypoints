"""Product Specification screen for generating detailed spec from idea brief."""

import logging
from datetime import datetime
from pathlib import Path

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, Header, Markdown, Static, TextArea

from waypoints.llm.client import ChatClient
from waypoints.models import Project
from waypoints.models.dialogue import DialogueHistory
from waypoints.tui.widgets.dialogue import ThinkingIndicator
from waypoints.tui.widgets.status_indicator import ModelStatusIndicator

logger = logging.getLogger(__name__)

SPEC_GENERATION_PROMPT = """\
Based on the Idea Brief below, generate a comprehensive Product Specification.

The specification should be detailed enough for engineers and product managers
to understand exactly what needs to be built. Use Markdown format.

# Product Specification: [Product Name]

## 1. Executive Summary
Brief overview of the product and its value proposition.

## 2. Problem Statement
### 2.1 Current Pain Points
### 2.2 Impact of the Problem
### 2.3 Why Now?

## 3. Target Users
### 3.1 Primary Persona
### 3.2 Secondary Personas
### 3.3 User Journey

## 4. Product Overview
### 4.1 Vision Statement
### 4.2 Core Value Proposition
### 4.3 Key Differentiators

## 5. Features & Requirements
### 5.1 MVP Features (Must Have)
### 5.2 Phase 2 Features (Should Have)
### 5.3 Future Considerations (Nice to Have)

## 6. Technical Considerations
### 6.1 Architecture Overview
### 6.2 Technology Stack Recommendations
### 6.3 Integration Requirements
### 6.4 Security & Privacy

## 7. Success Metrics
### 7.1 Key Performance Indicators
### 7.2 Success Criteria for MVP

## 8. Risks & Mitigations
### 8.1 Technical Risks
### 8.2 Market Risks
### 8.3 Mitigation Strategies

## 9. FAQ
Common questions and answers for the development team.

## 10. Appendix
### 10.1 Glossary
### 10.2 References

---

Here is the Idea Brief to expand:

{brief}

Generate the complete Product Specification now:"""


class ProductSpecScreen(Screen):
    """
    Product Specification screen - Generate detailed spec from idea brief.

    Displays the generated spec with editing capability.
    User can edit and then proceed to Waypoints planning.
    """

    BINDINGS = [
        Binding("ctrl+q", "quit", "Quit", show=True),
        Binding("ctrl+enter", "proceed_to_waypoints", "Continue", show=True),
        Binding("ctrl+e", "toggle_edit", "Edit", show=True),
        Binding("ctrl+s", "save", "Save", show=True),
    ]

    DEFAULT_CSS = """
    ProductSpecScreen {
        background: $surface;
        overflow: hidden;
    }

    ProductSpecScreen .content {
        width: 100%;
        height: 1fr;
        padding: 1 2;
    }

    ProductSpecScreen .generating {
        width: 100%;
        height: 100%;
        align: center middle;
    }

    ProductSpecScreen .generating Static {
        color: $text-muted;
    }

    ProductSpecScreen Markdown {
        width: 100%;
        padding: 0 2;
        background: transparent;
    }

    ProductSpecScreen TextArea {
        width: 100%;
        height: 1fr;
        border: none;
        background: transparent;
        display: none;
    }

    ProductSpecScreen TextArea.editing {
        display: block;
    }

    ProductSpecScreen VerticalScroll.editing {
        display: none;
    }

    ProductSpecScreen .file-path {
        dock: bottom;
        color: $text-muted;
        padding: 0 2;
    }

    ProductSpecScreen ModelStatusIndicator {
        dock: top;
        layer: above;
        margin: 0 0 0 1;
        height: 1;
        width: 2;
    }
    """

    def __init__(
        self,
        project: Project,
        idea: str | None = None,
        brief: str | None = None,
        history: DialogueHistory | None = None,
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)
        self.project = project
        self.idea = idea or ""
        self.brief = brief or ""
        self.history = history
        self.spec_content: str = ""
        self.is_editing: bool = False
        self.llm_client = ChatClient()
        self.file_path = self._generate_file_path()

    def _generate_file_path(self) -> Path:
        """Generate a unique file path for this spec."""
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        docs_dir = self.project.get_docs_path()
        docs_dir.mkdir(parents=True, exist_ok=True)
        return docs_dir / f"product-spec-{timestamp}.md"

    def compose(self) -> ComposeResult:
        yield Header()
        yield ModelStatusIndicator(id="model-status")
        with VerticalScroll(classes="content", id="spec-content"):
            with Vertical(classes="generating", id="generating-view"):
                yield ThinkingIndicator()
                yield Static("Generating product specification...")
            yield Markdown("", id="spec-display")
        yield TextArea(id="spec-editor")
        yield Static(str(self.file_path), classes="file-path", id="file-path")
        yield Footer()

    def on_mount(self) -> None:
        """Start generating the specification."""
        self.app.sub_title = f"{self.project.name} · Product Spec"
        self._generate_spec()

    @work(thread=True)
    def _generate_spec(self) -> None:
        """Generate product specification from idea brief."""
        prompt = SPEC_GENERATION_PROMPT.format(brief=self.brief)

        logger.info("Generating product spec from brief: %d chars", len(self.brief))

        # Start thinking indicator
        self.app.call_from_thread(self._set_thinking, True)

        spec_content = ""

        system_prompt = (
            "You are a senior product manager creating detailed "
            "product specifications. Be thorough but practical."
        )
        try:
            for chunk in self.llm_client.stream_message(
                messages=[{"role": "user", "content": prompt}],
                system=system_prompt,
            ):
                spec_content += chunk
                self.app.call_from_thread(self._update_spec_display, spec_content)
        except Exception as e:
            logger.exception("Error generating spec: %s", e)
            spec_content = f"# Error\n\nFailed to generate specification: {e}"
            self.app.call_from_thread(self.notify, f"Error: {e}", severity="error")

        self.spec_content = spec_content
        self.app.call_from_thread(self._finalize_spec)
        # Stop thinking indicator
        self.app.call_from_thread(self._set_thinking, False)

    def _set_thinking(self, thinking: bool) -> None:
        """Toggle the model status indicator."""
        self.query_one("#model-status", ModelStatusIndicator).set_thinking(thinking)

    def _update_spec_display(self, content: str) -> None:
        """Update the spec display during streaming."""
        self.query_one("#generating-view").display = False
        display = self.query_one("#spec-display", Markdown)
        display.update(content)

    def _finalize_spec(self) -> None:
        """Finalize spec display after generation and save to disk."""
        editor = self.query_one("#spec-editor", TextArea)
        editor.text = self.spec_content
        self._save_to_disk()
        logger.info("Spec generation complete: %d chars", len(self.spec_content))

    def _save_to_disk(self) -> None:
        """Save the spec content to disk."""
        try:
            self.file_path.write_text(self.spec_content)
            logger.info("Saved spec to %s", self.file_path)
            self.notify(f"Saved to {self.file_path.name}", severity="information")
        except OSError as e:
            logger.exception("Failed to save spec: %s", e)
            self.notify(f"Failed to save: {e}", severity="error")

    def action_toggle_edit(self) -> None:
        """Toggle between view and edit modes."""
        self.is_editing = not self.is_editing

        content_scroll = self.query_one("#spec-content", VerticalScroll)
        editor = self.query_one("#spec-editor", TextArea)

        if self.is_editing:
            content_scroll.add_class("editing")
            editor.add_class("editing")
            editor.text = self.spec_content
            editor.focus()
            self.app.sub_title = f"{self.project.name} · Product Spec (editing)"
        else:
            # Save edits
            self.spec_content = editor.text
            display = self.query_one("#spec-display", Markdown)
            display.update(self.spec_content)
            content_scroll.remove_class("editing")
            editor.remove_class("editing")
            self._save_to_disk()
            self.app.sub_title = f"{self.project.name} · Product Spec"

    def action_save(self) -> None:
        """Save the current content to disk."""
        if self.is_editing:
            editor = self.query_one("#spec-editor", TextArea)
            self.spec_content = editor.text
        self._save_to_disk()

    def action_proceed_to_waypoints(self) -> None:
        """Proceed to CHART phase (waypoint planning)."""
        if self.is_editing:
            editor = self.query_one("#spec-editor", TextArea)
            self.spec_content = editor.text

        # Save before proceeding
        self._save_to_disk()

        self.app.switch_phase(  # type: ignore
            "chart",
            {
                "project": self.project,
                "spec": self.spec_content,
                "idea": self.idea,
                "brief": self.brief,
                "history": self.history,
            },
        )
