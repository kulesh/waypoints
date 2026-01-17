"""Custom header widget with model status indicator."""

from typing import Any

from textual.app import ComposeResult, RenderResult
from textual.reactive import reactive
from textual.widgets import Header, Static
from textual.widgets._header import HeaderClockSpace, HeaderTitle

from waypoints.tui.widgets.metrics import MetricsSummary


class StatusIcon(Static):
    """Status indicator that replaces the header icon.

    Shows model status:
    - Green dot: ready
    - Blinking yellow dot: thinking
    - Red dot: error
    """

    DEFAULT_CSS = """
    StatusIcon {
        dock: left;
        padding: 0 1;
        width: 3;
        content-align: left middle;
        background: initial;
    }

    StatusIcon.ready {
        color: $success;
    }

    StatusIcon.thinking {
        color: $warning;
    }

    StatusIcon.error {
        color: $error;
    }
    """

    is_thinking: reactive[bool] = reactive(False)
    has_error: reactive[bool] = reactive(False)
    _blink_visible: reactive[bool] = reactive(True)
    _blink_timer: object = None

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.add_class("ready")

    def on_mount(self) -> None:
        """Start the blink timer."""
        self._blink_timer = self.set_interval(0.5, self._toggle_blink)

    def render(self) -> RenderResult:
        """Render the status dot."""
        if self.is_thinking and not self._blink_visible:
            return " "
        return "●"

    def watch_is_thinking(self, thinking: bool) -> None:
        """Update appearance when thinking state changes."""
        self.remove_class("ready", "thinking", "error")
        if thinking:
            self.add_class("thinking")
        else:
            self.add_class("ready")
            self._blink_visible = True

    def watch_has_error(self, error: bool) -> None:
        """Update appearance when error state changes."""
        if error:
            self.remove_class("ready", "thinking")
            self.add_class("error")
            self.is_thinking = False

    def _toggle_blink(self) -> None:
        """Toggle visibility for blink effect."""
        if self.is_thinking:
            self._blink_visible = not self._blink_visible
            self.refresh()

    def set_thinking(self, thinking: bool) -> None:
        """Set the thinking state."""
        self.has_error = False
        self.is_thinking = thinking

    def set_error(self) -> None:
        """Show error state."""
        self.is_thinking = False
        self.has_error = True


class StatusHeader(Header):
    """Header with integrated status indicator.

    The status indicator is built into the header, avoiding
    layering issues with background colors.
    """

    DEFAULT_CSS = """
    StatusHeader {
        dock: top;
        width: 100%;
        background: $panel;
        color: $foreground;
        height: 1;
    }
    StatusHeader.-tall {
        height: 3;
    }
    """

    def compose(self) -> ComposeResult:
        yield StatusIcon(id="status-icon")
        yield HeaderTitle()
        yield MetricsSummary(id="metrics-summary")
        yield (HeaderClockSpace())

    def set_thinking(self, thinking: bool) -> None:
        """Set the thinking state on the status icon."""
        self.query_one("#status-icon", StatusIcon).set_thinking(thinking)

    def set_error(self) -> None:
        """Set the error state on the status icon."""
        self.query_one("#status-icon", StatusIcon).set_error()

    def set_normal(self) -> None:
        """Reset to normal ready state."""
        self.query_one("#status-icon", StatusIcon).set_thinking(False)

    def update_cost(self, cost: float) -> None:
        """Update the displayed cost in the metrics summary."""
        self.query_one("#metrics-summary", MetricsSummary).update_cost(cost)

    def set_budget(self, budget: float | None) -> None:
        """Set the budget limit for the metrics display."""
        self.query_one("#metrics-summary", MetricsSummary).set_budget(budget)
