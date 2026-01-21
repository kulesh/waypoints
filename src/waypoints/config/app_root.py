"""App-root discovery (use sparingly)."""
from __future__ import annotations

from pathlib import Path


def dangerous_app_root() -> Path:
    """Return the Waypoints source root.

    This should rarely be used. Prefer project paths instead.
    """
    # app_root/.../src/waypoints/config/app_root.py -> app_root/
    return Path(__file__).resolve().parents[3]
