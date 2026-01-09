"""Model status indicator widget."""

from textual.reactive import reactive
from textual.widgets import Static


class ModelStatusIndicator(Static):
    """
    Indicator showing model availability and activity.

    - Steady dot: model ready
    - Blinking dot: model thinking/processing
    """

    DEFAULT_CSS = """
    ModelStatusIndicator {
        width: 2;
        height: 1;
        background: transparent;
    }

    ModelStatusIndicator.ready {
        color: $success;
    }

    ModelStatusIndicator.thinking {
        color: $warning;
    }

    ModelStatusIndicator.error {
        color: $error;
    }
    """

    is_thinking: reactive[bool] = reactive(False)
    _blink_visible: reactive[bool] = reactive(True)
    _blink_timer: object = None

    def __init__(self, **kwargs: object) -> None:
        super().__init__("●", **kwargs)
        self.add_class("ready")

    def on_mount(self) -> None:
        """Start the blink timer."""
        self._blink_timer = self.set_interval(0.5, self._toggle_blink)

    def watch_is_thinking(self, thinking: bool) -> None:
        """Update appearance when thinking state changes."""
        if thinking:
            self.remove_class("ready")
            self.remove_class("error")
            self.add_class("thinking")
        else:
            self.remove_class("thinking")
            self.remove_class("error")
            self.add_class("ready")
            self._blink_visible = True
            self.update("●")

    def _toggle_blink(self) -> None:
        """Toggle visibility for blink effect."""
        if self.is_thinking:
            self._blink_visible = not self._blink_visible
            self.update("●" if self._blink_visible else " ")

    def set_thinking(self, thinking: bool) -> None:
        """Set the thinking state."""
        self.is_thinking = thinking

    def set_error(self) -> None:
        """Show error state."""
        self.is_thinking = False
        self.remove_class("ready")
        self.remove_class("thinking")
        self.add_class("error")
        self.update("●")
