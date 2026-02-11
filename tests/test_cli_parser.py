from __future__ import annotations

from pathlib import Path

import pytest

from waypoints.cli.parser import parse_args


def test_parse_args_defaults_to_tui_mode() -> None:
    args = parse_args([])
    assert args.command is None
    assert args.workdir is None


def test_parse_args_export_bundle() -> None:
    args = parse_args(["export", "demo-project", "--bundle", "-o", "out.genspec.zip"])
    assert args.command == "export"
    assert args.project == "demo-project"
    assert args.bundle is True
    assert args.output == Path("out.genspec.zip")


def test_parse_args_memory_refresh_spec_context() -> None:
    args = parse_args(
        ["memory", "refresh-spec-context", "demo", "--only-stale", "--all"]
    )
    assert args.command == "memory"
    assert args.memory_action == "refresh-spec-context"
    assert args.project == "demo"
    assert args.only_stale is True
    assert args.all is True


def test_parse_args_import_rejects_invalid_mode() -> None:
    with pytest.raises(SystemExit):
        _ = parse_args(["import", "spec.genspec.jsonl", "--mode", "invalid"])
