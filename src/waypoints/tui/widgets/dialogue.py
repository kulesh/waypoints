"""Dialogue widgets for chat interface."""

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Markdown, Rule, Static, TextArea

from waypoints.tui.messages import UserSubmitted


class ThinkingIndicator(Static):
    """Animated dots indicator for thinking state."""

    DEFAULT_CSS = """
    ThinkingIndicator {
        height: 1;
        width: auto;
        color: $warning;
    }
    """

    _frames = ["·  ", "·· ", "···", " ··", "  ·", "   "]
    _frame: reactive[int] = reactive(0)

    def on_mount(self) -> None:
        self.set_interval(0.3, self._advance)

    def _advance(self) -> None:
        self._frame = (self._frame + 1) % len(self._frames)

    def watch__frame(self, frame: int) -> None:
        self.update(self._frames[frame])


class MessageWidget(Markdown):
    """Individual message with markdown rendering."""

    DEFAULT_CSS = """
    MessageWidget {
        width: 100%;
        padding: 0;
        margin: 0 0 2 0;
        background: transparent;
    }

    MessageWidget.user {
        color: $success;
    }

    MessageWidget.user MarkdownParagraph {
        color: $success;
    }

    MessageWidget.assistant {
        color: $text;
    }

    MessageWidget.streaming {
        color: $text-muted;
    }

    MessageWidget.streaming MarkdownParagraph {
        color: $text-muted;
    }
    """

    def __init__(
        self,
        content: str,
        role: str,
        **kwargs: object,
    ) -> None:
        # Add role prefix for user messages
        if role == "user":
            display_content = f"> {content}"
        else:
            display_content = content
        super().__init__(display_content, **kwargs)
        self.role = role
        self._raw_content = content
        self.add_class(role)

    def update_content(self, content: str) -> None:
        """Update message content (used during streaming)."""
        self._raw_content = content
        self.update(content)
        # Scroll parent into view during streaming
        if parent := self.parent:
            if hasattr(parent, "scroll_end"):
                parent.scroll_end(animate=False)


class Spacer(Static):
    """Flexible spacer that fills available space."""

    DEFAULT_CSS = """
    Spacer {
        height: 1fr;
    }
    """


class DialogueView(VerticalScroll):
    """Scrollable container for dialogue messages - bottom aligned."""

    DEFAULT_CSS = """
    DialogueView {
        height: 1fr;
        padding: 0 2 1 2;
        scrollbar-gutter: stable;
        scrollbar-size: 1 1;
        scrollbar-background: transparent;
        scrollbar-color: $surface-lighten-2;
        scrollbar-color-hover: $surface-lighten-3;
    }
    """

    auto_scroll: reactive[bool] = reactive(True)

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._last_message: MessageWidget | None = None
        self._spacer: Spacer | None = None
        self._thinking_indicator: ThinkingIndicator | None = None

    def on_mount(self) -> None:
        """Add spacer to push content to bottom."""
        self._spacer = Spacer()
        self.mount(self._spacer)

    def add_user_message(self, content: str) -> MessageWidget:
        """Add a user message and return the widget."""
        msg = MessageWidget(content, "user")
        self.mount(msg)
        self._anchor_if_enabled(msg)
        return msg

    def show_thinking(self) -> None:
        """Show animated thinking indicator."""
        if not self._thinking_indicator:
            self._thinking_indicator = ThinkingIndicator()
            self.mount(self._thinking_indicator)
            self._anchor_if_enabled(self._thinking_indicator)

    def hide_thinking(self) -> None:
        """Hide thinking indicator."""
        if self._thinking_indicator:
            self._thinking_indicator.remove()
            self._thinking_indicator = None

    def add_assistant_message(
        self, content: str = "", streaming: bool = False
    ) -> MessageWidget:
        """Add an assistant message, optionally in streaming mode."""
        # Remove thinking indicator if present
        self.hide_thinking()

        msg = MessageWidget(content, "assistant")
        if streaming:
            msg.add_class("streaming")
        self.mount(msg)
        self._anchor_if_enabled(msg)
        self._last_message = msg
        return msg

    def finalize_streaming(self, message: MessageWidget) -> None:
        """Mark streaming message as complete."""
        message.remove_class("streaming")

    def _anchor_if_enabled(self, widget: Widget) -> None:
        """Anchor widget if auto-scroll is enabled."""
        if self.auto_scroll:
            self.scroll_end(animate=False)

    def on_scroll_up(self) -> None:
        """Disable auto-scroll when user scrolls up."""
        self.auto_scroll = False

    def action_scroll_to_bottom(self) -> None:
        """Re-enable auto-scroll and scroll to bottom."""
        self.auto_scroll = True
        self.scroll_end()


