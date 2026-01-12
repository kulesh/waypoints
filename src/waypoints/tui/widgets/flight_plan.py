"""Flight plan widgets for CHART phase."""

from typing import Any

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Markdown, Static, TextArea, Tree
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


# Status icons and colors for waypoints
STATUS_ICONS = {
    WaypointStatus.COMPLETE: ("◉", "green"),
    WaypointStatus.IN_PROGRESS: ("◎", "bold cyan"),
    WaypointStatus.FAILED: ("✗", "bold red"),
    WaypointStatus.SKIPPED: ("⊘", "yellow"),
    WaypointStatus.PENDING: ("○", "dim"),
}
# Blink state icon (shown when blinking is "off")
STATUS_ICONS_BLINK = {
    WaypointStatus.COMPLETE: ("◉", "green"),
    WaypointStatus.IN_PROGRESS: (" ", ""),  # Blinks to empty
    WaypointStatus.FAILED: ("✗", "bold red"),
    WaypointStatus.SKIPPED: ("⊘", "yellow"),
    WaypointStatus.PENDING: ("○", "dim"),
}
EPIC_ICON = "◇"
EPIC_COLOR = ""  # Neutral color (same as regular text)


def _format_waypoint_label(
    waypoint: Waypoint,
    is_epic: bool = False,
    epic_progress: tuple[int, int] | None = None,
    width: int = 80,
    blink_visible: bool = True,
    cost: float | None = None,
) -> Text:
    """Format a waypoint for display in the tree.

    Args:
        waypoint: The waypoint to format.
        is_epic: Whether this waypoint is an epic (has children).
        epic_progress: Tuple of (complete_count, total_count) for epics.
        width: Target width for padding (fills with spaces).
        blink_visible: Whether to show the icon (for blink animation).
        cost: Optional cost in USD to display.

    Returns:
        Rich Text object with colored icon.
    """
    result = Text()

    # Get icon and color based on status
    if is_epic:
        icon = EPIC_ICON
        icon_color = EPIC_COLOR
    elif blink_visible:
        icon, icon_color = STATUS_ICONS[waypoint.status]
    else:
        icon, icon_color = STATUS_ICONS_BLINK[waypoint.status]

    # Add colored icon
    result.append(icon, style=icon_color)
    result.append(" ")

    # Add waypoint ID and title
    id_text = f"{waypoint.id}: "

    # Build progress suffix for epics
    progress_suffix = ""
    if is_epic and epic_progress:
        complete, total = epic_progress
        progress_suffix = f" ({complete}/{total})"

    # Build cost suffix for completed waypoints
    cost_suffix = ""
    if cost is not None and cost > 0:
        cost_suffix = f" [${cost:.2f}]"

    # Calculate max title length (accounting for icon + space + id + progress + cost)
    used_width = 2 + len(id_text) + len(progress_suffix) + len(cost_suffix)
    max_title_len = width - used_width

    title = waypoint.title
    if len(title) > max_title_len:
        title = title[: max_title_len - 3] + "..."

    result.append(id_text)
    result.append(title)

    # Add progress suffix for epics (in dim style)
    if progress_suffix:
        result.append(progress_suffix, style="dim")

    # Add cost suffix (in green for visibility)
    if cost_suffix:
        result.append(cost_suffix, style="green")

    # Pad to full width
    current_len = (
        2 + len(id_text) + len(title) + len(progress_suffix) + len(cost_suffix)
    )
    if current_len < width:
        result.append(" " * (width - current_len))

    return result


