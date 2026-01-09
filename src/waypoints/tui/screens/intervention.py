"""Intervention modal for handling execution failures.

This modal appears when waypoint execution fails or needs human input,
providing the user with clear options to proceed.
"""

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Markdown, Static

from waypoints.fly.intervention import (
    Intervention,
    InterventionAction,
    InterventionResult,
)


class InterventionModal(ModalScreen[InterventionResult | None]):
    """Modal shown when execution needs human intervention.

    Returns an InterventionResult with the user's chosen action,
    or None if cancelled.
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=True),
        Binding("r", "retry", "Retry", show=True),
        Binding("s", "skip", "Skip", show=True),
        Binding("e", "edit", "Edit", show=True),
        Binding("b", "rollback", "Rollback", show=True),
        Binding("a", "abort", "Abort", show=True),
    ]

    DEFAULT_CSS = """
    InterventionModal {
        align: center middle;
    }

    InterventionModal > Vertical {
        width: 80;
        max-width: 90%;
        height: auto;
        max-height: 80%;
        background: $surface;
        border: thick $error;
        padding: 1 2;
    }

    InterventionModal .modal-title {
        text-align: center;
        text-style: bold;
        color: $error;
        padding-bottom: 1;
    }

    InterventionModal .waypoint-info {
        padding: 1 0;
        border-bottom: solid $surface-lighten-1;
    }

    InterventionModal .info-label {
        color: $text-muted;
    }

    InterventionModal .info-value {
        text-style: bold;
    }

    InterventionModal .iteration-info {
        color: $warning;
    }

    InterventionModal .error-summary {
        padding: 1;
        margin: 1 0;
        background: $surface-darken-1;
        max-height: 12;
        overflow-y: auto;
    }

    InterventionModal .suggested-action {
        padding: 1 0;
        text-align: center;
        color: $text-muted;
    }

    InterventionModal .button-row {
        padding-top: 1;
        align: center middle;
        height: auto;
    }

    InterventionModal .button-row Button {
        margin: 0 1;
    }

    InterventionModal #btn-retry {
        background: $success;
    }

    InterventionModal #btn-skip {
        background: $warning;
    }

    InterventionModal #btn-edit {
        background: $primary;
    }

    InterventionModal #btn-rollback {
        background: $error-darken-1;
    }

    InterventionModal #btn-abort {
        background: $error;
    }
    """

    def __init__(self, intervention: Intervention) -> None:
        """Initialize the modal with intervention context.

        Args:
            intervention: The intervention data with failure context.
        """
        super().__init__()
        self.intervention = intervention

    def compose(self) -> ComposeResult:
        """Compose the modal layout."""
        intervention = self.intervention
        type_label = intervention.type.value.replace("_", " ").title()

        with Vertical():
            # Title
            yield Static(
                f"[bold red]Intervention Required: {type_label}[/]",
                classes="modal-title",
            )

            # Waypoint info
            with Vertical(classes="waypoint-info"):
                yield Static(
                    f"[dim]Waypoint:[/] [bold]{intervention.waypoint.id}[/] "
                    f"{intervention.waypoint.title}",
                )
                yield Static(
                    f"[dim]Iteration:[/] [yellow]{intervention.iteration}/"
                    f"{intervention.max_iterations}[/]",
                    classes="iteration-info",
                )

            # Error summary
            error_md = f"**Error:**\n\n{intervention.error_summary}"
            yield Markdown(error_md, classes="error-summary")

            # Suggested action hint
            suggested = intervention.suggested_action.value.title()
            yield Static(
                f"[dim]Suggested action: {suggested}[/]",
                classes="suggested-action",
            )

            # Action buttons - primary row
            with Horizontal(classes="button-row"):
                yield Button(
                    "Retry (+5 iterations)", id="btn-retry", variant="success"
                )
                yield Button("Skip Waypoint", id="btn-skip", variant="warning")

            # Action buttons - secondary row
            with Horizontal(classes="button-row"):
                yield Button("Edit & Retry", id="btn-edit", variant="primary")
                yield Button("Rollback", id="btn-rollback", variant="error")
                yield Button("Abort", id="btn-abort", variant="error")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses."""
        button_id = event.button.id

        if button_id == "btn-retry":
            self.action_retry()
        elif button_id == "btn-skip":
            self.action_skip()
        elif button_id == "btn-edit":
            self.action_edit()
        elif button_id == "btn-rollback":
            self.action_rollback()
        elif button_id == "btn-abort":
            self.action_abort()

    def action_retry(self) -> None:
        """Retry waypoint with additional iterations."""
        result = InterventionResult(
            action=InterventionAction.RETRY,
            additional_iterations=5,
        )
        self.dismiss(result)

    def action_skip(self) -> None:
        """Skip this waypoint and continue to next."""
        result = InterventionResult(
            action=InterventionAction.SKIP,
        )
        self.dismiss(result)

    def action_edit(self) -> None:
        """Edit waypoint and then retry."""
        result = InterventionResult(
            action=InterventionAction.EDIT,
            modified_waypoint=self.intervention.waypoint,
        )
        self.dismiss(result)

    def action_rollback(self) -> None:
        """Rollback to last safe git tag."""
        # Find the rollback tag from context if available
        rollback_tag = self.intervention.context.get("last_safe_tag")
        result = InterventionResult(
            action=InterventionAction.ROLLBACK,
            rollback_tag=rollback_tag,
        )
        self.dismiss(result)

    def action_abort(self) -> None:
        """Abort execution entirely."""
        result = InterventionResult(
            action=InterventionAction.ABORT,
        )
        self.dismiss(result)

    def action_cancel(self) -> None:
        """Cancel the modal (same as abort)."""
        self.dismiss(None)
