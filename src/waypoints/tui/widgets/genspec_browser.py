"""TUI widgets for browsing a generative specification."""

from __future__ import annotations

from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static, Tree

from waypoints.genspec.spec import (
    Artifact,
    BundleChecksums,
    BundleMetadata,
    GenerativeSpec,
    GenerativeStep,
    Phase,
)
from waypoints.tui.widgets.content_viewer import ContentViewer

# Phase icons for tree display
PHASE_ICONS = {
    "spark": "⚡",
    "shape_qa": "💬",
    "shape_brief": "📝",
    "shape_spec": "📋",
    "chart": "🗺️",
    "chart_breakdown": "📊",
    "chart_add": "➕",
    "fly": "🚀",
}

# Human-readable phase display names
PHASE_DISPLAY_NAMES = {
    "spark": "Spark",
    "shape_qa": "Shape Q&A",
    "shape_brief": "Shape Brief",
    "shape_spec": "Shape Spec",
    "chart": "Chart",
    "chart_breakdown": "Chart Breakdown",
    "chart_add": "Chart Add",
    "fly": "Fly",
}


class GenSpecTree(Tree[Any]):
    """Tree widget for browsing generative spec phases, steps, and artifacts."""

    DEFAULT_CSS = """
    GenSpecTree {
        height: 1fr;
        padding: 0;
        width: 1fr;
        overflow-x: hidden;
        scrollbar-gutter: stable;
        scrollbar-size: 1 1;
        scrollbar-background: $surface;
        scrollbar-color: $surface-lighten-2;
    }

    GenSpecTree > .tree--guides {
        color: $text-muted;
    }

    GenSpecTree > .tree--cursor {
        background: $surface-lighten-1;
        color: $text;
        text-style: none;
    }

    GenSpecTree:focus > .tree--cursor {
        background: $surface-lighten-2;
        color: $text;
        text-style: bold;
    }
    """

    BINDINGS = [
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
    ]

    def __init__(self, **kwargs: Any) -> None:
        super().__init__("SPEC", **kwargs)
        self._spec: GenerativeSpec | None = None
        self.show_root = False

    def update_spec(self, spec: GenerativeSpec | None) -> None:
        """Update the tree with a generative spec."""
        from rich.text import Text

        self._spec = spec
        self.root.remove_children()

        if not spec:
            return

        # Phase ordering
        phase_order = [
            "spark",
            "shape_qa",
            "shape_brief",
            "shape_spec",
            "chart",
            "chart_breakdown",
            "chart_add",
            "fly",
        ]

        # Add phases with their steps
        summary = spec.summary()
        phases = summary.get("phases", {})

        for phase_name in phase_order:
            if phase_name not in phases:
                continue

            try:
                phase_enum = Phase(phase_name)
                steps = spec.get_steps_by_phase(phase_enum)
            except Exception:
                steps = []

            # Calculate total cost for this phase
            phase_cost = sum(s.metadata.cost_usd for s in steps if s.metadata.cost_usd)

            # Format phase label
            icon = PHASE_ICONS.get(phase_name, "○")
            display_name = PHASE_DISPLAY_NAMES.get(
                phase_name, phase_name.replace("_", " ").title()
            )
            label = Text()
            label.append(f"{icon} ")
            label.append(display_name)
            label.append(f" ({len(steps)})", style="dim")
            if phase_cost > 0:
                label.append(f" ${phase_cost:.2f}", style="green")

            # Add phase as expandable node
            phase_data = {"type": "phase", "name": phase_name}
            phase_node = self.root.add(label, data=phase_data)

            # Add steps under this phase
            for step in steps:
                step_label = Text()
                timestamp = step.timestamp.strftime("%H:%M:%S")

                # For FLY steps, show waypoint ID and iteration
                if phase_name == "fly" and step.input.context:
                    ctx = step.input.context
                    wp_id = ctx.get("waypoint_id", "")
                    iteration = ctx.get("iteration", 1)
                    reason = ctx.get("iteration_reason", "")
                    step_label.append(f"  {wp_id} ")
                    step_label.append(f"iter {iteration}", style="cyan")
                    if reason and reason not in ("initial", "continue"):
                        step_label.append(f" ({reason})", style="yellow")
                else:
                    step_label.append(f"  {timestamp}")

                if step.metadata.cost_usd:
                    step_label.append(f" ${step.metadata.cost_usd:.3f}", style="green")
                phase_node.add_leaf(step_label, data={"type": "step", "step": step})

        # Add artifacts section
        if spec.artifacts:
            artifacts_label = Text()
            artifacts_label.append("📦 Artifacts")
            artifacts_label.append(f" ({len(spec.artifacts)})", style="dim")
            artifacts_node = self.root.add(
                artifacts_label, data={"type": "artifacts_header"}
            )

            for artifact in spec.artifacts:
                art_label = Text()
                atype = artifact.artifact_type.value.replace("_", " ").title()
                chars = len(artifact.content)
                art_label.append(f"  {atype}")
                art_label.append(f" ({chars:,} chars)", style="dim")
                artifacts_node.add_leaf(
                    art_label, data={"type": "artifact", "artifact": artifact}
                )

        # Expand all by default
        self.root.expand_all()

    def select_first(self) -> None:
        """Select the first item in the tree."""
        if self.root.children:
            first = self.root.children[0]
            self.move_cursor(first)


