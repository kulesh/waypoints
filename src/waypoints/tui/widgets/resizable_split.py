"""Resizable split container widgets.

Provides horizontal and vertical containers with a draggable divider
to resize panes.
"""

from typing import Any

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.events import MouseDown, MouseMove, MouseUp
from textual.reactive import reactive
from textual.widget import Widget


class ResizableSplit(Horizontal):
    """Horizontal container with draggable divider between two panes.

    The divider can be dragged with the mouse or adjusted with keyboard shortcuts.
    The left pane width is stored as a percentage of the total width.
    """

    # Bindings are handled by parent screens (ChartScreen, FlyScreen)
    # to avoid terminal capturing ctrl+arrow keys

    DEFAULT_CSS = """
    ResizableSplit {
        width: 100%;
        height: 100%;
    }

    ResizableSplit > .resizable-left {
        height: 100%;
    }

    ResizableSplit > .resizable-right {
        height: 100%;
    }

    ResizableSplit > .resizable-divider {
        width: 1;
        height: 100%;
        background: $surface;
    }

    ResizableSplit > .resizable-divider:hover {
        background: $surface-lighten-2;
    }

    ResizableSplit > .resizable-divider.dragging {
        background: $primary;
    }
    """

    # Left pane width as percentage (0-100)
    left_pct: reactive[int] = reactive(33)

    def __init__(
        self,
        left: Widget,
        right: Widget,
        left_pct: int = 33,
        left_min: int = 15,
        left_max: int = 70,
        **kwargs: Any,
    ) -> None:
        """Initialize the resizable split container.

        Args:
            left: The left pane widget
            right: The right pane widget
            left_pct: Initial left pane width as percentage (default 33%)
            left_min: Minimum left pane width percentage (default 15%)
            left_max: Maximum left pane width percentage (default 70%)
        """
        super().__init__(**kwargs)
        self._left = left
        self._right = right
        self._left_min = left_min
        self._left_max = left_max
        self._dragging = False
        self._drag_start_x = 0
        self._drag_start_pct = 0
        self.left_pct = left_pct

    def compose(self) -> ComposeResult:
        """Compose the left pane, divider, and right pane."""
        # Add CSS class to left pane
        self._left.add_class("resizable-left")
        yield self._left

        # Divider widget
        yield _Divider(classes="resizable-divider")

        # Add CSS class to right pane
        self._right.add_class("resizable-right")
        yield self._right

    def on_mount(self) -> None:
        """Apply initial sizing when mounted."""
        self._apply_sizes()

    def watch_left_pct(self, value: int) -> None:
        """React to left_pct changes."""
        self._apply_sizes()

    def _apply_sizes(self) -> None:
        """Apply current percentage to pane widths."""
        # Clamp to min/max
        pct = max(self._left_min, min(self._left_max, self.left_pct))
        if pct != self.left_pct:
            self.left_pct = pct
            return  # Will re-trigger via watch

        # Set widths using fr units for proportional sizing
        # left gets `pct` parts, right gets `100-pct` parts
        self._left.styles.width = f"{pct}%"
        self._right.styles.width = f"{100 - pct - 1}%"  # -1 for divider

    def _on_divider_drag_start(self, x: int) -> None:
        """Handle drag start on divider."""
        self._dragging = True
        self._drag_start_x = x
        self._drag_start_pct = self.left_pct
        self.capture_mouse()

        # Add visual feedback
        divider = self.query_one(".resizable-divider")
        divider.add_class("dragging")

    def _on_divider_drag_move(self, x: int) -> None:
        """Handle drag movement."""
        if not self._dragging:
            return

        # Calculate delta in percentage based on container width
        container_width = self.size.width
        if container_width <= 0:
            return

        delta_px = x - self._drag_start_x
        delta_pct = int((delta_px / container_width) * 100)

        new_pct = self._drag_start_pct + delta_pct
        new_pct = max(self._left_min, min(self._left_max, new_pct))

        if new_pct != self.left_pct:
            self.left_pct = new_pct

    def _on_divider_drag_end(self) -> None:
        """Handle drag end."""
        if not self._dragging:
            return

        self._dragging = False
        self.release_mouse()

        # Remove visual feedback
        divider = self.query_one(".resizable-divider")
        divider.remove_class("dragging")

    def on_mouse_move(self, event: MouseMove) -> None:
        """Handle mouse move for dragging."""
        if self._dragging:
            self._on_divider_drag_move(event.screen_x)

    def on_mouse_up(self, event: MouseUp) -> None:
        """Handle mouse up to end drag."""
        self._on_divider_drag_end()

    def action_resize_left(self) -> None:
        """Shrink the left pane by 5%."""
        self.left_pct = max(self._left_min, self.left_pct - 5)

    def action_resize_right(self) -> None:
        """Expand the left pane by 5%."""
        self.left_pct = min(self._left_max, self.left_pct + 5)


