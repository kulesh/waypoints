"""Composable phase runners for Waypoints.

Unix-style runners that can be piped together:

    echo "Build a blog" | run_spark | run_shape_brief | run_shape_spec | run_chart

Each runner:
1. Reads from stdin (or file arg)
2. Calls coordinator method
3. Writes to stdout (or file arg)
4. Progress/errors to stderr
5. Exit code: 0 = success, 1 = error
"""
from __future__ import annotations