class GenSpecPreviewPanel(VerticalScroll):
    """Right panel showing preview of selected step or artifact."""

    DEFAULT_CSS = """
    GenSpecPreviewPanel {
        width: 1fr;
        height: 100%;
        padding: 1 2;
    }

    GenSpecPreviewPanel .panel-title {
        text-style: bold;
        color: $text;
        padding-bottom: 1;
        border-bottom: solid $surface-lighten-1;
        margin-bottom: 1;
    }

    GenSpecPreviewPanel .placeholder {
        color: $text-muted;
        text-style: italic;
    }

    GenSpecPreviewPanel .section-header {
        text-style: bold;
        color: $text;
        margin-top: 1;
        margin-bottom: 0;
    }

    GenSpecPreviewPanel .meta-line {
        color: $text-muted;
    }

    GenSpecPreviewPanel .content-preview {
        color: $text;
        margin-top: 1;
        padding: 1;
        background: $surface-lighten-1;
    }

    GenSpecPreviewPanel .hint {
        color: $text-disabled;
        text-style: italic;
        margin-top: 2;
    }
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._current_data: Any = None
        self._placeholder_text = "Select a step or artifact to preview"

    def compose(self) -> ComposeResult:
        yield Static("PREVIEW", classes="panel-title")
        yield Static(
            self._placeholder_text,
            classes="placeholder",
            id="preview-placeholder",
        )
        yield Vertical(id="preview-content")

    def set_placeholder(self, text: str) -> None:
        """Update placeholder text shown when nothing is selected."""
        self._placeholder_text = text
        placeholder = self.query_one("#preview-placeholder", Static)
        placeholder.update(text)

    def show_step(self, step: GenerativeStep) -> None:
        """Display a step preview."""
        self._current_data = step
        placeholder = self.query_one("#preview-placeholder")
        content = self.query_one("#preview-content", Vertical)

        content.remove_children()
        placeholder.display = False

        # Step metadata
        phase_name = step.phase.value.replace("_", " ").title()
        content.mount(Static(f"Phase: {phase_name}", classes="meta-line"))
        content.mount(
            Static(
                f"Time: {step.timestamp.strftime('%Y-%m-%d %H:%M:%S')}",
                classes="meta-line",
            )
        )

        # For FLY steps, show iteration context
        if step.phase.value == "fly" and step.input.context:
            ctx = step.input.context
            if ctx.get("waypoint_id"):
                content.mount(
                    Static(f"Waypoint: {ctx['waypoint_id']}", classes="meta-line")
                )
            if ctx.get("waypoint_title"):
                content.mount(
                    Static(f"Title: {ctx['waypoint_title']}", classes="meta-line")
                )
            if ctx.get("iteration"):
                iter_info = f"Iteration: {ctx['iteration']}"
                if ctx.get("iteration_reason"):
                    iter_info += f" ({ctx['iteration_reason']})"
                content.mount(Static(iter_info, classes="meta-line"))

        if step.metadata.cost_usd:
            content.mount(
                Static(f"Cost: ${step.metadata.cost_usd:.4f}", classes="meta-line")
            )
        if step.metadata.tokens_in or step.metadata.tokens_out:
            tokens = f"Tokens: {step.metadata.tokens_in or 0} in"
            tokens += f" / {step.metadata.tokens_out or 0} out"
            content.mount(Static(tokens, classes="meta-line"))
        if step.metadata.model:
            content.mount(Static(f"Model: {step.metadata.model}", classes="meta-line"))

        # Input preview
        content.mount(Static("Input", classes="section-header"))
        if step.input.system_prompt:
            sys_preview = step.input.system_prompt[:200]
            if len(step.input.system_prompt) > 200:
                sys_preview += "..."
            content.mount(Static(f"System: {sys_preview}", classes="content-preview"))

        if step.input.user_prompt:
            user_preview = step.input.user_prompt[:200]
            if len(step.input.user_prompt) > 200:
                user_preview += "..."
            content.mount(Static(f"User: {user_preview}", classes="content-preview"))

        # Output preview
        content.mount(Static("Output", classes="section-header"))
        if step.output.content:
            output_preview = step.output.content[:300]
            if len(step.output.content) > 300:
                output_preview += "..."
            content.mount(Static(output_preview, classes="content-preview"))
        else:
            # FLY steps have no output captured (only inputs)
            content.mount(
                Static("[Generated during execution]", classes="content-preview dim")
            )

        content.mount(Static("Press Enter for full detail", classes="hint"))

    def show_artifact(self, artifact: Artifact) -> None:
        """Display an artifact preview."""
        self._current_data = artifact
        placeholder = self.query_one("#preview-placeholder")
        content = self.query_one("#preview-content", Vertical)

        content.remove_children()
        placeholder.display = False

        # Artifact metadata
        atype = artifact.artifact_type.value.replace("_", " ").title()
        content.mount(Static(f"Type: {atype}", classes="meta-line"))
        content.mount(
            Static(f"Size: {len(artifact.content):,} characters", classes="meta-line")
        )
        if artifact.file_path:
            content.mount(Static(f"File: {artifact.file_path}", classes="meta-line"))

        # Content preview
        content.mount(Static("Content Preview", classes="section-header"))
        preview = artifact.content[:500]
        if len(artifact.content) > 500:
            preview += "\n..."
        content.mount(
            ContentViewer(
                preview, file_path=artifact.file_path, classes="content-preview"
            )
        )

        content.mount(Static("Press Enter for full content", classes="hint"))

    def show_phase(self, phase_name: str, spec: GenerativeSpec) -> None:
        """Display a phase summary."""
        self._current_data = None
        placeholder = self.query_one("#preview-placeholder")
        content = self.query_one("#preview-content", Vertical)

        content.remove_children()
        placeholder.display = False

        display_name = PHASE_DISPLAY_NAMES.get(
            phase_name, phase_name.replace("_", " ").title()
        )
        content.mount(Static(f"Phase: {display_name}", classes="section-header"))

        try:
            phase_enum = Phase(phase_name)
            steps = spec.get_steps_by_phase(phase_enum)
        except Exception:
            steps = []

        content.mount(Static(f"Steps: {len(steps)}", classes="meta-line"))

        total_cost = sum(s.metadata.cost_usd for s in steps if s.metadata.cost_usd)
        if total_cost > 0:
            content.mount(Static(f"Total Cost: ${total_cost:.2f}", classes="meta-line"))

        total_tokens_in = sum(
            s.metadata.tokens_in for s in steps if s.metadata.tokens_in
        )
        total_tokens_out = sum(
            s.metadata.tokens_out for s in steps if s.metadata.tokens_out
        )
        if total_tokens_in or total_tokens_out:
            content.mount(
                Static(
                    f"Total Tokens: {total_tokens_in:,} in / {total_tokens_out:,} out",
                    classes="meta-line",
                )
            )

        content.mount(Static("Expand to see individual steps", classes="hint"))

    def clear(self) -> None:
        """Clear the preview panel."""
        self._current_data = None
        placeholder = self.query_one("#preview-placeholder")
        content = self.query_one("#preview-content", Vertical)
        content.remove_children()
        placeholder.display = True


class StepDetailModal(ModalScreen[None]):
    """Modal for viewing full step details."""

    DEFAULT_CSS = """
    StepDetailModal {
        align: center middle;
        background: $surface 60%;
    }
    StepDetailModal > Vertical {
        width: 80%;
        max-width: 100;
        height: 80%;
        background: $surface;
        border: solid $surface-lighten-2;
        padding: 1 2;
    }
    StepDetailModal .modal-title {
        text-style: bold;
        text-align: center;
        border-bottom: solid $surface-lighten-1;
        padding-bottom: 1;
        margin-bottom: 1;
    }
    StepDetailModal .section {
        text-style: bold;
        margin-top: 1;
    }
    StepDetailModal .scroll-content {
        height: 1fr;
        padding: 0 1;
    }
    """

    BINDINGS = [Binding("escape", "close", "Close")]

    def __init__(self, step: GenerativeStep) -> None:
        super().__init__()
        self.step = step

    def compose(self) -> ComposeResult:
        with Vertical():
            phase = self.step.phase.value.replace("_", " ").title()
            yield Static(f"Step Detail - {phase}", classes="modal-title")
            with VerticalScroll(classes="scroll-content"):
                yield Static("Input", classes="section")
                if self.step.input.system_prompt:
                    yield Static(f"System:\n{self.step.input.system_prompt}")
                if self.step.input.user_prompt:
                    yield Static(f"\nUser:\n{self.step.input.user_prompt}")
                yield Static("\nOutput", classes="section")
                yield Static(self.step.output.content)

    def action_close(self) -> None:
        self.dismiss()


class ArtifactDetailModal(ModalScreen[None]):
    """Modal for viewing full artifact content."""

    DEFAULT_CSS = """
    ArtifactDetailModal {
        align: center middle;
        background: $surface 60%;
    }
    ArtifactDetailModal > Vertical {
        width: 80%;
        max-width: 100;
        height: 80%;
        background: $surface;
        border: solid $surface-lighten-2;
        padding: 1 2;
    }
    ArtifactDetailModal .modal-title {
        text-style: bold;
        text-align: center;
        border-bottom: solid $surface-lighten-1;
        padding-bottom: 1;
        margin-bottom: 1;
    }
    ArtifactDetailModal .scroll-content {
        height: 1fr;
        padding: 0 1;
    }
    """

    BINDINGS = [Binding("escape", "close", "Close")]

    def __init__(self, artifact: Artifact) -> None:
        super().__init__()
        self.artifact = artifact

    def compose(self) -> ComposeResult:
        with Vertical():
            atype = self.artifact.artifact_type.value.replace("_", " ").title()
            yield Static(f"Artifact - {atype}", classes="modal-title")
            with VerticalScroll(classes="scroll-content"):
                yield ContentViewer(
                    self.artifact.content,
                    file_path=self.artifact.file_path,
                )

    def action_close(self) -> None:
        self.dismiss()


class GenSpecBrowser(Horizontal):
    """GenSpec browser with tree + preview + detail views."""

    DEFAULT_CSS = """
    GenSpecBrowser {
        width: 1fr;
        height: 100%;
    }

    GenSpecBrowser .tree-panel {
        width: 35;
        height: 100%;
        border-right: solid $surface-lighten-1;
    }

    GenSpecBrowser .tree-panel .panel-title {
        text-style: bold;
        color: $text;
        padding: 1;
        border-bottom: solid $surface-lighten-1;
    }

    GenSpecBrowser .tree-panel .legend {
        dock: bottom;
        height: auto;
        padding: 0 1;
        border-top: solid $surface-lighten-1;
        color: $text-muted;
    }
    """

    def __init__(
        self,
        *,
        title: str = "GENERATIVE SPEC",
        legend_items: list[str] | None = None,
        show_source_info: bool = False,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._spec: GenerativeSpec | None = None
        self._metadata: BundleMetadata | None = None
        self._checksums: BundleChecksums | None = None
        self._source_label: str | None = None
        self._title = title
        self._legend_items = legend_items or []
        self._show_source_info = show_source_info

    def compose(self) -> ComposeResult:
        with Vertical(classes="tree-panel"):
            yield Static(self._title, classes="panel-title")
            yield GenSpecTree(id="genspec-tree")
            if self._legend_items:
                yield Static("  ".join(self._legend_items), classes="legend")
        yield GenSpecPreviewPanel(id="genspec-preview")

    def set_spec(
        self,
        spec: GenerativeSpec | None,
        *,
        source_label: str | None = None,
        metadata: BundleMetadata | None = None,
        checksums: BundleChecksums | None = None,
        select_first: bool = False,
    ) -> None:
        """Set the spec data for the browser."""
        self._spec = spec
        self._metadata = metadata
        self._checksums = checksums
        self._source_label = source_label

        tree = self.query_one("#genspec-tree", GenSpecTree)
        preview = self.query_one("#genspec-preview", GenSpecPreviewPanel)
        tree.update_spec(spec)
        preview.clear()
        preview.set_placeholder(self._build_placeholder_text())

        if select_first:
            tree.select_first()

    def focus_tree(self) -> None:
        """Focus the tree widget."""
        tree = self.query_one("#genspec-tree", GenSpecTree)
        tree.focus()

    def on_tree_node_highlighted(self, event: Tree.NodeHighlighted[Any]) -> None:
        """Update preview when tree cursor moves."""
        preview = self.query_one("#genspec-preview", GenSpecPreviewPanel)

        if not event.node.data or self._spec is None:
            preview.clear()
            return

        data = event.node.data
        if data.get("type") == "step":
            preview.show_step(data["step"])
        elif data.get("type") == "artifact":
            preview.show_artifact(data["artifact"])
        elif data.get("type") == "phase":
            preview.show_phase(data["name"], self._spec)
        else:
            preview.clear()

    def on_tree_node_selected(self, event: Tree.NodeSelected[Any]) -> None:
        """Handle Enter key - show full detail modal or expand/collapse."""
        if not event.node.data:
            return

        data = event.node.data
        if data.get("type") == "step":
            self._show_step_detail(data["step"])
        elif data.get("type") == "artifact":
            self._show_artifact_detail(data["artifact"])
        # For phases/headers, tree handles expand/collapse automatically

    def _show_step_detail(self, step: GenerativeStep) -> None:
        """Show full step detail in a modal."""
        self.app.push_screen(StepDetailModal(step))

    def _show_artifact_detail(self, artifact: Artifact) -> None:
        """Show full artifact content in a modal."""
        self.app.push_screen(ArtifactDetailModal(artifact))

    def _build_placeholder_text(self) -> str:
        base = "Select a step or artifact to preview"
        if not self._show_source_info:
            return base

        info_lines: list[str] = []
        if self._source_label:
            info_lines.append(f"Source: {self._source_label}")
        if self._metadata:
            info_lines.append(
                f"Bundle: {self._metadata.schema} v{self._metadata.version}"
            )
        if self._checksums:
            info_lines.append(
                f"Checksums: {self._checksums.algorithm} "
                f"({len(self._checksums.files)} files)"
            )
        if not info_lines:
            return base
        return "\n".join([base, "", *info_lines])
