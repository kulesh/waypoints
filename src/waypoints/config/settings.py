"""Configuration and settings persistence."""

import json
import logging
import os
from pathlib import Path
from typing import Any

from waypoints.config.paths import get_paths

logger = logging.getLogger(__name__)


def get_config_dir() -> Path:
    """Get the waypoints config directory, creating if needed.

    Returns XDG-compliant path: ~/.config/waypoints/
    """
    config_dir = get_paths().global_config_dir
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir


def get_settings_path() -> Path:
    """Get the path to the settings file."""
    return get_paths().global_settings


def detect_terminal_theme() -> str:
    """Detect terminal light/dark preference."""
    import os

    # Check COLORFGBG env var (format: "fg;bg" where bg < 7 means dark)
    colorfgbg = os.environ.get("COLORFGBG", "")
    if colorfgbg:
        try:
            parts = colorfgbg.split(";")
            if len(parts) >= 2:
                bg = int(parts[-1])
                # Background color index: 0-6 typically dark, 7+ typically light
                return "textual-light" if bg >= 7 else "textual-dark"
        except (ValueError, IndexError):
            pass

    # macOS Terminal.app in dark mode
    if os.environ.get("TERM_PROGRAM") == "Apple_Terminal":
        # Apple Terminal doesn't expose dark mode directly, default to dark
        return "textual-dark"

    # iTerm2 can be queried but it's complex, default to dark
    # Most modern terminals default to dark
    return "textual-dark"


class Settings:
    """Persistent settings for Waypoints."""

    _defaults: dict[str, Any] = {
        # theme intentionally not in defaults - we detect it
    }

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._load()

    def _load(self) -> None:
        """Load settings from disk."""
        path = get_settings_path()
        if path.exists():
            try:
                self._data = json.loads(path.read_text())
            except (json.JSONDecodeError, OSError):
                self._data = {}
        else:
            self._data = {}

    def _save(self) -> None:
        """Save settings to disk."""
        path = get_settings_path()
        try:
            path.write_text(json.dumps(self._data, indent=2))
            logger.info("Saved settings to %s: %s", path, self._data)
        except OSError as e:
            logger.error("Failed to save settings: %s", e)

    def get(self, key: str) -> Any:
        """Get a setting value, falling back to default."""
        return self._data.get(key, self._defaults.get(key))

    def set(self, key: str, value: Any) -> None:
        """Set a setting value and persist to disk."""
        self._data[key] = value
        self._save()

    @property
    def theme(self) -> str:
        """Get the current theme, detecting from terminal if not set."""
        saved = self._data.get("theme")
        if saved:
            return str(saved)
        # No saved preference - detect from terminal
        return detect_terminal_theme()

    @theme.setter
    def theme(self, value: str) -> None:
        """Set the theme."""
        self.set("theme", value)

    @property
    def project_directory(self) -> Path:
        """Get the projects directory path.

        Returns the configured project directory, or defaults to
        the centralized paths workspace projects directory.
        """
        saved = self._data.get("project_directory")
        if saved:
            return Path(saved).expanduser().resolve()
        return get_paths().projects_dir

    @project_directory.setter
    def project_directory(self, value: str | Path) -> None:
        """Set the projects directory."""
        self.set("project_directory", str(value))

    @property
    def model(self) -> str:
        """Get the LLM model name for metrics tracking.

        Deprecated: Use llm_model instead. This property exists for
        backwards compatibility.
        """
        return self.llm_model

    @model.setter
    def model(self, value: str) -> None:
        """Set the model name."""
        self.llm_model = value

    # --- LLM Provider Settings ---

    @property
    def llm_provider(self) -> str:
        """Get the LLM provider name ('anthropic' or 'openai')."""
        llm = self._data.get("llm", {})
        return str(llm.get("provider", "anthropic"))

    @llm_provider.setter
    def llm_provider(self, value: str) -> None:
        """Set the LLM provider."""
        llm = self._data.get("llm", {})
        llm["provider"] = value
        self.set("llm", llm)

    @property
    def llm_model(self) -> str:
        """Get the LLM model name.

        Returns the configured model, or a default based on the provider.
        """
        llm = self._data.get("llm", {})

        # Check for model in new llm config
        if "model" in llm:
            return str(llm["model"])

        # Migration: check old top-level model setting
        old_model = self._data.get("model")
        if old_model:
            return str(old_model)

        # Default based on provider
        provider = llm.get("provider", "anthropic")
        if provider == "openai":
            return "gpt-5.2"
        return "claude-sonnet-4-5-20241022"

    @llm_model.setter
    def llm_model(self, value: str) -> None:
        """Set the LLM model name."""
        llm = self._data.get("llm", {})
        llm["model"] = value
        self.set("llm", llm)

    @property
    def openai_api_key(self) -> str | None:
        """Get OpenAI API key from settings or environment.

        Priority: settings > OPENAI_API_KEY env var
        """
        llm = self._data.get("llm", {})
        key = llm.get("openai_api_key")
        if key:
            return str(key)
        return os.environ.get("OPENAI_API_KEY")

    @openai_api_key.setter
    def openai_api_key(self, value: str | None) -> None:
        """Set OpenAI API key in settings."""
        llm = self._data.get("llm", {})
        if value:
            llm["openai_api_key"] = value
        elif "openai_api_key" in llm:
            del llm["openai_api_key"]
        self.set("llm", llm)

    @property
    def anthropic_api_key(self) -> str | None:
        """Get Anthropic API key from settings or environment.

        Priority: settings > ANTHROPIC_API_KEY env var
        Returns None if using web auth.
        """
        llm = self._data.get("llm", {})
        key = llm.get("anthropic_api_key")
        if key:
            return str(key)
        return os.environ.get("ANTHROPIC_API_KEY")

    @anthropic_api_key.setter
    def anthropic_api_key(self, value: str | None) -> None:
        """Set Anthropic API key in settings."""
        llm = self._data.get("llm", {})
        if value:
            llm["anthropic_api_key"] = value
        elif "anthropic_api_key" in llm:
            del llm["anthropic_api_key"]
        self.set("llm", llm)

    @property
    def use_web_auth(self) -> bool:
        """Whether to use web auth for Anthropic (default True).

        When True and no API key is set, Anthropic provider uses
        browser-based authentication via Claude Agent SDK.
        """
        llm = self._data.get("llm", {})
        return bool(llm.get("use_web_auth", True))

    @use_web_auth.setter
    def use_web_auth(self, value: bool) -> None:
        """Set web auth preference for Anthropic."""
        llm = self._data.get("llm", {})
        llm["use_web_auth"] = value
        self.set("llm", llm)


# Global settings instance
settings = Settings()
