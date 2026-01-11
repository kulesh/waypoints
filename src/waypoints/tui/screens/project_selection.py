"""Project selection screen - first screen shown on app startup."""

import logging
from datetime import datetime
from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, Footer, OptionList, Static
from textual.widgets.option_list import Option

from waypoints.fly.execution_log import ExecutionLogReader
from waypoints.llm.metrics import MetricsCollector
from waypoints.models import Project
from waypoints.models.flight_plan import FlightPlanReader
from waypoints.models.waypoint import WaypointStatus
from waypoints.tui.screens.ideation import IdeationScreen
from waypoints.tui.widgets.header import StatusHeader

logger = logging.getLogger(__name__)


def _format_relative_time(dt: datetime) -> str:
    """Format datetime as relative time string."""
    now = datetime.now()
    diff = now - dt

    if diff.days > 365:
        years = diff.days // 365
        return f"{years}y ago"
    elif diff.days > 30:
        months = diff.days // 30
        return f"{months}mo ago"
    elif diff.days > 0:
        return f"{diff.days}d ago"
    elif diff.seconds > 3600:
        hours = diff.seconds // 3600
        return f"{hours}h ago"
    elif diff.seconds > 60:
        minutes = diff.seconds // 60
        return f"{minutes}m ago"
    else:
        return "just now"


class ConfirmDeleteProjectModal(ModalScreen[bool]):
    """Confirmation modal for deleting a project."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=True),
        Binding("enter", "confirm", "Confirm", show=True),
    ]

    DEFAULT_CSS = """
    ConfirmDeleteProjectModal {
        align: center middle;
        background: $surface 60%;
    }

    ConfirmDeleteProjectModal > Vertical {
        width: 60;
        height: auto;
        max-height: 20;
        background: $surface;
        border: solid $surface-lighten-2;
        padding: 1 2;
    }

    ConfirmDeleteProjectModal .modal-title {
        text-style: bold;
        color: $text;
        text-align: center;
        padding: 1 0;
        margin-bottom: 1;
    }

    ConfirmDeleteProjectModal .project-name {
        margin-bottom: 1;
        color: $text;
        text-style: bold;
    }

    ConfirmDeleteProjectModal .warning {
        color: $text-muted;
        margin-top: 1;
    }

    ConfirmDeleteProjectModal .modal-actions {
        dock: bottom;
        height: auto;
        padding: 1 0 0 0;
        margin-top: 1;
        border-top: solid $surface-lighten-1;
        align: center middle;
    }

    ConfirmDeleteProjectModal Button {
        margin: 0 1;
        min-width: 10;
    }

    ConfirmDeleteProjectModal Button#btn-delete {
        background: $error-darken-2;
    }

    ConfirmDeleteProjectModal Button#btn-cancel {
        background: $surface-lighten-1;
    }
    """

    def __init__(self, project: Project, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.project = project

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("Delete Project?", classes="modal-title")
            yield Static(self.project.name, classes="project-name")
            yield Static(
                "This will permanently delete all project files, "
                "including docs, sessions, and flight plans.",
                classes="warning",
            )
            with Horizontal(classes="modal-actions"):
                yield Button("Delete", id="btn-delete", variant="error")
                yield Button("Cancel", id="btn-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-delete":
            self.dismiss(True)
        else:
            self.dismiss(False)

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


class ProjectListPanel(Vertical):
    """Left panel showing list of projects."""

    DEFAULT_CSS = """
    ProjectListPanel {
        width: 1fr;
        height: 100%;
        border-right: solid $surface-lighten-1;
    }

    ProjectListPanel .panel-title {
        text-style: bold;
        color: $text;
        padding: 1;
        border-bottom: solid $surface-lighten-1;
    }

    ProjectListPanel OptionList {
        height: 1fr;
        background: transparent;
    }

    ProjectListPanel .empty-message {
        color: $text-muted;
        text-style: italic;
        padding: 2;
        text-align: center;
    }
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._projects: list[Project] = []

    def compose(self) -> ComposeResult:
        yield Static("PROJECTS", classes="panel-title")
        yield OptionList(id="project-list")

    def update_projects(self, projects: list[Project]) -> None:
        """Update the list with projects."""
        self._projects = projects
        option_list = self.query_one("#project-list", OptionList)
        option_list.clear_options()

        if not projects:
            # Show empty message as disabled option
            option_list.add_option(
                Option("No projects yet. Press 'n' to create one.", disabled=True)
            )
        else:
            for project in projects:
                # Format: name (phase) - time ago
                phase = project.journey.phase if project.journey else "new"
                time_ago = _format_relative_time(project.updated_at)
                label = f"{project.name} ({phase}) - {time_ago}"
                option_list.add_option(Option(label, id=project.slug))

    @property
    def selected_project(self) -> Project | None:
        """Get currently highlighted project."""
        option_list = self.query_one("#project-list", OptionList)
        if option_list.highlighted is not None and self._projects:
            idx = option_list.highlighted
            if idx < len(self._projects):
                return self._projects[idx]
        return None


