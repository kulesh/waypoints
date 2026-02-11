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
from waypoints.tui.widgets.flight_plan_styles import (
    ADD_WAYPOINT_MODAL_CSS,
    ADD_WAYPOINT_PREVIEW_MODAL_CSS,
    BREAKDOWN_PREVIEW_MODAL_CSS,
    CONFIRM_DELETE_MODAL_CSS,
    DEBUG_WAYPOINT_MODAL_CSS,
    FLIGHT_PLAN_PANEL_CSS,
    FLIGHT_PLAN_TREE_CSS,
    REPRIORITIZE_PREVIEW_MODAL_CSS,
    WAYPOINT_DETAIL_MODAL_CSS,
    WAYPOINT_EDIT_MODAL_CSS,
    WAYPOINT_MODAL_BASE_CSS,
    WAYPOINT_PREVIEW_PANEL_CSS,
)


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

    debug_suffix = ""
    if waypoint.debug_of:
        debug_suffix = f" (debug of {waypoint.debug_of})"

    # Build cost suffix for completed waypoints
    cost_suffix = ""
    if cost is not None and cost > 0:
        cost_suffix = f" [${cost:.2f}]"

    # Calculate max title length (accounting for icon + space + id + progress + cost)
    used_width = (
        2 + len(id_text) + len(progress_suffix) + len(debug_suffix) + len(cost_suffix)
    )
    max_title_len = width - used_width

    title = waypoint.title
    if len(title) > max_title_len:
        title = title[: max_title_len - 3] + "..."

    result.append(id_text)
    result.append(title)

    # Add progress suffix for epics (in dim style)
    if progress_suffix:
        result.append(progress_suffix, style="dim")

    if debug_suffix:
        result.append(debug_suffix, style="dim")

    # Add cost suffix (in green for visibility)
    if cost_suffix:
        result.append(cost_suffix, style="green")

    # Pad to full width
    current_len = (
        2
        + len(id_text)
        + len(title)
        + len(progress_suffix)
        + len(debug_suffix)
        + len(cost_suffix)
    )
    if current_len < width:
        result.append(" " * (width - current_len))

    return result


class FlightPlanTree(Tree[Waypoint]):
    """Tree widget for displaying the flight plan."""

    DEFAULT_CSS = FLIGHT_PLAN_TREE_CSS

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

    DEFAULT_CSS = FLIGHT_PLAN_PANEL_CSS

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

    DEFAULT_CSS = WAYPOINT_PREVIEW_PANEL_CSS

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


class WaypointModalBase(ModalScreen[Any]):
    """Base class for all waypoint-related modals.

    Provides shared CSS (centering, surface background, title bar, action bar,
    button styling) and a convention-based button dispatch via on_button_pressed.
    Subclasses override compose() to yield their specific content and define
    BINDINGS / DEFAULT_CSS only for unique elements.
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=True),
    ]

    DEFAULT_CSS = WAYPOINT_MODAL_BASE_CSS

    def action_cancel(self) -> None:
        """Cancel — dismiss with a falsy default."""
        self.dismiss(None)


class WaypointDetailModal(WaypointModalBase):
    """Modal screen showing full waypoint details."""

    BINDINGS = [
        Binding("escape", "close", "Close", show=True),
        Binding("e", "edit", "Edit", show=True),
        Binding("b", "break_down", "Break Down", show=True),
        Binding("d", "delete", "Delete", show=True),
    ]

    DEFAULT_CSS = WAYPOINT_DETAIL_MODAL_CSS

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
                yield Button("Edit", id="btn-edit", compact=True)
                yield Button("Break Down", id="btn-break", compact=True)
                yield Button("Delete", id="btn-delete", compact=True)
                yield Button("Close", id="btn-close", compact=True)

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
            self.dismiss(None)
        elif event.button.id == "btn-edit":
            self.action_edit()
        elif event.button.id == "btn-break":
            self.action_break_down()
        elif event.button.id == "btn-delete":
            self.action_delete()

    def action_close(self) -> None:
        """Close the modal."""
        self.dismiss(None)

    def action_edit(self) -> None:
        """Edit waypoint."""
        self.dismiss("edit")

    def action_break_down(self) -> None:
        """Break down waypoint."""
        self.dismiss("break_down")

    def action_delete(self) -> None:
        """Delete waypoint."""
        self.dismiss("delete")


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


class ConfirmDeleteModal(WaypointModalBase):
    """Confirmation modal for deleting a waypoint."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=True),
        Binding("enter", "confirm", "Confirm", show=True),
    ]

    DEFAULT_CSS = CONFIRM_DELETE_MODAL_CSS

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


