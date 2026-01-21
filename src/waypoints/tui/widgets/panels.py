"""Right panel widgets for phase-specific content."""
from __future__ import annotations

from typing import Any

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Markdown, Static


class RightPanel(VerticalScroll):
    """Base class for phase-specific right panels.

    Subclasses should override compose_content() and update_content().
    """

    DEFAULT_CSS = """
    RightPanel {
        width: 1fr;
        height: 100%;
        border-left: solid $primary-darken-2;
        padding: 1 2;
    }
    RightPanel .panel-title {
        text-style: bold;
        color: $primary;
        padding-bottom: 1;
        border-bottom: solid $surface-lighten-1;
        margin-bottom: 1;
    }
    RightPanel .muted {
        color: $text-muted;
        text-style: italic;
    }
    """

    title: str = "Panel"

    def compose(self) -> ComposeResult:
        yield Static(self.title, classes="panel-title")
        yield from self.compose_content()

    def compose_content(self) -> ComposeResult:
        """Override to provide panel-specific content."""
        yield Static("Override compose_content() in subclass", classes="muted")

    def update_content(self, data: Any) -> None:
        """Update panel content based on dialogue progress."""
        pass


class SpecPanel(RightPanel):
    """Right panel for SHAPE phase - displays evolving product spec."""

    title = "Product Specification"

    def compose_content(self) -> ComposeResult:
        yield Static(
            "Answer questions to build your spec...",
            id="spec-placeholder",
            classes="muted",
        )
        yield Markdown("", id="spec-content")

    def update_content(self, spec_data: dict[str, Any] | None) -> None:
        """Update spec display from Q&A refinement."""
        placeholder = self.query_one("#spec-placeholder")
        content = self.query_one("#spec-content", Markdown)

        if spec_data:
            placeholder.display = False
            md = self._format_spec(spec_data)
            content.update(md)
        else:
            placeholder.display = True
            content.update("")

    def _format_spec(self, data: dict[str, Any]) -> str:
        """Format spec data as Markdown with sections."""
        sections = []

        if title := data.get("title"):
            sections.append(f"# {title}")

        if vision := data.get("vision"):
            sections.append(f"## Vision\n{vision}")

        if problem := data.get("problem"):
            sections.append(f"## Problem\n{problem}")

        if solution := data.get("solution"):
            sections.append(f"## Solution\n{solution}")

        if audience := data.get("audience"):
            sections.append(f"## Target Audience\n{audience}")

        if features := data.get("features"):
            if isinstance(features, list):
                feature_list = "\n".join(f"- {f}" for f in features)
                sections.append(f"## Features\n{feature_list}")
            else:
                sections.append(f"## Features\n{features}")

        if constraints := data.get("constraints"):
            if isinstance(constraints, list):
                constraint_list = "\n".join(f"- {c}" for c in constraints)
                sections.append(f"## Constraints\n{constraint_list}")
            else:
                sections.append(f"## Constraints\n{constraints}")

        if success_criteria := data.get("success_criteria"):
            if isinstance(success_criteria, list):
                criteria_list = "\n".join(f"- {c}" for c in success_criteria)
                sections.append(f"## Success Criteria\n{criteria_list}")
            else:
                sections.append(f"## Success Criteria\n{success_criteria}")

        return "\n\n".join(sections) if sections else "# Specification\n\n(Building...)"