class _Divider(Widget):
    """Draggable divider widget."""

    def render(self) -> str:
        """Render empty - styling handled by CSS."""
        return ""

    def on_mouse_down(self, event: MouseDown) -> None:
        """Start drag when clicked."""
        event.stop()
        # Notify parent to start drag
        parent = self.parent
        if isinstance(parent, ResizableSplit):
            parent._on_divider_drag_start(event.screen_x)


class ResizableSplitVertical(Vertical):
    """Vertical container with draggable divider between two panes."""

    DEFAULT_CSS = """
    ResizableSplitVertical {
        width: 100%;
        height: 100%;
    }

    ResizableSplitVertical > .resizable-top {
        width: 100%;
    }

    ResizableSplitVertical > .resizable-bottom {
        width: 100%;
    }

    ResizableSplitVertical > .resizable-divider-vertical {
        height: 1;
        width: 100%;
        background: $surface;
    }

    ResizableSplitVertical > .resizable-divider-vertical:hover {
        background: $surface-lighten-2;
    }

    ResizableSplitVertical > .resizable-divider-vertical.dragging {
        background: $primary;
    }
    """

    top_pct: reactive[int] = reactive(50)

    def __init__(
        self,
        top: Widget,
        bottom: Widget,
        top_pct: int = 50,
        top_min: int = 20,
        top_max: int = 80,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._top = top
        self._bottom = bottom
        self._top_min = top_min
        self._top_max = top_max
        self._dragging = False
        self._drag_start_y = 0
        self._drag_start_pct = 0
        self.top_pct = top_pct

    def compose(self) -> ComposeResult:
        self._top.add_class("resizable-top")
        yield self._top

        yield _VerticalDivider(classes="resizable-divider-vertical")

        self._bottom.add_class("resizable-bottom")
        yield self._bottom

    def on_mount(self) -> None:
        self._apply_sizes()

    def watch_top_pct(self, value: int) -> None:
        self._apply_sizes()

    def _apply_sizes(self) -> None:
        pct = max(self._top_min, min(self._top_max, self.top_pct))
        if pct != self.top_pct:
            self.top_pct = pct
            return
        self._top.styles.height = f"{pct}%"
        self._bottom.styles.height = f"{100 - pct - 1}%"  # -1 for divider

    def _on_divider_drag_start(self, y: int) -> None:
        self._dragging = True
        self._drag_start_y = y
        self._drag_start_pct = self.top_pct
        self.capture_mouse()
        divider = self.query_one(".resizable-divider-vertical")
        divider.add_class("dragging")

    def _on_divider_drag_move(self, y: int) -> None:
        if not self._dragging:
            return
        container_height = self.size.height
        if container_height <= 0:
            return
        delta_px = y - self._drag_start_y
        delta_pct = int((delta_px / container_height) * 100)
        new_pct = self._drag_start_pct + delta_pct
        new_pct = max(self._top_min, min(self._top_max, new_pct))
        if new_pct != self.top_pct:
            self.top_pct = new_pct

    def _on_divider_drag_end(self) -> None:
        if not self._dragging:
            return
        self._dragging = False
        self.release_mouse()
        divider = self.query_one(".resizable-divider-vertical")
        divider.remove_class("dragging")

    def on_mouse_move(self, event: MouseMove) -> None:
        if self._dragging:
            self._on_divider_drag_move(event.screen_y)

    def on_mouse_up(self, event: MouseUp) -> None:
        self._on_divider_drag_end()

    def action_resize_up(self) -> None:
        self.top_pct = max(self._top_min, self.top_pct - 5)

    def action_resize_down(self) -> None:
        self.top_pct = min(self._top_max, self.top_pct + 5)


class _VerticalDivider(Widget):
    """Draggable horizontal divider widget."""

    def render(self) -> str:
        return ""

    def on_mouse_down(self, event: MouseDown) -> None:
        event.stop()
        parent = self.parent
        if isinstance(parent, ResizableSplitVertical):
            parent._on_divider_drag_start(event.screen_y)
