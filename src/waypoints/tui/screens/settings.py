"""Settings modal for application configuration."""

import logging

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Static, Switch

from waypoints.config import get_settings_path, settings

logger = logging.getLogger(__name__)


class SettingsModal(ModalScreen[None]):
    """Modal for viewing and editing application settings."""

    BINDINGS = [
        Binding("escape", "close", "Close", show=True),
    ]

    DEFAULT_CSS = """
    SettingsModal {
        align: center middle;
    }

    SettingsModal > Vertical {
        width: 70;
        max-width: 90%;
        height: auto;
        max-height: 80%;
        background: $surface;
        border: solid $primary;
        padding: 1 2;
    }

    SettingsModal .modal-title {
        text-align: center;
        text-style: bold;
        color: $text;
        padding-bottom: 1;
        border-bottom: solid $surface-lighten-1;
        margin-bottom: 1;
    }

    SettingsModal .settings-scroll {
        height: auto;
        max-height: 20;
    }

    SettingsModal .setting-row {
        height: auto;
        padding: 1 0;
        border-bottom: solid $surface-lighten-1;
    }

    SettingsModal .setting-label {
        color: $text;
        text-style: bold;
        margin-bottom: 0;
    }

    SettingsModal .setting-description {
        color: $text-muted;
        margin-bottom: 1;
    }

    SettingsModal .setting-value {
        color: $text;
    }

    SettingsModal Input {
        width: 100%;
        margin: 0;
    }

    SettingsModal .theme-row {
        height: auto;
        align: left middle;
    }

    SettingsModal .theme-row Label {
        margin-right: 2;
    }

    SettingsModal .file-path {
        color: $text-disabled;
        text-style: italic;
        padding: 1 0 0 0;
        text-align: center;
    }

    SettingsModal .button-row {
        padding-top: 1;
        align: center middle;
        height: auto;
    }

    SettingsModal .button-row Button {
        margin: 0 1;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("Settings", classes="modal-title")

            with VerticalScroll(classes="settings-scroll"):
                # Theme setting
                with Vertical(classes="setting-row"):
                    yield Static("Theme", classes="setting-label")
                    yield Static(
                        "Application color theme", classes="setting-description"
                    )
                    with Horizontal(classes="theme-row"):
                        yield Label("Dark")
                        yield Switch(
                            value=settings.theme == "textual-dark", id="theme-switch"
                        )
                        yield Label("Light mode when OFF")

                # Project directory setting
                with Vertical(classes="setting-row"):
                    yield Static("Project Directory", classes="setting-label")
                    yield Static(
                        "Base directory for storing project data",
                        classes="setting-description",
                    )
                    yield Input(
                        value=str(settings.project_directory),
                        id="project-dir-input",
                    )

            # Settings file path
            yield Static(
                f"Settings file: {get_settings_path()}",
                classes="file-path",
            )

            # Buttons
            with Horizontal(classes="button-row"):
                yield Button("Save", id="btn-save", variant="primary")
                yield Button("Close", id="btn-close")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses."""
        if event.button.id == "btn-save":
            self._save_settings()
        self.dismiss(None)

    def on_switch_changed(self, event: Switch.Changed) -> None:
        """Handle theme switch change - apply immediately."""
        if event.switch.id == "theme-switch":
            new_theme = "textual-dark" if event.value else "textual-light"
            settings.theme = new_theme
            self.app.theme = new_theme

    def _save_settings(self) -> None:
        """Save all settings."""
        # Get project directory from input
        project_dir_input = self.query_one("#project-dir-input", Input)
        new_dir = project_dir_input.value.strip()
        if new_dir:
            settings.project_directory = new_dir

        self.notify("Settings saved", severity="information")
        logger.info("Settings saved")

    def action_close(self) -> None:
        """Close the modal."""
        self.dismiss(None)
