"""Flight plan widgets for CHART phase."""

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.widgets import Button, Markdown, Static

from waypoints.models.flight_plan import FlightPlan
from waypoints.models.waypoint import Waypoint, WaypointStatus


class WaypointSelected(Message):
    """Waypoint was selected in the flight plan."""

    def __init__(self, waypoint_id: str) -> None:
        self.waypoint_id = waypoint_id
        super().__init__()


class WaypointOpenDetail(Message):
    """Request to open waypoint detail modal."""

    def __init__(self, waypoint_id: str) -> None:
        self.waypoint_id = waypoint_id
        super().__init__()


class WaypointListItem(Static):
    """Single waypoint item in the flight plan tree."""

    DEFAULT_CSS = """
    WaypointListItem {
        height: auto;
        padding: 0 1;
        margin: 0;
    }

    WaypointListItem.selected {
        background: $primary-darken-2;
    }

    WaypointListItem:hover {
        background: $surface-lighten-1;
    }
    """

    STATUS_ICONS = {
        WaypointStatus.COMPLETE: "◉",
        WaypointStatus.IN_PROGRESS: "◎",
        WaypointStatus.PENDING: "○",
    }
    EPIC_ICON = "◇"

    def __init__(
        self,
        waypoint: Waypoint,
        depth: int = 0,
        is_epic: bool = False,
        **kwargs: object,
    ) -> None:
        self.waypoint = waypoint
        self.depth = depth
        self.is_epic = is_epic
        super().__init__(**kwargs)

    def compose(self) -> ComposeResult:
        indent = "  " * self.depth
        connector = "├─" if self.depth > 0 else ""
        if self.is_epic:
            icon = self.EPIC_ICON
        else:
            icon = self.STATUS_ICONS[self.waypoint.status]

        title = self.waypoint.title
        if len(title) > 30:
            title = title[:27] + "..."

        yield Static(f"{indent}{connector}{icon} {self.waypoint.id}: {title}")

    def on_click(self) -> None:
        self.post_message(WaypointSelected(self.waypoint.id))


