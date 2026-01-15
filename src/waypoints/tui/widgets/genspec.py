"""TUI widgets for viewing and interacting with generative specifications."""

import logging
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, RadioButton, RadioSet, Static

if TYPE_CHECKING:
    from waypoints.tui.app import WaypointsApp

from waypoints.genspec import GenerativeSpec, export_project, export_to_file
from waypoints.models.project import Project

logger = logging.getLogger(__name__)


class GenSpecViewerModal(ModalScreen[None]):
    """Modal for viewing a generative specification summary."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=True),
        Binding("e", "export", "Export", show=True),
    ]

    DEFAULT_CSS = """
    GenSpecViewerModal {
        align: center middle;
        background: $surface 60%;
    }

    GenSpecViewerModal > Vertical {
        width: 60;
        height: auto;
        max-height: 80%;
        background: $surface;
        border: solid $surface-lighten-2;
        padding: 1 2;
    }

    GenSpecViewerModal .modal-title {
        text-style: bold;
        color: $text;
        text-align: center;
        padding: 1 0;
        margin-bottom: 1;
        border-bottom: solid $surface-lighten-1;
    }

    GenSpecViewerModal .modal-content {
        padding: 0 1;
    }

    GenSpecViewerModal .section-title {
        text-style: bold;
        color: $text;
        margin-top: 1;
    }

    GenSpecViewerModal .stat-row {
        color: $text-muted;
        padding-left: 2;
    }

    GenSpecViewerModal .modal-actions {
        dock: bottom;
        height: auto;
        padding: 1 0 0 0;
        margin-top: 1;
        border-top: solid $surface-lighten-1;
        align: center middle;
    }

    GenSpecViewerModal Button {
        margin: 0 1;
        min-width: 10;
        height: 3;
        background: $surface-lighten-1;
    }
    """

    def __init__(
        self,
        spec: GenerativeSpec,
        project: Project,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.spec = spec
        self.project = project

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("Generative Specification", classes="modal-title")

            with Vertical(classes="modal-content"):
                # Summary section
                yield Static("Summary", classes="section-title")
                yield Static("", id="summary-content", classes="stat-row")

                # Phases section
                yield Static("Phases", classes="section-title")
                yield Static("", id="phases-content", classes="stat-row")

                # Artifacts section
                yield Static("Artifacts", classes="section-title")
                yield Static("", id="artifacts-content", classes="stat-row")

            with Horizontal(classes="modal-actions"):
                yield Button("Export", id="export-btn")
                yield Button("Close", id="close-btn")

    def on_mount(self) -> None:
        """Populate the spec viewer with data."""
        summary = self.spec.summary()

        # Summary content
        summary_lines = [
            f"Project: {summary['source_project']}",
            f"Steps: {summary['total_steps']}",
            f"Decisions: {summary['total_decisions']}",
            f"Artifacts: {summary['total_artifacts']}",
        ]
        if summary.get("total_cost_usd"):
            summary_lines.append(f"Cost: ${summary['total_cost_usd']:.2f}")
        if summary.get("model"):
            summary_lines.append(f"Model: {summary['model']}")

        self.query_one("#summary-content", Static).update("\n".join(summary_lines))

        # Phases content
        phases = summary.get("phases", {})
        if phases:
            phase_lines = [
                f"{phase}: {count} steps" for phase, count in phases.items()
            ]
            self.query_one("#phases-content", Static).update("\n".join(phase_lines))
        else:
            self.query_one("#phases-content", Static).update("No phases recorded")

        # Artifacts content
        artifact_lines = []
        for artifact in self.spec.artifacts:
            atype = artifact.artifact_type.value
            chars = len(artifact.content)
            artifact_lines.append(f"{atype}: {chars:,} chars")
        self.query_one("#artifacts-content", Static).update(
            "\n".join(artifact_lines) if artifact_lines else "No artifacts"
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses."""
        if event.button.id == "close-btn":
            self.dismiss()
        elif event.button.id == "export-btn":
            self._do_export()

    def action_cancel(self) -> None:
        """Close the modal."""
        self.dismiss()

    def action_export(self) -> None:
        """Export the spec to file."""
        self._do_export()

    def _do_export(self) -> None:
        """Export the generative spec to a file."""
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        filename = f"{self.project.slug}-{timestamp}.genspec.jsonl"
        output_path = Path.cwd() / filename

        try:
            export_to_file(self.spec, output_path)
            self.app.notify(f"Exported to {filename}")
            logger.info("Exported genspec to %s", output_path)
        except Exception as e:
            self.app.notify(f"Export failed: {e}", severity="error")
            logger.exception("Failed to export genspec: %s", e)