class WaypointEditModal(WaypointModalBase):
    """Modal for editing waypoint details."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=True),
        Binding("ctrl+s", "save", "Save", show=True),
    ]

    DEFAULT_CSS = WAYPOINT_EDIT_MODAL_CSS

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


class BreakDownPreviewModal(WaypointModalBase):
    """Modal showing generated sub-waypoints for confirmation."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=True),
        Binding("enter", "confirm", "Confirm", show=True),
    ]

    DEFAULT_CSS = BREAKDOWN_PREVIEW_MODAL_CSS

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
        self.dismiss(False)


class AddWaypointModal(WaypointModalBase):
    """Modal for describing a new waypoint for AI generation."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=True),
        Binding("ctrl+enter", "add", "Add", show=True),
    ]

    DEFAULT_CSS = ADD_WAYPOINT_MODAL_CSS

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("Add New Waypoint", classes="modal-title")
            yield Static(
                "Describe what you want to add and AI will generate the waypoint:",
                classes="modal-label",
            )
            yield TextArea(id="description-input")
            with Horizontal(classes="modal-actions"):
                yield Button("Add", id="btn-add")
                yield Button("Cancel", id="btn-cancel")

    def on_mount(self) -> None:
        """Focus the description input."""
        self.query_one("#description-input", TextArea).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses."""
        if event.button.id == "btn-add":
            self._submit()
        else:
            self.dismiss(None)

    def _submit(self) -> None:
        """Submit the description for AI generation."""
        description = self.query_one("#description-input", TextArea).text.strip()
        if description:
            self.dismiss(description)
        else:
            self.notify("Please enter a description", severity="warning")

    def action_add(self) -> None:
        """Add waypoint from description."""
        self._submit()


class DebugWaypointModal(WaypointModalBase):
    """Modal for describing a debug waypoint fork."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=True),
        Binding("ctrl+enter", "add", "Fork", show=True),
    ]

    DEFAULT_CSS = DEBUG_WAYPOINT_MODAL_CSS

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("Fork Debug Waypoint", classes="modal-title")
            yield Static(
                "Describe the issue to fix or behavior to refine:",
                classes="modal-label",
            )
            yield TextArea(id="debug-input")
            with Horizontal(classes="modal-actions"):
                yield Button("Fork", id="btn-fork")
                yield Button("Cancel", id="btn-cancel")

    def on_mount(self) -> None:
        """Focus the debug input."""
        self.query_one("#debug-input", TextArea).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses."""
        if event.button.id == "btn-fork":
            self._submit()
        else:
            self.dismiss(None)

    def _submit(self) -> None:
        """Submit the debug brief."""
        brief = self.query_one("#debug-input", TextArea).text.strip()
        if brief:
            self.dismiss(brief)
        else:
            self.notify("Please enter a debug brief", severity="warning")

    def action_add(self) -> None:
        """Fork a debug waypoint from the brief."""
        self._submit()


