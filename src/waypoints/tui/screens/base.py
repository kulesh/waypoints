"""Base dialogue screen for phase screens."""

from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Footer, Header

from waypoints.models.dialogue import DialogueHistory
from waypoints.tui.messages import (
    PhaseTransition,
    StreamingChunk,
    StreamingCompleted,
    StreamingStarted,
    UserSubmitted,
)
from waypoints.tui.widgets.dialogue import ChatInput, DialoguePanel, DialogueView
from waypoints.tui.widgets.status_indicator import ModelStatusIndicator

if TYPE_CHECKING:
    from waypoints.tui.widgets.dialogue import MessageWidget


class BaseDialogueScreen(Screen):
    """
    Base screen for dialogue-based phases.

    Subclasses should override:
    - handle_user_message(): Process user input and trigger LLM
    - process_response(): Handle completed response
    """

    BINDINGS = [
        Binding("ctrl+q", "quit", "Quit"),
        Binding("escape", "focus_input", "Focus Input"),
    ]

    DEFAULT_CSS = """
    BaseDialogueScreen {
        overflow: hidden;
    }

    BaseDialogueScreen ModelStatusIndicator {
        dock: top;
        layer: above;
        margin: 0 0 0 1;
        height: 1;
        width: 2;
        background: transparent;
    }
    """

    # Override in subclasses
    phase_name: str = "base"
    input_hint: str | None = None

    def __init__(
        self, history: DialogueHistory | None = None, **kwargs: object
    ) -> None:
        super().__init__(**kwargs)
        self.history = history or DialogueHistory(phase=self.phase_name)
        self._current_streaming_widget: "MessageWidget | None" = None
        self._streaming_content: str = ""

    def compose(self) -> ComposeResult:
        yield Header()
        yield ModelStatusIndicator(id="model-status")
        yield DialoguePanel(
            input_hint=self.input_hint,
            id="dialogue-panel",
        )
        yield Footer()

    def on_mount(self) -> None:
        """Set phase indicator in header."""
        self.app.sub_title = self.phase_name.upper()

    def handle_user_message(self, text: str) -> None:
        """
        Process user message. Typically:
        1. Add to history
        2. Update dialogue view
        3. Trigger LLM call via worker
        """
        raise NotImplementedError("Subclasses must implement handle_user_message()")

    def process_response(self, content: str) -> None:
        """
        Process completed LLM response. Typically:
        1. Extract structured data
        2. Determine if phase complete
        """
        raise NotImplementedError("Subclasses must implement process_response()")

    @property
    def dialogue_panel(self) -> DialoguePanel:
        return self.query_one(DialoguePanel)

    @property
    def dialogue_view(self) -> DialogueView:
        return self.dialogue_panel.view

    # Event Handlers

    def on_user_submitted(self, event: UserSubmitted) -> None:
        """Handle user input submission."""
        self.handle_user_message(event.text)

    def on_streaming_started(self, event: StreamingStarted) -> None:
        """Prepare UI for streaming response."""
        self.dialogue_panel.input_bar.disable()
        self._streaming_content = ""
        self._current_streaming_widget = None
        # Show thinking indicator until first content arrives
        self.dialogue_view.show_thinking()
        # Update model status indicator
        self.query_one("#model-status", ModelStatusIndicator).set_thinking(True)

    def on_streaming_chunk(self, event: StreamingChunk) -> None:
        """Update streaming message with new chunk."""
        self._streaming_content += event.chunk

        # Create message widget on first chunk (replaces thinking indicator)
        if not self._current_streaming_widget:
            self._current_streaming_widget = self.dialogue_view.add_assistant_message(
                self._streaming_content, streaming=True
            )
        else:
            self._current_streaming_widget.update_content(self._streaming_content)

    def on_streaming_completed(self, event: StreamingCompleted) -> None:
        """Finalize streaming and process response."""
        # Hide thinking indicator if still showing (no chunks received)
        self.dialogue_view.hide_thinking()
        # Update model status indicator
        self.query_one("#model-status", ModelStatusIndicator).set_thinking(False)

        if self._current_streaming_widget:
            # Ensure final content is displayed (important for error messages)
            if event.full_content:
                self._current_streaming_widget.update_content(event.full_content)
            self.dialogue_view.finalize_streaming(self._current_streaming_widget)
            self._current_streaming_widget = None
        elif event.full_content:
            # No chunks received but we have content (e.g., error message)
            self.dialogue_view.add_assistant_message(event.full_content)

        self.dialogue_panel.input_bar.enable()
        self.process_response(event.full_content)

    def on_phase_transition(self, event: PhaseTransition) -> None:
        """Handle transition to next phase."""
        # This would be handled by the app
        self.app.switch_phase(event.target_phase, event.data)  # type: ignore

    # Actions

    def action_focus_input(self) -> None:
        """Focus the input field."""
        self.dialogue_panel.input_bar.query_one(ChatInput).focus()