class RegenerateModal(ModalScreen[str | None]):
    """Modal for configuring and launching project regeneration."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=True),
        Binding("ctrl+enter", "submit", "Submit", show=True),
    ]

    DEFAULT_CSS = """
    RegenerateModal {
        align: center middle;
        background: $surface 60%;
    }

    RegenerateModal > Vertical {
        width: 60;
        height: auto;
        max-height: 80%;
        background: $surface;
        border: solid $surface-lighten-2;
        padding: 1 2;
    }

    RegenerateModal .modal-title {
        text-style: bold;
        color: $text;
        text-align: center;
        padding: 1 0;
        margin-bottom: 1;
        border-bottom: solid $surface-lighten-1;
    }

    RegenerateModal .modal-content {
        padding: 0 1;
    }

    RegenerateModal .modal-label {
        color: $text-muted;
        padding: 0 0 1 0;
    }

    RegenerateModal Input {
        margin-bottom: 1;
        background: $surface-lighten-1;
        border: none;
    }

    RegenerateModal Input:focus {
        background: $surface-lighten-2;
        border: none;
    }

    RegenerateModal RadioSet {
        margin-bottom: 1;
        height: auto;
        background: transparent;
        border: none;
    }

    RegenerateModal RadioButton {
        margin: 0;
        padding: 0 0 0 1;
        background: transparent;
    }

    RegenerateModal .hint {
        color: $text-muted;
        text-style: italic;
    }

    RegenerateModal .modal-actions {
        dock: bottom;
        height: auto;
        padding: 1 0 0 0;
        margin-top: 1;
        border-top: solid $surface-lighten-1;
        align: center middle;
    }

    RegenerateModal Button {
        margin: 0 1;
        min-width: 10;
        height: 3;
        background: $surface-lighten-1;
    }
    """

    def __init__(self, project: Project, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.project = project

    @property
    def waypoints_app(self) -> "WaypointsApp":
        """Get the app as WaypointsApp for type checking."""
        return cast("WaypointsApp", self.app)

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("Regenerate Project", classes="modal-title")

            with Vertical(classes="modal-content"):
                yield Label("New Project Name:", classes="modal-label")
                yield Input(
                    value=f"{self.project.name} V2",
                    placeholder="Enter project name",
                    id="project-name",
                )

                yield Label("Mode:", classes="modal-label")
                with RadioSet(id="mode-select"):
                    yield RadioButton(
                        "Replay (use cached outputs)", id="mode-replay"
                    )
                    yield RadioButton(
                        "Regenerate (call LLM fresh)", id="mode-regenerate"
                    )
                    yield RadioButton(
                        "Compare (replay vs regenerate)", id="mode-compare"
                    )

                yield Static(
                    "Replay: instant. Regenerate: fresh LLM outputs. "
                    "Compare: diff both.",
                    classes="hint",
                )

            with Horizontal(classes="modal-actions"):
                yield Button("Regenerate", id="regenerate-btn")
                yield Button("Cancel", id="cancel-btn")

    def on_mount(self) -> None:
        """Set default selection and focus."""
        replay_button = self.query_one("#mode-replay", RadioButton)
        replay_button.value = True
        self.query_one("#project-name", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses."""
        if event.button.id == "cancel-btn":
            self.dismiss(None)
        elif event.button.id == "regenerate-btn":
            self._submit()

    def action_cancel(self) -> None:
        """Cancel and close the modal."""
        self.dismiss(None)

    def action_submit(self) -> None:
        """Submit the form."""
        self._submit()

    def _submit(self) -> None:
        """Validate and start regeneration."""
        name_input = self.query_one("#project-name", Input)
        new_name = name_input.value.strip()
        if new_name:
            self._start_regeneration(new_name)
        else:
            self.app.notify("Please enter a project name", severity="warning")

    def _start_regeneration(self, new_name: str) -> None:
        """Start the regeneration process."""
        from waypoints.genspec import ExecutionMode, execute_spec

        try:
            # Get selected mode from RadioSet
            regenerate_btn = self.query_one("#mode-regenerate", RadioButton)
            compare_btn = self.query_one("#mode-compare", RadioButton)
            if compare_btn.value:
                mode = ExecutionMode.COMPARE
            elif regenerate_btn.value:
                mode = ExecutionMode.REGENERATE
            else:
                mode = ExecutionMode.REPLAY

            # Export current project to spec
            spec = export_project(self.project)

            # Execute with selected mode
            mode_names = {
                ExecutionMode.REPLAY: "Replaying",
                ExecutionMode.REGENERATE: "Regenerating",
                ExecutionMode.COMPARE: "Comparing",
            }
            mode_name = mode_names.get(mode, "Processing")
            self.app.notify(
                f"{mode_name} project from spec...", severity="information"
            )

            def on_progress(message: str, current: int, total: int) -> None:
                logger.info("Progress: %s (%d/%d)", message, current, total)

            result = execute_spec(
                spec=spec,
                project_name=new_name,
                mode=mode,
                on_progress=on_progress,
            )

            if result.success and result.project:
                self.app.notify(
                    f"Created project: {result.project.name}",
                    severity="information",
                )
                # Dismiss modal and navigate to the new project
                self.dismiss(new_name)
                # Pop all screens and resume the new project
                while len(self.waypoints_app.screen_stack) > 1:
                    self.waypoints_app.pop_screen()
                self.waypoints_app._resume_project(result.project)
            else:
                error_msg = result.error or "Unknown error"
                self.app.notify(
                    f"Regeneration failed: {error_msg}", severity="error"
                )
                logger.error("Regeneration failed: %s", error_msg)

        except Exception as e:
            self.app.notify(f"Error: {e}", severity="error")
            logger.exception("Regeneration failed: %s", e)
