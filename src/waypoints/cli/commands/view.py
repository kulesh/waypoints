"""View command for genspec inspection."""

from __future__ import annotations

import argparse
import sys

from waypoints.genspec.viewer import ViewOptions, load_genspec, render_view


def cmd_view(args: argparse.Namespace) -> int:
    """View a genspec JSONL file or bundle."""
    try:
        spec, metadata, checksums = load_genspec(args.path)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    options = ViewOptions(
        show_steps=not args.no_steps,
        steps_limit=args.steps_limit,
        show_preview=not args.no_preview,
        preview_lines=args.preview_lines,
    )
    output = render_view(spec, metadata, checksums, options)
    print(output)
    return 0