class FlightPlanTree(Tree[Waypoint]):
    """Tree widget for displaying the flight plan."""

    DEFAULT_CSS = """
    FlightPlanTree {
        height: 1fr;
        padding: 0;
        width: 1fr;
        overflow-x: hidden;
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

    def __init__(self, **kwargs: Any) -> None:
        super().__init__("PLAN", **kwargs)
        self._flight_plan: FlightPlan | None = None
        self._cost_by_waypoint: dict[str, float] = {}
        # Hide the root node - we just want to show waypoints
        self.show_root = False
        # Blink state for active waypoints
        self._blink_visible: bool = True
        self._blink_timer: object = None

    def on_mount(self) -> None:
        """Start the blink timer for active waypoints."""
        self._blink_timer = self.set_interval(0.5, self._toggle_blink)

    def _toggle_blink(self) -> None:
        """Toggle visibility of IN_PROGRESS waypoint icons."""
        self._blink_visible = not self._blink_visible
        self._update_active_labels()

    def update_flight_plan(
        self,
        flight_plan: FlightPlan,
        cost_by_waypoint: dict[str, float] | None = None,
    ) -> None:
        """Update the tree with a new flight plan.

        Args:
            flight_plan: The flight plan to display.
            cost_by_waypoint: Optional dict mapping waypoint ID to cost in USD.
        """
        self._flight_plan = flight_plan
        self._cost_by_waypoint = cost_by_waypoint or {}
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

                # Calculate epic progress if this is an epic
                epic_progress = None
                if is_epic and fp:
                    children = fp.get_children(wp.id)
                    complete = sum(
                        1 for c in children if c.status == WaypointStatus.COMPLETE
                    )
                    epic_progress = (complete, len(children))

                # Get cost for this waypoint
                wp_cost = self._cost_by_waypoint.get(wp.id)

                label = _format_waypoint_label(
                    wp,
                    is_epic=is_epic,
                    epic_progress=epic_progress,
                    blink_visible=self._blink_visible,
                    cost=wp_cost,
                )

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

    def _update_active_labels(self) -> None:
        """Update labels for IN_PROGRESS waypoints (for blink animation)."""
        if not self._flight_plan:
            return

        def update_node(node: TreeNode[Waypoint]) -> None:
            if node.data and node.data.status == WaypointStatus.IN_PROGRESS:
                fp = self._flight_plan
                is_epic = fp.is_epic(node.data.id) if fp else False

                # Calculate epic progress if this is an epic
                epic_progress = None
                if is_epic and fp:
                    children = fp.get_children(node.data.id)
                    complete = sum(
                        1 for c in children if c.status == WaypointStatus.COMPLETE
                    )
                    epic_progress = (complete, len(children))

                # Get cost for this waypoint
                wp_cost = self._cost_by_waypoint.get(node.data.id)

                label = _format_waypoint_label(
                    node.data,
                    is_epic=is_epic,
                    epic_progress=epic_progress,
                    blink_visible=self._blink_visible,
                    cost=wp_cost,
                )
                node.set_label(label)
            for child in node.children:
                update_node(child)

        for child in self.root.children:
            update_node(child)

    def on_tree_node_highlighted(self, event: Tree.NodeHighlighted[Waypoint]) -> None:
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
        border-right: solid $surface-lighten-1;
    }

    FlightPlanPanel .panel-title {
        text-style: bold;
        color: $text;
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

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._flight_plan: FlightPlan | None = None

    def compose(self) -> ComposeResult:
        yield Static("IMPLEMENTATION PLAN", classes="panel-title")
        yield FlightPlanTree(id="flight-tree")
        yield Static("◉ Done  ◎ Active  ○ Pending  ◇ Epic", classes="legend")

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
        color: $text;
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

    def __init__(self, **kwargs: Any) -> None:
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
        background: $surface 60%;
    }

    WaypointDetailModal > Vertical {
        width: 70%;
        max-width: 80;
        height: auto;
        max-height: 80%;
        background: $surface;
        border: solid $surface-lighten-2;
        padding: 1 2;
    }

    WaypointDetailModal .modal-title {
        text-style: bold;
        color: $text;
        text-align: center;
        padding: 1 0;
        margin-bottom: 1;
        border-bottom: solid $surface-lighten-1;
    }

    WaypointDetailModal .modal-content {
        height: auto;
        max-height: 50;
        padding: 1 0;
    }

    WaypointDetailModal .modal-content Markdown {
        margin: 0;
        padding: 0;
    }

    WaypointDetailModal .modal-actions {
        dock: bottom;
        height: 3;
        padding: 1 0 0 0;
        margin-top: 1;
        border-top: solid $surface-lighten-1;
        align: center middle;
    }

    WaypointDetailModal Button {
        margin: 0 1;
        min-width: 10;
        height: 1;
    }

    WaypointDetailModal Button#btn-edit {
        background: $primary-darken-2;
    }

    WaypointDetailModal Button#btn-delete {
        background: $surface-lighten-1;
        color: $error;
    }

    WaypointDetailModal Button#btn-close {
        background: $surface-lighten-1;
    }
    """

    def __init__(
        self, waypoint: Waypoint, is_epic: bool = False, **kwargs: Any
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
        """Edit waypoint - handled by parent screen."""
        self.dismiss(False)
        self.app.post_message(WaypointRequestEdit(self.waypoint))

    def action_break_down(self) -> None:
        """Break down waypoint - handled by parent screen."""
        self.dismiss(False)
        self.app.post_message(WaypointRequestBreakDown(self.waypoint))

    def action_delete(self) -> None:
        """Delete waypoint - handled by parent screen."""
        self.dismiss(False)
        self.app.post_message(WaypointRequestDelete(self.waypoint.id))


class WaypointRequestDelete(Message):
    """Request to delete a waypoint (bubbles up to screen)."""

    def __init__(self, waypoint_id: str) -> None:
        self.waypoint_id = waypoint_id
        super().__init__()


class WaypointRequestEdit(Message):
    """Request to edit a waypoint (bubbles up to screen)."""

    def __init__(self, waypoint: Waypoint) -> None:
        self.waypoint = waypoint
        super().__init__()


class WaypointRequestBreakDown(Message):
    """Request to break down a waypoint (bubbles up to screen)."""

    def __init__(self, waypoint: Waypoint) -> None:
        self.waypoint = waypoint
        super().__init__()


class ConfirmDeleteModal(ModalScreen[bool]):
    """Confirmation modal for deleting a waypoint."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=True),
        Binding("enter", "confirm", "Confirm", show=True),
    ]

    DEFAULT_CSS = """
    ConfirmDeleteModal {
        align: center middle;
        background: $surface 60%;
    }

    ConfirmDeleteModal > Vertical {
        width: 60;
        height: auto;
        max-height: 24;
        background: $surface;
        border: solid $surface-lighten-2;
        border-top: solid $error;
        padding: 1 2;
    }

    ConfirmDeleteModal .modal-title {
        text-style: bold;
        color: $text;
        text-align: center;
        padding: 1 0;
        margin-bottom: 1;
    }

    ConfirmDeleteModal .modal-content {
        height: auto;
        padding: 0;
    }

    ConfirmDeleteModal .waypoint-info {
        margin-bottom: 1;
        color: $text-muted;
    }

    ConfirmDeleteModal .warning {
        color: $warning;
        margin-top: 1;
        padding: 0;
    }

    ConfirmDeleteModal .modal-actions {
        dock: bottom;
        height: auto;
        padding: 1 0 0 0;
        margin-top: 1;
        border-top: solid $surface-lighten-1;
        align: center middle;
    }

    ConfirmDeleteModal Button {
        margin: 0 1;
        min-width: 10;
    }

    ConfirmDeleteModal Button#btn-delete {
        background: $error-darken-2;
    }

    ConfirmDeleteModal Button#btn-cancel {
        background: $surface-lighten-1;
    }
    """

    def __init__(
        self,
        waypoint_id: str,
        waypoint_title: str,
        has_children: bool = False,
        dependents: list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.waypoint_id = waypoint_id
        self.waypoint_title = waypoint_title
        self.has_children = has_children
        self.dependents = dependents or []

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("Delete Waypoint?", classes="modal-title")
            with Vertical(classes="modal-content"):
                yield Static(
                    f"{self.waypoint_id}: {self.waypoint_title}",
                    classes="waypoint-info",
                )
                if self.has_children:
                    yield Static(
                        "⚠ This epic has sub-waypoints that will be orphaned.",
                        classes="warning",
                    )
                if self.dependents:
                    deps = ", ".join(self.dependents[:3])
                    if len(self.dependents) > 3:
                        deps += f" +{len(self.dependents) - 3} more"
                    yield Static(
                        f"⚠ {deps} depend on this waypoint.",
                        classes="warning",
                    )
            with Horizontal(classes="modal-actions"):
                yield Button("Delete", id="btn-delete", variant="error")
                yield Button("Cancel", id="btn-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses."""
        if event.button.id == "btn-delete":
            self.dismiss(True)
        else:
            self.dismiss(False)

    def action_confirm(self) -> None:
        """Confirm deletion."""
        self.dismiss(True)

    def action_cancel(self) -> None:
        """Cancel deletion."""
        self.dismiss(False)