class FlightPlanPanel(VerticalScroll):
    """Left panel showing the flight plan tree."""

    DEFAULT_CSS = """
    FlightPlanPanel {
        width: 1fr;
        height: 100%;
        border-right: solid $primary-darken-2;
        padding: 1;
    }

    FlightPlanPanel .panel-title {
        text-style: bold;
        color: $primary;
        padding-bottom: 1;
        border-bottom: solid $surface-lighten-1;
        margin-bottom: 1;
    }

    FlightPlanPanel .legend {
        dock: bottom;
        height: auto;
        padding-top: 1;
        border-top: solid $surface-lighten-1;
        color: $text-muted;
    }

    FlightPlanPanel .waypoint-list {
        height: auto;
    }
    """

    BINDINGS = [
        Binding("j", "move_down", "Move Down", show=False),
        Binding("k", "move_up", "Move Up", show=False),
        Binding("down", "move_down", "Move Down", show=False),
        Binding("up", "move_up", "Move Up", show=False),
        Binding("enter", "open_detail", "Open Detail", show=False),
    ]

    selected_id: reactive[str | None] = reactive(None)

    def __init__(self, flight_plan: FlightPlan | None = None, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._flight_plan = flight_plan or FlightPlan()
        self._waypoint_items: list[WaypointListItem] = []

    def compose(self) -> ComposeResult:
        yield Static("FLIGHT PLAN", classes="panel-title")
        yield Vertical(id="waypoint-list", classes="waypoint-list")
        yield Static(
            "◉ Done  ◎ Active  ○ Pending  ◇ Epic", classes="legend"
        )

    def update_flight_plan(self, flight_plan: FlightPlan) -> None:
        """Update the flight plan display."""
        self._flight_plan = flight_plan
        self._rebuild_list()

    def _rebuild_list(self) -> None:
        """Rebuild the waypoint list from flight plan."""
        container = self.query_one("#waypoint-list", Vertical)

        # Remove existing items
        for item in self._waypoint_items:
            item.remove()
        self._waypoint_items.clear()

        # Add new items
        for waypoint, depth in self._flight_plan.iterate_in_order():
            is_epic = self._flight_plan.is_epic(waypoint.id)
            item = WaypointListItem(waypoint, depth, is_epic)
            if waypoint.id == self.selected_id:
                item.add_class("selected")
            self._waypoint_items.append(item)
            container.mount(item)

    def watch_selected_id(self, old_id: str | None, new_id: str | None) -> None:
        """Update selection highlighting."""
        for item in self._waypoint_items:
            if item.waypoint.id == old_id:
                item.remove_class("selected")
            if item.waypoint.id == new_id:
                item.add_class("selected")

    def on_waypoint_selected(self, event: WaypointSelected) -> None:
        """Handle waypoint selection from click - let event bubble to screen."""
        self.selected_id = event.waypoint_id
        # Don't stop - let it bubble up to ChartScreen

    def action_move_down(self) -> None:
        """Move selection down."""
        if not self._waypoint_items:
            return

        new_id: str | None = None
        if self.selected_id is None:
            new_id = self._waypoint_items[0].waypoint.id
        else:
            for i, item in enumerate(self._waypoint_items):
                if item.waypoint.id == self.selected_id:
                    if i + 1 < len(self._waypoint_items):
                        new_id = self._waypoint_items[i + 1].waypoint.id
                    break

        if new_id:
            self.selected_id = new_id
            self.post_message(WaypointSelected(new_id))

    def action_move_up(self) -> None:
        """Move selection up."""
        if not self._waypoint_items:
            return

        new_id: str | None = None
        if self.selected_id is None:
            new_id = self._waypoint_items[-1].waypoint.id
        else:
            for i, item in enumerate(self._waypoint_items):
                if item.waypoint.id == self.selected_id:
                    if i > 0:
                        new_id = self._waypoint_items[i - 1].waypoint.id
                    break

        if new_id:
            self.selected_id = new_id
            self.post_message(WaypointSelected(new_id))

    def action_open_detail(self) -> None:
        """Open detail modal for selected waypoint."""
        if self.selected_id:
            self.post_message(WaypointOpenDetail(self.selected_id))


class WaypointPreviewPanel(VerticalScroll):
    """Right panel showing brief preview of selected waypoint."""

    DEFAULT_CSS = """
    WaypointPreviewPanel {
        width: 1fr;
        height: 100%;
        padding: 1 2;
    }

    WaypointPreviewPanel .panel-title {
        text-style: bold;
        color: $primary;
        padding-bottom: 1;
        border-bottom: solid $surface-lighten-1;
        margin-bottom: 1;
    }

    WaypointPreviewPanel .placeholder {
        color: $text-muted;
        text-style: italic;
    }

    WaypointPreviewPanel .wp-title {
        text-style: bold;
        margin-bottom: 1;
    }

    WaypointPreviewPanel .wp-objective {
        color: $text;
        margin-bottom: 1;
    }

    WaypointPreviewPanel .wp-meta {
        color: $text-muted;
        margin-bottom: 0;
    }

    WaypointPreviewPanel .wp-hint {
        color: $text-disabled;
        text-style: italic;
        margin-top: 2;
    }
    """

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._waypoint: Waypoint | None = None

    def compose(self) -> ComposeResult:
        yield Static("PREVIEW", classes="panel-title")
        yield Static(
            "Select a waypoint to preview", classes="placeholder", id="placeholder"
        )
        yield Vertical(id="preview-content")

    def show_waypoint(self, waypoint: Waypoint | None, is_epic: bool = False) -> None:
        """Display waypoint preview."""
        self._waypoint = waypoint
        placeholder = self.query_one("#placeholder")
        content = self.query_one("#preview-content", Vertical)

        # Clear existing content
        content.remove_children()

        if waypoint is None:
            placeholder.display = True
        else:
            placeholder.display = False

            # Title
            title = f"{waypoint.id}: {waypoint.title}"
            content.mount(Static(title, classes="wp-title"))

            # Objective (truncated)
            objective = waypoint.objective
            if len(objective) > 100:
                objective = objective[:97] + "..."
            content.mount(Static(objective, classes="wp-objective"))

            # Status
            status_text = waypoint.status.value.replace("_", " ").title()
            if is_epic:
                status_text += " (Epic)"
            content.mount(Static(f"Status: {status_text}", classes="wp-meta"))

            # Dependencies
            if waypoint.dependencies:
                deps = ", ".join(waypoint.dependencies)
                content.mount(Static(f"Depends on: {deps}", classes="wp-meta"))
            else:
                content.mount(Static("Depends on: None", classes="wp-meta"))

            # Acceptance criteria preview (show first 3)
            if waypoint.acceptance_criteria:
                content.mount(Static("Criteria:", classes="wp-meta"))
                for i, criterion in enumerate(waypoint.acceptance_criteria[:3]):
                    # Truncate long criteria
                    text = criterion if len(criterion) <= 40 else criterion[:37] + "..."
                    content.mount(Static(f"  • {text}", classes="wp-meta"))
                if len(waypoint.acceptance_criteria) > 3:
                    remaining = len(waypoint.acceptance_criteria) - 3
                    content.mount(Static(f"  ... +{remaining} more", classes="wp-meta"))

            # Hint
            content.mount(Static("Press Enter for details", classes="wp-hint"))


class WaypointDetailModal(ModalScreen[bool]):
    """Modal screen showing full waypoint details."""

    BINDINGS = [
        Binding("escape", "close", "Close", show=True),
        Binding("e", "edit", "Edit", show=True),
        Binding("b", "break_down", "Break Down", show=True),
        Binding("d", "delete", "Delete", show=True),
    ]

    DEFAULT_CSS = """
    WaypointDetailModal {
        align: center middle;
    }

    WaypointDetailModal > Vertical {
        width: 70%;
        max-width: 80;
        height: auto;
        max-height: 80%;
        background: $surface;
        border: thick $primary;
        padding: 1 2;
    }

    WaypointDetailModal .modal-title {
        text-style: bold;
        color: $primary;
        text-align: center;
        padding-bottom: 1;
        border-bottom: solid $surface-lighten-1;
        margin-bottom: 1;
    }

    WaypointDetailModal .modal-content {
        height: auto;
        max-height: 50;
        padding: 1;
    }

    WaypointDetailModal .modal-actions {
        dock: bottom;
        height: auto;
        padding-top: 1;
        border-top: solid $surface-lighten-1;
        align: center middle;
    }

    WaypointDetailModal Button {
        margin: 0 1;
    }
    """

    def __init__(
        self, waypoint: Waypoint, is_epic: bool = False, **kwargs: object
    ) -> None:
        super().__init__(**kwargs)
        self.waypoint = waypoint
        self.is_epic = is_epic

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(
                f"{self.waypoint.id}: {self.waypoint.title}", classes="modal-title"
            )
            with VerticalScroll(classes="modal-content"):
                yield Markdown(self._format_details())
            with Horizontal(classes="modal-actions"):
                yield Button("Edit", id="btn-edit", variant="primary")
                yield Button("Break Down", id="btn-break")
                yield Button("Delete", id="btn-delete", variant="error")
                yield Button("Close", id="btn-close")

    def _format_details(self) -> str:
        """Format waypoint as Markdown."""
        wp = self.waypoint

        # Objective
        sections = [f"## Objective\n{wp.objective}"]

        # Acceptance criteria
        if wp.acceptance_criteria:
            criteria = "\n".join(f"- [ ] {c}" for c in wp.acceptance_criteria)
            sections.append(f"## Acceptance Criteria\n{criteria}")
        else:
            sections.append("## Acceptance Criteria\n*None defined*")

        # Dependencies
        if wp.dependencies:
            deps = "\n".join(f"- {d}" for d in wp.dependencies)
            sections.append(f"## Dependencies\n{deps}")
        else:
            sections.append("## Dependencies\nNone")

        # Status
        status = wp.status.value.replace("_", " ").title()
        if self.is_epic:
            status += " (Epic - has sub-waypoints)"
        sections.append(f"## Status\n{status}")

        return "\n\n".join(sections)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses."""
        if event.button.id == "btn-close":
            self.dismiss(False)
        elif event.button.id == "btn-edit":
            self.action_edit()
        elif event.button.id == "btn-break":
            self.action_break_down()
        elif event.button.id == "btn-delete":
            self.action_delete()

    def action_close(self) -> None:
        """Close the modal."""
        self.dismiss(False)

    def action_edit(self) -> None:
        """Edit waypoint (placeholder)."""
        self.app.notify("Edit not yet implemented")

    def action_break_down(self) -> None:
        """Break down waypoint (placeholder)."""
        self.app.notify("Break down not yet implemented")

    def action_delete(self) -> None:
        """Delete waypoint (placeholder)."""
        self.app.notify("Delete not yet implemented")
