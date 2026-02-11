"""Waypoints CLI package."""

from .app import dispatch, run
from .parser import build_parser, parse_args

__all__ = ["build_parser", "dispatch", "parse_args", "run"]