class WaypointUpdated(Message):
    """Waypoint was updated."""

    def __init__(self, waypoint: Waypoint) -> None:
        self.waypoint = waypoint
        super().__init__()


class WaypointEditModal(ModalScreen[Waypoint | None]):
    """Modal for editing waypoint details."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=True),
        Binding("ctrl+s", "save", "Save", show=True),
    ]

    DEFAULT_CSS = """
    WaypointEditModal {
        align: center middle;
        background: $surface 60%;
    }

    WaypointEditModal > Vertical {
        width: 80%;
        max-width: 90;
        height: auto;
        max-height: 85%;
        background: $surface;
        border: solid $surface-lighten-2;
        padding: 1 2;
    }

    WaypointEditModal .modal-title {
        text-style: bold;
        color: $text;
        text-align: center;
        padding: 1 0;
        margin-bottom: 1;
        border-bottom: solid $surface-lighten-1;
    }

    WaypointEditModal .form-content {
        height: auto;
        max-height: 45;
        padding: 0;
        scrollbar-gutter: stable;
        scrollbar-size: 1 1;
        scrollbar-background: transparent;
        scrollbar-color: $surface-lighten-2;
    }

    WaypointEditModal .field-label {
        margin-top: 1;
        margin-bottom: 0;
        color: $text-muted;
    }

    WaypointEditModal Input {
        margin-bottom: 1;
        background: $surface-lighten-1;
        border: none;
    }

    WaypointEditModal Input:focus {
        background: $surface-lighten-2;
        border: none;
    }

    WaypointEditModal TextArea {
        height: 6;
        margin-bottom: 1;
        background: $surface-lighten-1;
        border: none;
    }

    WaypointEditModal TextArea:focus {
        background: $surface-lighten-2;
        border: none;
    }

    WaypointEditModal .criteria-area {
        height: 8;
    }

    WaypointEditModal .hint {
        color: $text-disabled;
        text-style: italic;
        margin-bottom: 1;
    }

    WaypointEditModal .modal-actions {
        dock: bottom;
        height: auto;
        padding: 1 0 0 0;
        margin-top: 1;
        border-top: solid $surface-lighten-1;
        align: center middle;
    }

    WaypointEditModal Button {
        margin: 0 1;
        min-width: 10;
    }

    WaypointEditModal Button#btn-save {
        background: $success-darken-2;
    }

    WaypointEditModal Button#btn-cancel {
        background: $surface-lighten-1;
    }
    """

    def __init__(self, waypoint: Waypoint, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.waypoint = waypoint
        self._original_waypoint = waypoint

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(f"Edit {self.waypoint.id}", classes="modal-title")
            with VerticalScroll(classes="form-content"):
                yield Static("Title", classes="field-label")
                yield Input(
                    value=self.waypoint.title,
                    placeholder="Waypoint title",
                    id="input-title",
                )

                yield Static("Objective", classes="field-label")
                yield TextArea(
                    self.waypoint.objective,
                    id="input-objective",
                )

                yield Static("Acceptance Criteria", classes="field-label")
                yield Static("One criterion per line", classes="hint")
                criteria_text = "\n".join(self.waypoint.acceptance_criteria)
                yield TextArea(
                    criteria_text,
                    id="input-criteria",
                    classes="criteria-area",
                )

            with Horizontal(classes="modal-actions"):
                yield Button("Save", id="btn-save", variant="primary")
                yield Button("Cancel", id="btn-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses."""
        if event.button.id == "btn-save":
            self.action_save()
        else:
            self.action_cancel()

    def action_save(self) -> None:
        """Save changes and dismiss."""
        title_input = self.query_one("#input-title", Input)
        objective_area = self.query_one("#input-objective", TextArea)
        criteria_area = self.query_one("#input-criteria", TextArea)

        # Get values
        new_title = title_input.value.strip()
        new_objective = objective_area.text.strip()
        criteria_lines = criteria_area.text.strip().split("\n")
        new_criteria = [c.strip() for c in criteria_lines if c.strip()]

        # Validate
        if not new_title:
            self.app.notify("Title is required", severity="error")
            return
        if not new_objective:
            self.app.notify("Objective is required", severity="error")
            return

        # Create updated waypoint
        updated = Waypoint(
            id=self.waypoint.id,
            title=new_title,
            objective=new_objective,
            acceptance_criteria=new_criteria,
            parent_id=self.waypoint.parent_id,
            dependencies=self.waypoint.dependencies,
            status=self.waypoint.status,
            created_at=self.waypoint.created_at,
            completed_at=self.waypoint.completed_at,
        )

        self.dismiss(updated)

    def action_cancel(self) -> None:
        """Cancel editing."""
        self.dismiss(None)


