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

from waypoints.genspec import export_project
from waypoints.models.project import Project

logger = logging.getLogger(__name__)


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
        width: 55;
        height: auto;
        max-height: 80%;
        background: $surface;
        border: solid $surface-lighten-2;
        padding: 0 1;
    }

    RegenerateModal .modal-title {
        text-style: bold;
        color: $text;
        text-align: center;
        padding: 0;
        margin-bottom: 0;
        border-bottom: solid $surface-lighten-1;
    }

    RegenerateModal .modal-content {
        padding: 0;
    }

    RegenerateModal .modal-label {
        color: $text-muted;
        padding: 0;
    }

    RegenerateModal Input {
        margin: 0 0 1 0;
        background: $surface-lighten-1;
        border: none;
    }

    RegenerateModal Input:focus {
        background: $surface-lighten-2;
        border: none;
    }

    RegenerateModal RadioSet {
        margin: 0;
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
        margin: 0;
    }

    RegenerateModal .start-from-section {
        height: 0;
        overflow: hidden;
        margin: 0;
    }

    RegenerateModal .start-from-section.visible {
        height: auto;
        margin-top: 1;
    }

    RegenerateModal .modal-actions {
        dock: bottom;
        height: auto;
        padding: 0;
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
                    yield RadioButton("Replay (use cached outputs)", id="mode-replay")
                    yield RadioButton(
                        "Regenerate (call LLM fresh)", id="mode-regenerate"
                    )

                yield Static(
                    "Replay: instant, uses cached outputs. "
                    "Regenerate: fresh LLM calls.",
                    classes="hint",
                )

                with Vertical(classes="start-from-section", id="start-from-section"):
                    yield Label("Start from:", classes="modal-label")
                    with RadioSet(id="start-from-select"):
                        yield RadioButton(
                            "Shape Q&A", id="start-qa", value=True
                        )
                        yield RadioButton(
                            "Idea Brief (use cached Q&A)", id="start-brief"
                        )

            with Horizontal(classes="modal-actions"):
                yield Button("OK", id="ok-btn")
                yield Button("Cancel", id="cancel-btn")

    def on_mount(self) -> None:
        """Set default selection and focus."""
        replay_button = self.query_one("#mode-replay", RadioButton)
        replay_button.value = True
        self.query_one("#project-name", Input).focus()

    def on_radio_set_changed(self, event: RadioSet.Changed) -> None:
        """Handle mode selection changes."""
        if event.radio_set.id == "mode-select":
            # Show "Start from" section only for Regenerate mode
            start_from_section = self.query_one("#start-from-section")
            regenerate_btn = self.query_one("#mode-regenerate", RadioButton)

            if regenerate_btn.value:
                start_from_section.add_class("visible")
            else:
                start_from_section.remove_class("visible")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses."""
        if event.button.id == "cancel-btn":
            self.dismiss(None)
        elif event.button.id == "ok-btn":
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
            if regenerate_btn.value:
                mode = ExecutionMode.REGENERATE
            else:
                mode = ExecutionMode.REPLAY

            # Get skip_qa option (only relevant for Regenerate mode)
            skip_qa = False
            if mode == ExecutionMode.REGENERATE:
                start_brief_btn = self.query_one("#start-brief", RadioButton)
                skip_qa = start_brief_btn.value

            # Export current project to spec
            spec = export_project(self.project)

            # Execute with selected mode
            mode_names = {
                ExecutionMode.REPLAY: "Replaying",
                ExecutionMode.REGENERATE: "Regenerating",
            }
            mode_name = mode_names.get(mode, "Processing")
            extra = " (from Idea Brief)" if skip_qa else ""
            self.app.notify(
                f"{mode_name} project from spec{extra}...", severity="information"
            )

            def on_progress(message: str, current: int, total: int) -> None:
                logger.info("Progress: %s (%d/%d)", message, current, total)

            result = execute_spec(
                spec=spec,
                project_name=new_name,
                mode=mode,
                on_progress=on_progress,
                skip_qa=skip_qa,
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
                self.app.notify(f"Regeneration failed: {error_msg}", severity="error")
                logger.error("Regeneration failed: %s", error_msg)

        except Exception as e:
            self.app.notify(f"Error: {e}", severity="error")
            logger.exception("Regeneration failed: %s", e)


class ExportModal(ModalScreen[tuple[str, str] | None]):
    """Modal for export file selection.

    Returns: (directory, filename) or None if cancelled.
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=True),
        Binding("ctrl+enter", "submit", "Export", show=True),
    ]

    DEFAULT_CSS = """
    ExportModal {
        align: center middle;
        background: $surface 60%;
    }

    ExportModal > Vertical {
        width: 70;
        height: auto;
        max-height: 80%;
        background: $surface;
        border: solid $surface-lighten-2;
        padding: 1 2;
    }

    ExportModal .modal-title {
        text-style: bold;
        color: $text;
        text-align: center;
        padding: 1 0;
        margin-bottom: 1;
        border-bottom: solid $surface-lighten-1;
    }

    ExportModal .modal-content {
        padding: 0 1;
    }

    ExportModal .modal-label {
        color: $text-muted;
        padding: 0 0 1 0;
    }

    ExportModal Input {
        margin-bottom: 1;
        background: $surface-lighten-1;
        border: none;
    }

    ExportModal Input:focus {
        background: $surface-lighten-2;
        border: none;
    }

    ExportModal .hint {
        color: $text-muted;
        text-style: italic;
        margin-bottom: 1;
    }

    ExportModal .modal-actions {
        dock: bottom;
        height: auto;
        padding: 1 0 0 0;
        margin-top: 1;
        border-top: solid $surface-lighten-1;
        align: center middle;
    }

    ExportModal Button {
        margin: 0 1;
        min-width: 10;
        height: 3;
        background: $surface-lighten-1;
    }
    """

    def __init__(self, project_slug: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.project_slug = project_slug
        self._default_dir = self._get_default_directory()
        self._default_filename = self._generate_filename()

    def _get_default_directory(self) -> str:
        """Get default export directory (prefer Downloads, fall back to cwd)."""
        downloads = Path.home() / "Downloads"
        if downloads.exists() and downloads.is_dir():
            return str(downloads)
        return str(Path.cwd())

    def _generate_filename(self) -> str:
        """Generate default filename with timestamp."""
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        return f"{self.project_slug}-{timestamp}.genspec.jsonl"

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("Export GenSpec", classes="modal-title")

            with Vertical(classes="modal-content"):
                yield Label("Directory:", classes="modal-label")
                yield Input(
                    value=self._default_dir,
                    placeholder="Enter export directory",
                    id="export-dir",
                )

                yield Label("Filename:", classes="modal-label")
                yield Input(
                    value=self._default_filename,
                    placeholder="Enter filename",
                    id="export-filename",
                )

                yield Static(
                    "The genspec file contains the full project generation history.",
                    classes="hint",
                )

            with Horizontal(classes="modal-actions"):
                yield Button("Export", id="export-btn")
                yield Button("Cancel", id="cancel-btn")

    def on_mount(self) -> None:
        """Focus on directory input."""
        self.query_one("#export-dir", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses."""
        if event.button.id == "cancel-btn":
            self.dismiss(None)
        elif event.button.id == "export-btn":
            self._submit()

    def action_cancel(self) -> None:
        """Cancel and close the modal."""
        self.dismiss(None)

    def action_submit(self) -> None:
        """Submit the form."""
        self._submit()

    def _submit(self) -> None:
        """Validate and return export path."""
        dir_input = self.query_one("#export-dir", Input)
        filename_input = self.query_one("#export-filename", Input)

        directory = dir_input.value.strip()
        filename = filename_input.value.strip()

        # Validate directory
        if not directory:
            self.app.notify("Please enter a directory", severity="warning")
            return

        dir_path = Path(directory).expanduser()
        if not dir_path.exists():
            self.app.notify("Directory does not exist", severity="warning")
            return

        if not dir_path.is_dir():
            self.app.notify("Path is not a directory", severity="warning")
            return

        # Validate filename
        if not filename:
            self.app.notify("Please enter a filename", severity="warning")
            return

        if not filename.endswith(".genspec.jsonl"):
            filename = filename + ".genspec.jsonl"

        # Check for existing file
        full_path = dir_path / filename
        if full_path.exists():
            # Warn but allow overwrite
            self.app.notify("File exists - will overwrite", severity="warning")

        self.dismiss((str(dir_path), filename))


class GenSpecPreview(Static):
    """Widget showing a summary preview of a genspec file.

    Used in the import modal to show spec details before importing.
    """

    DEFAULT_CSS = """
    GenSpecPreview {
        height: auto;
        padding: 1;
        margin-top: 1;
        background: $surface-lighten-1;
        border: solid $surface-lighten-2;
    }

    GenSpecPreview .preview-title {
        text-style: bold;
        margin-bottom: 1;
    }

    GenSpecPreview .preview-row {
        color: $text-muted;
    }

    GenSpecPreview .preview-artifacts {
        color: $success;
    }

    GenSpecPreview .preview-empty {
        color: $text-disabled;
        text-style: italic;
    }
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._spec: Any = None

    def update_from_path(self, path: str) -> bool:
        """Update preview from a file path.

        Args:
            path: Path to the genspec file

        Returns:
            True if successfully loaded, False otherwise
        """
        from waypoints.genspec.importer import import_from_file

        try:
            self._spec = import_from_file(Path(path))
            self._render_preview()
            return True
        except Exception:
            self._spec = None
            self.update("")
            return False

    def _render_preview(self) -> None:
        """Render the preview from loaded spec."""
        import json

        from waypoints.genspec.spec import ArtifactType

        if self._spec is None:
            self.update("")
            return

        lines = []

        # Project name
        project_name = self._spec.source_project or "Unnamed Project"
        lines.append(f"[bold]Project:[/bold] {project_name}")

        # Creation date
        if self._spec.created_at:
            date_str = self._spec.created_at.strftime("%Y-%m-%d %H:%M")
            lines.append(f"[bold]Created:[/bold] {date_str}")

        # Waypoint count from flight plan
        waypoint_count = 0
        flight_plan = self._spec.get_artifact(ArtifactType.FLIGHT_PLAN)
        if flight_plan and flight_plan.content:
            try:
                waypoints_data = json.loads(flight_plan.content)
                waypoint_count = len(waypoints_data)
            except json.JSONDecodeError:
                pass
        lines.append(f"[bold]Waypoints:[/bold] {waypoint_count}")

        # Artifacts summary
        artifact_types = {a.artifact_type for a in self._spec.artifacts}
        markers = []
        if ArtifactType.IDEA_BRIEF in artifact_types:
            markers.append("✓ Brief")
        if ArtifactType.PRODUCT_SPEC in artifact_types:
            markers.append("✓ Spec")
        if ArtifactType.FLIGHT_PLAN in artifact_types:
            markers.append("✓ Plan")

        artifacts_str = " ".join(markers) if markers else "None"
        lines.append(f"[bold]Artifacts:[/bold] {artifacts_str}")

        # Steps count
        step_count = len(self._spec.steps) if self._spec.steps else 0
        lines.append(f"[bold]Steps:[/bold] {step_count}")

        self.update("\n".join(lines))

    def clear_preview(self) -> None:
        """Clear the preview."""
        self._spec = None
        self.update("")
