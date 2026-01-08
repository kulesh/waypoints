"""Configuration and settings persistence."""

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def get_config_dir() -> Path:
    """Get the waypoints config directory, creating if needed."""
    config_dir = Path.home() / ".waypoints"
    config_dir.mkdir(exist_ok=True)
    return config_dir


def get_settings_path() -> Path:
    """Get the path to the settings file."""
    return get_config_dir() / "settings.json"


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

    # Check for common dark mode indicators
    colorterm = os.environ.get("COLORTERM", "").lower()
    term = os.environ.get("TERM", "").lower()

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
            return saved
        # No saved preference - detect from terminal
        return detect_terminal_theme()

    @theme.setter
    def theme(self, value: str) -> None:
        """Set the theme."""
        self.set("theme", value)


# Global settings instance
settings = Settings()
