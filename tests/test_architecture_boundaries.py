"""Architecture guardrails for Fly, CLI, and main entrypoint boundaries."""

from __future__ import annotations

import ast
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src" / "waypoints"


def _parse_module(path: Path) -> ast.AST:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _non_empty_non_comment_line_count(path: Path) -> int:
    count = 0
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line and not line.startswith("#"):
            count += 1
    return count


def test_fly_screen_blocks_cross_component_private_attribute_access() -> None:
    """Fly screen should not reach into private attrs on non-self objects."""
    fly_path = SRC_ROOT / "tui" / "screens" / "fly.py"
    source = fly_path.read_text(encoding="utf-8")
    tree = _parse_module(fly_path)

    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        # Guard only single-underscore "private" attrs; ignore dunder attrs.
        if not node.attr.startswith("_") or node.attr.startswith("__"):
            continue

        owner = node.value
        if isinstance(owner, ast.Name) and owner.id in {"self", "cls"}:
            continue

        expr = ast.get_source_segment(source, node) or ast.dump(
            node, include_attributes=False
        )
        violations.append(f"L{node.lineno}: {expr}")

    assert not violations, (
        "Found private attribute access on non-self objects in fly screen:\n"
        + "\n".join(violations)
    )


def test_main_entrypoint_stays_within_size_and_ownership_budget() -> None:
    """main.py should remain a thin entrypoint with no command business logic."""
    main_path = SRC_ROOT / "main.py"
    source = main_path.read_text(encoding="utf-8")
    tree = _parse_module(main_path)

    line_budget = 130
    line_count = _non_empty_non_comment_line_count(main_path)
    assert line_count <= line_budget, (
        f"main.py exceeded size budget: {line_count} > {line_budget} "
        "(non-empty, non-comment lines)"
    )

    parser_calls: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in {"add_argument", "add_parser", "add_subparsers"}:
                parser_calls.append(f"L{node.lineno}: {node.func.attr}")
    assert not parser_calls, (
        "main.py should not construct argparse parsers directly:\n"
        + "\n".join(parser_calls)
    )

    forbidden_import_prefixes = (
        "waypoints.fly",
        "waypoints.genspec",
        "waypoints.memory",
        "waypoints.orchestration",
        "waypoints.spec",
        "waypoints.tui.screens",
        "waypoints.verify",
    )
    violating_imports: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.module is None:
            continue
        if node.module.startswith(forbidden_import_prefixes):
            violating_imports.append(f"L{node.lineno}: from {node.module} import ...")

    assert not violating_imports, (
        "main.py should delegate command business logic to waypoints.cli.*:\n"
        + "\n".join(violating_imports)
    )
    assert "run_cli(" in source, "main.py must delegate through waypoints.cli.app.run"


def test_cli_parser_and_commands_stay_separated() -> None:
    """Parser owns argparse construction; command modules own business logic."""
    parser_path = SRC_ROOT / "cli" / "parser.py"
    parser_tree = _parse_module(parser_path)

    parser_import_violations: list[str] = []
    for node in ast.walk(parser_tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("waypoints"):
                    parser_import_violations.append(
                        f"L{node.lineno}: import {alias.name}"
                    )
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            if node.module.startswith("waypoints"):
                parser_import_violations.append(
                    f"L{node.lineno}: from {node.module} import ..."
                )

    assert not parser_import_violations, (
        "cli/parser.py should not import application business modules:\n"
        + "\n".join(parser_import_violations)
    )

    command_dir = SRC_ROOT / "cli" / "commands"
    parser_api_calls = {"add_argument", "add_parser", "add_subparsers", "parse_args"}
    command_violations: list[str] = []

    for cmd_file in sorted(command_dir.glob("*.py")):
        tree = _parse_module(cmd_file)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in parser_api_calls:
                    command_violations.append(
                        f"{cmd_file.relative_to(PROJECT_ROOT)}:L{node.lineno}"
                        f" uses argparse API {node.func.attr}"
                    )
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id == "ArgumentParser":
                    command_violations.append(
                        f"{cmd_file.relative_to(PROJECT_ROOT)}:L{node.lineno} "
                        "constructs ArgumentParser"
                    )

    assert not command_violations, (
        "cli/commands modules should not build parsers or parse argv:\n"
        + "\n".join(command_violations)
    )
