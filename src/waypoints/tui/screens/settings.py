"""Settings modal for application configuration."""

import logging

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Select, Static, Switch

from waypoints.config import get_settings_path, settings

logger = logging.getLogger(__name__)

# Provider options for select widget
PROVIDER_OPTIONS = [
    ("Anthropic (Claude)", "anthropic"),
    ("OpenAI (GPT)", "openai"),
]


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
        max-height: 85%;
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
        max-height: 30;
    }

    SettingsModal .section-header {
        text-style: bold;
        color: $primary;
        padding: 1 0 0 0;
        margin-top: 1;
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

    SettingsModal Select {
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

    SettingsModal .toggle-row {
        height: auto;
        align: left middle;
    }

    SettingsModal .toggle-row Label {
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

    SettingsModal .api-key-hint {
        color: $text-disabled;
        text-style: italic;
        margin-top: 0;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("Settings", classes="modal-title")

            with VerticalScroll(classes="settings-scroll"):
                # === General Settings ===
                yield Static("General", classes="section-header")

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

                # === LLM Settings ===
                yield Static("LLM Provider", classes="section-header")

                # Provider selection
                with Vertical(classes="setting-row"):
                    yield Static("Provider", classes="setting-label")
                    yield Static(
                        "AI model provider for generation",
                        classes="setting-description",
                    )
                    yield Select(
                        PROVIDER_OPTIONS,
                        value=settings.llm_provider,
                        id="provider-select",
                    )

                # Model name
                with Vertical(classes="setting-row"):
                    yield Static("Model", classes="setting-label")
                    yield Static(
                        "Model identifier (e.g., claude-sonnet-4-5-20241022, gpt-5.2)",
                        classes="setting-description",
                    )
                    yield Input(
                        value=settings.llm_model,
                        placeholder="Model name",
                        id="model-input",
                    )

                # Web auth toggle (Anthropic only)
                with Vertical(classes="setting-row", id="web-auth-row"):
                    yield Static("Web Authentication", classes="setting-label")
                    yield Static(
                        "Use browser auth for Anthropic (no API key needed)",
                        classes="setting-description",
                    )
                    with Horizontal(classes="toggle-row"):
                        yield Label("Enabled")
                        yield Switch(value=settings.use_web_auth, id="web-auth-switch")

                # OpenAI API Key
                with Vertical(classes="setting-row", id="openai-key-row"):
                    yield Static("OpenAI API Key", classes="setting-label")
                    yield Static(
                        "Required for OpenAI models",
                        classes="setting-description",
                    )
                    yield Input(
                        value=settings.openai_api_key or "",
                        placeholder="sk-...",
                        password=True,
                        id="openai-key-input",
                    )
                    yield Static(
                        "Or set OPENAI_API_KEY environment variable",
                        classes="api-key-hint",
                    )

                # Anthropic API Key
                with Vertical(classes="setting-row", id="anthropic-key-row"):
                    yield Static("Anthropic API Key", classes="setting-label")
                    yield Static(
                        "Optional if using web auth",
                        classes="setting-description",
                    )
                    yield Input(
                        value=settings.anthropic_api_key or "",
                        placeholder="sk-ant-...",
                        password=True,
                        id="anthropic-key-input",
                    )
                    yield Static(
                        "Or set ANTHROPIC_API_KEY environment variable",
                        classes="api-key-hint",
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

    def on_mount(self) -> None:
        """Update visibility based on provider."""
        self._update_provider_ui()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses."""
        if event.button.id == "btn-save":
            self._save_settings()
        self.dismiss(None)

    def on_switch_changed(self, event: Switch.Changed) -> None:
        """Handle switch changes."""
        if event.switch.id == "theme-switch":
            new_theme = "textual-dark" if event.value else "textual-light"
            settings.theme = new_theme
            self.app.theme = new_theme

    def on_select_changed(self, event: Select.Changed) -> None:
        """Handle provider selection change."""
        if event.select.id == "provider-select":
            self._update_provider_ui()
            # Update model to default for selected provider
            provider = str(event.value)
            model_input = self.query_one("#model-input", Input)
            if provider == "openai":
                if "claude" in model_input.value.lower():
                    model_input.value = "gpt-5.2"
            elif provider == "anthropic":
                if "gpt" in model_input.value.lower():
                    model_input.value = "claude-sonnet-4-5-20241022"

    def _update_provider_ui(self) -> None:
        """Update UI elements based on selected provider."""
        try:
            provider_select = self.query_one("#provider-select", Select)
            provider = str(provider_select.value)

            # Show/hide provider-specific options
            web_auth_row = self.query_one("#web-auth-row")
            anthropic_key_row = self.query_one("#anthropic-key-row")
            openai_key_row = self.query_one("#openai-key-row")

            if provider == "anthropic":
                web_auth_row.display = True
                anthropic_key_row.display = True
                openai_key_row.display = False
            else:  # openai
                web_auth_row.display = False
                anthropic_key_row.display = False
                openai_key_row.display = True
        except Exception:
            pass  # UI not ready yet

    def _save_settings(self) -> None:
        """Save all settings."""
        # General settings
        project_dir_input = self.query_one("#project-dir-input", Input)
        new_dir = project_dir_input.value.strip()
        if new_dir:
            settings.project_directory = new_dir

        # LLM settings
        provider_select = self.query_one("#provider-select", Select)
        settings.llm_provider = str(provider_select.value)

        model_input = self.query_one("#model-input", Input)
        if model_input.value.strip():
            settings.llm_model = model_input.value.strip()

        web_auth_switch = self.query_one("#web-auth-switch", Switch)
        settings.use_web_auth = web_auth_switch.value

        # API keys (only save if non-empty to avoid overwriting env vars)
        openai_key = self.query_one("#openai-key-input", Input).value.strip()
        if openai_key:
            settings.openai_api_key = openai_key

        anthropic_key = self.query_one("#anthropic-key-input", Input).value.strip()
        if anthropic_key:
            settings.anthropic_api_key = anthropic_key

        self.notify("Settings saved", severity="information")
        logger.info(
            "Settings saved: provider=%s, model=%s",
            settings.llm_provider,
            settings.llm_model,
        )

    def action_close(self) -> None:
        """Close the modal."""
        self.dismiss(None)