class BreakDownPreviewModal(ModalScreen[bool]):
    """Modal showing generated sub-waypoints for confirmation."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=True),
        Binding("enter", "confirm", "Confirm", show=True),
    ]

    DEFAULT_CSS = """
    BreakDownPreviewModal {
        align: center middle;
        background: $surface 60%;
    }

    BreakDownPreviewModal > Vertical {
        width: 80%;
        max-width: 90;
        height: auto;
        max-height: 80%;
        background: $surface;
        border: solid $surface-lighten-2;
        padding: 1 2;
    }

    BreakDownPreviewModal .modal-title {
        text-style: bold;
        color: $text;
        text-align: center;
        padding: 1 0;
        margin-bottom: 1;
        border-bottom: solid $surface-lighten-1;
    }

    BreakDownPreviewModal .modal-content {
        height: auto;
        max-height: 40;
        padding: 0;
        scrollbar-gutter: stable;
        scrollbar-size: 1 1;
        scrollbar-background: transparent;
        scrollbar-color: $surface-lighten-2;
    }

    BreakDownPreviewModal .parent-info {
        color: $text-muted;
        margin-bottom: 1;
        padding-bottom: 1;
        border-bottom: dashed $surface-lighten-1;
    }

    BreakDownPreviewModal .sub-waypoint {
        margin-bottom: 1;
        padding: 1;
        background: $surface-lighten-1;
    }

    BreakDownPreviewModal .sub-title {
        text-style: bold;
        color: $text;
    }

    BreakDownPreviewModal .sub-objective {
        color: $text-muted;
        margin-top: 0;
    }

    BreakDownPreviewModal .modal-actions {
        dock: bottom;
        height: auto;
        padding: 1 0 0 0;
        margin-top: 1;
        border-top: solid $surface-lighten-1;
        align: center middle;
    }

    BreakDownPreviewModal Button {
        margin: 0 1;
        min-width: 10;
    }

    BreakDownPreviewModal Button#btn-confirm {
        background: $success-darken-2;
    }

    BreakDownPreviewModal Button#btn-cancel {
        background: $surface-lighten-1;
    }
    """

    def __init__(
        self,
        parent_waypoint: Waypoint,
        sub_waypoints: list[Waypoint],
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.parent_waypoint = parent_waypoint
        self.sub_waypoints = sub_waypoints

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("Add Sub-Waypoints?", classes="modal-title")
            with VerticalScroll(classes="modal-content"):
                parent_label = (
                    f"Breaking down: {self.parent_waypoint.id} - "
                    f"{self.parent_waypoint.title}"
                )
                yield Static(parent_label, classes="parent-info")
                for wp in self.sub_waypoints:
                    with Vertical(classes="sub-waypoint"):
                        yield Static(f"{wp.id}: {wp.title}", classes="sub-title")
                        objective = wp.objective
                        if len(objective) > 80:
                            objective = objective[:77] + "..."
                        yield Static(objective, classes="sub-objective")
            with Horizontal(classes="modal-actions"):
                yield Button(
                    f"Add {len(self.sub_waypoints)} Sub-Waypoints",
                    id="btn-confirm",
                    variant="primary",
                )
                yield Button("Cancel", id="btn-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses."""
        if event.button.id == "btn-confirm":
            self.dismiss(True)
        else:
            self.dismiss(False)

    def action_confirm(self) -> None:
        """Confirm adding sub-waypoints."""
        self.dismiss(True)

    def action_cancel(self) -> None:
        """Cancel."""
        self.dismiss(False)