class ProjectPreviewPanel(VerticalScroll):
    """Right panel showing preview of selected project."""

    DEFAULT_CSS = """
    ProjectPreviewPanel {
        width: 1fr;
        height: 100%;
        padding: 1 2;
    }

    ProjectPreviewPanel .panel-title {
        text-style: bold;
        color: $text;
        padding-bottom: 1;
        border-bottom: solid $surface-lighten-1;
        margin-bottom: 1;
    }

    ProjectPreviewPanel .placeholder {
        color: $text-muted;
        text-style: italic;
    }

    ProjectPreviewPanel .project-name {
        text-style: bold;
        color: $text;
        margin-bottom: 1;
    }

    ProjectPreviewPanel .project-meta {
        color: $text-muted;
        margin-bottom: 0;
    }

    ProjectPreviewPanel .project-idea {
        color: $text;
        margin-top: 1;
        padding: 1;
        background: $surface-lighten-1;
    }

    ProjectPreviewPanel .hint {
        color: $text-disabled;
        text-style: italic;
        margin-top: 2;
    }

    ProjectPreviewPanel .project-stats {
        color: $text-muted;
        margin-bottom: 0;
    }

    ProjectPreviewPanel .project-stats-success {
        color: $success;
    }

    ProjectPreviewPanel .project-stats-failed {
        color: $error;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static("PREVIEW", classes="panel-title")
        yield Static(
            "Select a project to preview", classes="placeholder", id="placeholder"
        )
        yield Vertical(id="preview-content")

    def _get_waypoint_stats(self, project: Project) -> tuple[int, int] | None:
        """Get (completed, total) waypoint counts. Returns None if no flight plan."""
        try:
            flight_plan = FlightPlanReader.load(project)
            if not flight_plan or not flight_plan.waypoints:
                return None
            total = len(flight_plan.waypoints)
            completed = sum(
                1
                for wp in flight_plan.waypoints
                if wp.status == WaypointStatus.COMPLETE
            )
            return (completed, total)
        except Exception:
            return None

    def _get_cost_and_time(self, project: Project) -> tuple[float, int]:
        """Get (total_cost, total_seconds) for project."""
        cost = 0.0
        try:
            collector = MetricsCollector(project)
            cost = collector.total_cost
        except Exception:
            pass

        total_seconds = 0
        try:
            log_files = ExecutionLogReader.list_logs(project)
            for log_path in log_files:
                log = ExecutionLogReader.load(log_path)
                if log.completed_at and log.started_at:
                    total_seconds += int(
                        (log.completed_at - log.started_at).total_seconds()
                    )
        except Exception:
            pass

        return (cost, total_seconds)

    def _get_last_execution(self, project: Project) -> tuple[datetime, str] | None:
        """Get (timestamp, result) of most recent execution by completion time."""
        try:
            log_files = ExecutionLogReader.list_logs(project)
            if not log_files:
                return None

            # Find the log with the most recent completed_at timestamp
            latest: tuple[datetime, str] | None = None
            for log_path in log_files:
                try:
                    log = ExecutionLogReader.load(log_path)
                    if log.completed_at:
                        if latest is None or log.completed_at > latest[0]:
                            latest = (log.completed_at, log.result or "unknown")
                except Exception:
                    continue

            return latest
        except Exception:
            pass
        return None

    def show_project(self, project: Project | None) -> None:
        """Display project preview."""
        placeholder = self.query_one("#placeholder", Static)
        content = self.query_one("#preview-content", Vertical)

        # Clear previous content
        content.remove_children()

        if project is None:
            placeholder.display = True
        else:
            placeholder.display = False

            # Name
            content.mount(Static(project.name, classes="project-name"))

            # Phase
            phase = project.journey.phase if project.journey else "new"
            content.mount(
                Static(
                    f"Phase: {phase.replace('-', ' ').title()}", classes="project-meta"
                )
            )

            # Dates
            created = project.created_at.strftime("%Y-%m-%d %H:%M")
            updated = _format_relative_time(project.updated_at)
            content.mount(Static(f"Created: {created}", classes="project-meta"))
            content.mount(Static(f"Updated: {updated}", classes="project-meta"))

            # Waypoint progress (only if flight plan exists)
            stats = self._get_waypoint_stats(project)
            if stats:
                completed, total = stats
                bar_width = 8
                filled = int((completed / total) * bar_width) if total > 0 else 0
                bar = "■" * filled + "□" * (bar_width - filled)
                content.mount(
                    Static(f"{bar} {completed}/{total} waypoints", classes="project-stats")
                )

            # Cost and time
            cost, time_secs = self._get_cost_and_time(project)
            if cost > 0 or time_secs > 0:
                parts: list[str] = []
                if cost > 0:
                    parts.append(f"${cost:.2f}")
                if time_secs > 0:
                    mins, secs = divmod(time_secs, 60)
                    if mins >= 60:
                        hours, mins = divmod(mins, 60)
                        parts.append(f"{hours}h {mins}m total")
                    elif mins > 0:
                        parts.append(f"{mins}m {secs}s total")
                    else:
                        parts.append(f"{secs}s total")
                content.mount(Static(" · ".join(parts), classes="project-stats"))

            # Last execution
            last_exec = self._get_last_execution(project)
            if last_exec:
                exec_time, result = last_exec
                time_ago = _format_relative_time(exec_time)
                result_display = result.replace("_", " ").title()
                css_class = "project-stats"
                if result == "success":
                    css_class = "project-stats-success"
                elif result in ("failed", "max_iterations"):
                    css_class = "project-stats-failed"
                content.mount(
                    Static(f"Last run: {time_ago} · {result_display}", classes=css_class)
                )

            # Project description - prefer summary over initial idea
            description = project.summary if project.summary else project.initial_idea
            if description:
                display_text = description[:800]
                if len(description) > 800:
                    display_text += "..."
                content.mount(Static(display_text, classes="project-idea"))

            # Hint
            content.mount(Static("Press Enter to open", classes="hint"))


class ProjectSelectionScreen(Screen[None]):
    """
    Project selection screen - shown on app startup.

    Two-panel layout:
    - Left: List of projects
    - Right: Selected project preview
    """

    BINDINGS = [
        Binding("ctrl+q", "quit", "Quit", show=True),
        Binding("n", "new_project", "New", show=True),
        Binding("d", "delete_project", "Delete", show=True),
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
    ]

    DEFAULT_CSS = """
    ProjectSelectionScreen {
        background: $surface;
        overflow: hidden;
    }

    ProjectSelectionScreen .main-container {
        width: 100%;
        height: 1fr;
    }
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._projects: list[Project] = []

    def compose(self) -> ComposeResult:
        yield StatusHeader()
        with Horizontal(classes="main-container"):
            yield ProjectListPanel(id="project-list-panel")
            yield ProjectPreviewPanel(id="preview-panel")
        yield Footer()

    def on_mount(self) -> None:
        """Load projects and set up initial state."""
        self.app.sub_title = "Projects"
        self._refresh_projects()

        # Focus the list panel
        option_list = self.query_one("#project-list", OptionList)
        option_list.focus()

    def _refresh_projects(self) -> None:
        """Reload project list from disk."""
        self._projects = Project.list_all()
        list_panel = self.query_one("#project-list-panel", ProjectListPanel)
        list_panel.update_projects(self._projects)

        # Clear preview - it will be populated when user highlights an item
        preview_panel = self.query_one("#preview-panel", ProjectPreviewPanel)
        preview_panel.show_project(None)

    def on_option_list_option_highlighted(
        self, event: OptionList.OptionHighlighted
    ) -> None:
        """Handle project highlight for preview update."""
        list_panel = self.query_one("#project-list-panel", ProjectListPanel)
        project = list_panel.selected_project
        preview_panel = self.query_one("#preview-panel", ProjectPreviewPanel)
        preview_panel.show_project(project)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Handle Enter key on option list - open the selected project."""
        list_panel = self.query_one("#project-list-panel", ProjectListPanel)
        project = list_panel.selected_project
        if project:
            self.app._resume_project(project)  # type: ignore[attr-defined]

    def action_new_project(self) -> None:
        """Create a new project - go to IdeationScreen."""
        self.app.switch_screen(IdeationScreen())

    def action_open_project(self) -> None:
        """Open/resume the selected project."""
        list_panel = self.query_one("#project-list-panel", ProjectListPanel)
        project = list_panel.selected_project
        if project:
            # Use the app's resume logic
            self.app._resume_project(project)  # type: ignore[attr-defined]

    def action_delete_project(self) -> None:
        """Delete the selected project with confirmation."""
        list_panel = self.query_one("#project-list-panel", ProjectListPanel)
        project = list_panel.selected_project
        if project:
            self._show_delete_confirmation(project)

    def _show_delete_confirmation(self, project: Project) -> None:
        """Show delete confirmation modal."""

        def handle_delete(confirmed: bool | None) -> None:
            if confirmed:
                project.delete()
                self._refresh_projects()
                self.notify(f"Deleted: {project.name}")

        self.app.push_screen(
            ConfirmDeleteProjectModal(project),
            handle_delete,
        )

    def action_cursor_down(self) -> None:
        """Move selection down in the list."""
        option_list = self.query_one("#project-list", OptionList)
        option_list.action_cursor_down()

    def action_cursor_up(self) -> None:
        """Move selection up in the list."""
        option_list = self.query_one("#project-list", OptionList)
        option_list.action_cursor_up()