class ChatInput(TextArea):
    """Text area that submits on Enter, newline on Shift+Enter."""

    def _on_key(self, event: object) -> None:
        """Handle key events before TextArea processes them."""
        from textual.events import Key

        if isinstance(event, Key):
            if event.key == "enter":
                # Plain Enter = submit
                event.prevent_default()
                event.stop()
                if text := self.text.strip():
                    self.post_message(UserSubmitted(text))
                    self.clear()
                return
            elif event.key == "shift+enter":
                # Shift+Enter = insert newline
                event.prevent_default()
                event.stop()
                self.insert("\n")
                return

        super()._on_key(event)


class InputBar(Vertical):
    """Input field at bottom of dialogue."""

    DEFAULT_CSS = """
    InputBar {
        dock: bottom;
        height: auto;
        max-height: 12;
        padding: 0;
    }

    InputBar Rule {
        color: $surface-lighten-2;
        margin: 0;
    }

    InputBar ChatInput {
        width: 100%;
        min-height: 3;
        height: auto;
        max-height: 10;
        border: none;
        background: transparent;
        padding: 1 1;
    }

    InputBar ChatInput:focus {
        border: none;
    }

    InputBar ChatInput.-disabled {
        opacity: 0.5;
    }

    InputBar .hint {
        color: $text-disabled;
        padding: 0 1;
    }
    """

    def __init__(
        self,
        hint: str | None = None,
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)
        self.hint = hint

    def compose(self) -> ComposeResult:
        yield Rule(line_style="heavy")
        if self.hint:
            yield Static(self.hint, classes="hint")
        yield ChatInput(id="dialogue-input")

    def disable(self) -> None:
        """Disable input during response generation."""
        self.query_one(ChatInput).disabled = True

    def enable(self) -> None:
        """Re-enable input after response completes."""
        inp = self.query_one(ChatInput)
        inp.disabled = False
        inp.focus()


class DialoguePanel(Vertical):
    """Complete dialogue panel combining view and input."""

    BINDINGS = [
        Binding("j", "scroll_down", "Scroll Down", show=False),
        Binding("k", "scroll_up", "Scroll Up", show=False),
        Binding("G", "scroll_to_bottom", "Go to Bottom", show=False),
    ]

    DEFAULT_CSS = """
    DialoguePanel {
        width: 100%;
        height: 1fr;
    }
    """

    def __init__(
        self,
        input_hint: str | None = None,
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)
        self.input_hint = input_hint

    def compose(self) -> ComposeResult:
        yield DialogueView(id="dialogue-view")
        yield InputBar(
            hint=self.input_hint,
            id="input-bar",
        )

    @property
    def view(self) -> DialogueView:
        return self.query_one(DialogueView)

    @property
    def input_bar(self) -> InputBar:
        return self.query_one(InputBar)

    def action_scroll_down(self) -> None:
        self.view.scroll_down()

    def action_scroll_up(self) -> None:
        self.view.scroll_up()

    def action_scroll_to_bottom(self) -> None:
        self.view.action_scroll_to_bottom()
