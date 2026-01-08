"""Flight plan widgets for CHART phase."""

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, Markdown, Static, Tree
from textual.widgets.tree import TreeNode

from waypoints.models.flight_plan import FlightPlan
from waypoints.models.waypoint import Waypoint, WaypointStatus


class WaypointSelected(Message):
    """Waypoint was selected (highlighted) in the flight plan."""

    def __init__(self, waypoint_id: str) -> None:
        self.waypoint_id = waypoint_id
        super().__init__()


class WaypointOpenDetail(Message):
    """Request to open waypoint detail modal."""

    def __init__(self, waypoint_id: str) -> None:
        self.waypoint_id = waypoint_id
        super().__init__()


# Status icons for waypoints
STATUS_ICONS = {
    WaypointStatus.COMPLETE: "◉",
    WaypointStatus.IN_PROGRESS: "◎",
    WaypointStatus.PENDING: "○",
}
EPIC_ICON = "◇"


def _format_waypoint_label(
    waypoint: Waypoint, is_epic: bool = False, width: int = 80
) -> str:
    """Format a waypoint for display in the tree.

    Args:
        waypoint: The waypoint to format.
        is_epic: Whether this waypoint is an epic.
        width: Target width for padding (fills with spaces).
    """
    icon = EPIC_ICON if is_epic else STATUS_ICONS[waypoint.status]
    # Calculate available space for title (width - icon - space - id - colon - space)
    id_prefix = f"{icon} {waypoint.id}: "
    max_title_len = width - len(id_prefix)
    title = waypoint.title
    if len(title) > max_title_len:
        title = title[: max_title_len - 3] + "..."
    label = f"{id_prefix}{title}"
    # Pad to full width
    return label.ljust(width)


class FlightPlanTree(Tree[Waypoint]):
    """Tree widget for displaying the flight plan."""

    DEFAULT_CSS = """
    FlightPlanTree {
        height: 1fr;
        padding: 0;
        width: 1fr;
        scrollbar-gutter: stable;
        scrollbar-size: 1 1;
        scrollbar-background: $surface;
        scrollbar-color: $surface-lighten-2;
    }

    FlightPlanTree > .tree--guides {
        color: $text-muted;
    }

    FlightPlanTree > .tree--guides-selected {
        color: $text-muted;
    }

    FlightPlanTree > .tree--cursor {
        background: $surface-lighten-1;
        color: $text;
        text-style: none;
    }

    FlightPlanTree:focus > .tree--cursor {
        background: $surface-lighten-2;
        color: $text;
        text-style: bold;
    }

    FlightPlanTree > .tree--highlight {
        text-style: none;
    }

    FlightPlanTree > .tree--highlight-line {
        background: $surface-lighten-1;
    }
    """

    BINDINGS = [
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
    ]

    def __init__(self, **kwargs: object) -> None:
        super().__init__("FLIGHT PLAN", **kwargs)
        self._flight_plan: FlightPlan | None = None
        # Hide the root node - we just want to show waypoints
        self.show_root = False

    def update_flight_plan(self, flight_plan: FlightPlan) -> None:
        """Update the tree with a new flight plan."""
        self._flight_plan = flight_plan
        self._rebuild_tree()

    def _rebuild_tree(self) -> None:
        """Rebuild the tree from the flight plan."""
        if not self._flight_plan:
            return

        # Clear existing nodes
        self.root.remove_children()

        # Build a map of parent_id -> children for efficient lookup
        children_map: dict[str | None, list[Waypoint]] = {}
        for wp in self._flight_plan.waypoints:
            parent = wp.parent_id
            if parent not in children_map:
                children_map[parent] = []
            children_map[parent].append(wp)

        # Add root-level waypoints (those with no parent)
        def add_children(
            parent_node: TreeNode[Waypoint], parent_id: str | None
        ) -> None:
            for wp in children_map.get(parent_id, []):
                fp = self._flight_plan
                is_epic = fp.is_epic(wp.id) if fp else False
                label = _format_waypoint_label(wp, is_epic)

                # Check if this waypoint has children
                has_children = wp.id in children_map

                if has_children:
                    # Add as expandable node
                    child_node = parent_node.add(label, data=wp, expand=True)
                    add_children(child_node, wp.id)
                else:
                    # Add as leaf node
                    parent_node.add_leaf(label, data=wp)

        add_children(self.root, None)

        # Expand all nodes by default
        self.root.expand_all()

    def on_tree_node_highlighted(
        self, event: Tree.NodeHighlighted[Waypoint]
    ) -> None:
        """Handle node highlight - emit WaypointSelected for preview update."""
        if event.node.data:
            self.post_message(WaypointSelected(event.node.data.id))

    def on_tree_node_selected(self, event: Tree.NodeSelected[Waypoint]) -> None:
        """Handle node selection (Enter) - emit WaypointOpenDetail."""
        if event.node.data:
            self.post_message(WaypointOpenDetail(event.node.data.id))

    def select_first(self) -> None:
        """Highlight the first waypoint in the tree (for initial preview)."""
        # Get the first child of root (first waypoint)
        if self.root.children:
            first_node = self.root.children[0]
            # Move cursor to highlight (not select - that opens detail modal)
            self.move_cursor(first_node)
            # Emit message for preview update
            if first_node.data:
                self.post_message(WaypointSelected(first_node.data.id))


class FlightPlanPanel(Vertical):
    """Left panel showing the flight plan tree."""

    DEFAULT_CSS = """
    FlightPlanPanel {
        width: 1fr;
        height: 100%;
        border-right: solid $primary-darken-2;
    }

    FlightPlanPanel .panel-title {
        text-style: bold;
        color: $primary;
        padding: 1;
        border-bottom: solid $surface-lighten-1;
    }

    FlightPlanPanel .legend {
        dock: bottom;
        height: auto;
        padding: 1;
        border-top: solid $surface-lighten-1;
        color: $text-muted;
    }
    """

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._flight_plan: FlightPlan | None = None

    def compose(self) -> ComposeResult:
        yield Static("FLIGHT PLAN", classes="panel-title")
        yield FlightPlanTree(id="flight-tree")
        yield Static(
            "◉ Done  ◎ Active  ○ Pending  ◇ Epic", classes="legend"
        )

    def update_flight_plan(self, flight_plan: FlightPlan) -> None:
        """Update the flight plan display."""
        self._flight_plan = flight_plan
        tree = self.query_one("#flight-tree", FlightPlanTree)
        tree.update_flight_plan(flight_plan)

    @property
    def selected_waypoint(self) -> Waypoint | None:
        """Get the currently highlighted waypoint."""
        tree = self.query_one("#flight-tree", FlightPlanTree)
        if tree.cursor_node and tree.cursor_node.data:
            return tree.cursor_node.data
        return None

    @property
    def selected_id(self) -> str | None:
        """Get the ID of the currently highlighted waypoint."""
        wp = self.selected_waypoint
        return wp.id if wp else None

    def select_first(self) -> None:
        """Select the first waypoint in the tree."""
        tree = self.query_one("#flight-tree", FlightPlanTree)
        tree.select_first()


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