class AddWaypointPreviewModal(WaypointModalBase):
    """Preview modal for AI-generated waypoint before adding."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=True),
        Binding("enter", "confirm", "Confirm", show=True),
    ]

    DEFAULT_CSS = ADD_WAYPOINT_PREVIEW_MODAL_CSS

    def __init__(
        self,
        waypoint: Waypoint,
        insert_after: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.waypoint = waypoint
        self.insert_after = insert_after

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("Add This Waypoint?", classes="modal-title")
            with VerticalScroll(classes="modal-content"):
                yield Static(self.waypoint.id, classes="waypoint-id")
                yield Static(self.waypoint.title, classes="waypoint-title")
                yield Static(self.waypoint.objective, classes="waypoint-objective")
                yield Static("Acceptance Criteria:", classes="section-label")
                for criterion in self.waypoint.acceptance_criteria[:5]:
                    yield Static(f"• {criterion}", classes="criteria-item")
                if len(self.waypoint.acceptance_criteria) > 5:
                    yield Static(
                        f"  +{len(self.waypoint.acceptance_criteria) - 5} more",
                        classes="criteria-item",
                    )
                if self.waypoint.dependencies:
                    deps = ", ".join(self.waypoint.dependencies)
                    yield Static(f"Dependencies: {deps}", classes="insert-info")
                if self.insert_after:
                    yield Static(
                        f"Will be inserted after: {self.insert_after}",
                        classes="insert-info",
                    )
                else:
                    yield Static("Will be appended to end", classes="insert-info")
            with Horizontal(classes="modal-actions"):
                yield Button("Add Waypoint", id="btn-confirm")
                yield Button("Cancel", id="btn-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses."""
        if event.button.id == "btn-confirm":
            self.dismiss(True)
        else:
            self.dismiss(False)

    def action_confirm(self) -> None:
        """Confirm adding the waypoint."""
        self.dismiss(True)

    def action_cancel(self) -> None:
        """Cancel."""
        self.dismiss(False)


class ReprioritizePreviewModal(WaypointModalBase):
    """Modal showing before/after waypoint order comparison."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=True),
        Binding("enter", "confirm", "Apply", show=True),
    ]

    DEFAULT_CSS = REPRIORITIZE_PREVIEW_MODAL_CSS

    def __init__(
        self,
        current_order: list[str],
        new_order: list[str],
        rationale: str,
        waypoint_titles: dict[str, str],
        changes: list[dict[str, str]] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.current_order = current_order
        self.new_order = new_order
        self.rationale = rationale
        self.waypoint_titles = waypoint_titles
        self.changes = changes or []

    def compose(self) -> ComposeResult:
        # Determine which waypoints moved
        moved_ids = set()
        for i, wp_id in enumerate(self.new_order):
            if i >= len(self.current_order) or wp_id != self.current_order[i]:
                moved_ids.add(wp_id)

        with Vertical():
            yield Static("Reprioritize Waypoints?", classes="modal-title")
            yield Static(self.rationale, classes="rationale")

            with Horizontal(classes="columns-container"):
                # Current order column
                with Vertical(classes="order-column"):
                    yield Static("Current Order", classes="column-title")
                    for i, wp_id in enumerate(self.current_order, 1):
                        title = self.waypoint_titles.get(wp_id, "")
                        if len(title) > 30:
                            title = title[:27] + "..."
                        yield Static(
                            f"{i}. {wp_id}: {title}",
                            classes="waypoint-item",
                        )

                # Arrow
                yield Static("→", classes="arrow-column")

                # New order column
                with Vertical(classes="order-column"):
                    yield Static("Proposed Order", classes="column-title")
                    for i, wp_id in enumerate(self.new_order, 1):
                        title = self.waypoint_titles.get(wp_id, "")
                        if len(title) > 30:
                            title = title[:27] + "..."
                        classes = "waypoint-item"
                        if wp_id in moved_ids:
                            classes += " waypoint-moved"
                        yield Static(f"{i}. {wp_id}: {title}", classes=classes)

            with Horizontal(classes="modal-actions"):
                yield Button("Apply New Order", id="btn-confirm", variant="primary")
                yield Button("Cancel", id="btn-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses."""
        if event.button.id == "btn-confirm":
            self.dismiss(True)
        else:
            self.dismiss(False)

    def action_confirm(self) -> None:
        """Confirm applying the new order."""
        self.dismiss(True)

    def action_cancel(self) -> None:
        """Cancel."""
        self.dismiss(False)
