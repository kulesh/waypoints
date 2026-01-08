#!/usr/bin/env python3
"""Test FlyScreen directly with a sandboxed output folder.

This script creates a test environment in a specified folder and launches
the FLY screen for testing agentic waypoint execution without polluting
the main source tree.

Usage:
    python scripts/test_fly_screen.py <output-folder> [spec-file.md]

Examples:
    python scripts/test_fly_screen.py /tmp/fly-test
    python scripts/test_fly_screen.py /tmp/fly-test docs/product-spec.md
"""

import os
import sys
from datetime import datetime
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from textual.app import App
from textual.binding import Binding

from waypoints.models.flight_plan import FlightPlan
from waypoints.models.project import Project
from waypoints.models.waypoint import Waypoint, WaypointStatus
from waypoints.tui.screens.fly import FlyScreen

# Sample spec for testing
DEFAULT_SPEC = """# Test Project Specification

## Overview
A simple Python utility for greeting users.

## Requirements
- Create a greet.py module with a `greet(name)` function
- The function should return "Hello, {name}!"
- Add a main block that greets "World" by default

## Technical Details
- Python 3.10+
- No external dependencies
- Include basic tests
"""

# Sample waypoints for testing
DEFAULT_WAYPOINTS = [
    {
        "id": "WP-001",
        "title": "Create greeting module",
        "objective": "Create a Python module with a greet function",
        "acceptance_criteria": [
            "File greet.py exists",
            "Function greet(name) returns 'Hello, {name}!'",
            "Module has a main block that prints greeting",
        ],
        "dependencies": [],
    },
    {
        "id": "WP-002",
        "title": "Add tests",
        "objective": "Create pytest tests for the greeting module",
        "acceptance_criteria": [
            "File test_greet.py exists",
            "Test verifies greet('World') returns 'Hello, World!'",
            "Test verifies greet('Alice') returns 'Hello, Alice!'",
            "All tests pass with pytest",
        ],
        "dependencies": ["WP-001"],
    },
]


class TestFlyApp(App):
    """Minimal app to test FlyScreen in a sandboxed folder."""

    TITLE = "Waypoints"
    SUB_TITLE = "FLY Test"

    BINDINGS = [
        Binding("ctrl+q", "quit", "Quit"),
    ]

    CSS = """
    Screen {
        background: $surface;
    }
    """

    def __init__(
        self,
        project: Project,
        flight_plan: FlightPlan,
        spec: str,
    ):
        super().__init__()
        self.project = project
        self.flight_plan = flight_plan
        self.spec = spec

    def on_mount(self) -> None:
        self.push_screen(
            FlyScreen(
                project=self.project,
                flight_plan=self.flight_plan,
                spec=self.spec,
            )
        )

    def switch_phase(self, phase: str, data: dict | None = None) -> None:
        """Handle phase switches (just quit for test app)."""
        if phase == "chart":
            self.notify("Would return to CHART (exiting test)")
            self.exit()


def setup_test_environment(output_folder: Path) -> None:
    """Set up the test folder structure."""
    output_folder.mkdir(parents=True, exist_ok=True)

    # Create .waypoints directory structure
    waypoints_dir = output_folder / ".waypoints"
    waypoints_dir.mkdir(exist_ok=True)
    (waypoints_dir / "projects").mkdir(exist_ok=True)

    # Create a simple README in the output folder
    readme = output_folder / "README.md"
    if not readme.exists():
        readme.write_text(
            "# Test Project\n\n"
            "This folder is used for testing FLY screen waypoint execution.\n"
        )

    print(f"✓ Set up test environment in {output_folder}")


def create_test_project(output_folder: Path, name: str = "fly-test") -> Project:
    """Create a test project in the output folder."""
    # Change to output folder so Project creates files there
    original_cwd = Path.cwd()
    os.chdir(output_folder)

    try:
        project = Project.create(name=name, idea="Test project for FLY screen")
        print(f"✓ Created project: {project.name} ({project.slug})")
        return project
    finally:
        os.chdir(original_cwd)


def create_test_flight_plan(
    project: Project,
    waypoints_data: list[dict],
    output_folder: Path,
) -> FlightPlan:
    """Create a test flight plan with sample waypoints."""
    waypoints = []
    for wp_data in waypoints_data:
        wp = Waypoint(
            id=wp_data["id"],
            title=wp_data["title"],
            objective=wp_data["objective"],
            acceptance_criteria=wp_data.get("acceptance_criteria", []),
            dependencies=wp_data.get("dependencies", []),
            status=WaypointStatus.PENDING,
            created_at=datetime.now(),
        )
        waypoints.append(wp)

    flight_plan = FlightPlan(
        project_slug=project.slug,
        waypoints=waypoints,
        created_at=datetime.now(),
    )

    # Save flight plan
    original_cwd = Path.cwd()
    os.chdir(output_folder)
    try:
        from waypoints.models.flight_plan import FlightPlanWriter

        writer = FlightPlanWriter(project)
        writer.save(flight_plan)
        print(f"✓ Created flight plan with {len(waypoints)} waypoints")
    finally:
        os.chdir(original_cwd)

    return flight_plan


def main():
    if len(sys.argv) < 2:
        print("Usage: test_fly_screen.py <output-folder> [spec-file.md]")
        print()
        print("Arguments:")
        print("  output-folder  Folder where generated code will be placed")
        print("  spec-file.md   Optional product spec (uses default if omitted)")
        print()
        print("Examples:")
        print("  python scripts/test_fly_screen.py /tmp/fly-test")
        print("  python scripts/test_fly_screen.py /tmp/fly-test docs/spec.md")
        sys.exit(1)

    output_folder = Path(sys.argv[1]).resolve()

    # Load spec
    if len(sys.argv) > 2:
        spec_path = Path(sys.argv[2])
        if not spec_path.exists():
            print(f"Spec file not found: {spec_path}")
            sys.exit(1)
        spec = spec_path.read_text()
        print(f"✓ Loaded spec from {spec_path} ({len(spec)} chars)")
    else:
        spec = DEFAULT_SPEC
        print("✓ Using default test spec")

    # Set up environment
    setup_test_environment(output_folder)

    # Create project and flight plan
    project = create_test_project(output_folder)
    flight_plan = create_test_flight_plan(project, DEFAULT_WAYPOINTS, output_folder)

    print()
    print("Waypoints to execute:")
    for wp in flight_plan.waypoints:
        deps = f" (deps: {', '.join(wp.dependencies)})" if wp.dependencies else ""
        print(f"  {wp.id}: {wp.title}{deps}")

    print()
    print(f"Output folder: {output_folder}")
    print("Generated code will appear in this folder.")
    print()
    print("Launching FlyScreen...")
    print("Press Space to start execution, Ctrl+Q to quit")
    print()

    # Change to output folder and run app
    os.chdir(output_folder)

    app = TestFlyApp(
        project=project,
        flight_plan=flight_plan,
        spec=spec,
    )
    app.run()

    print()
    print(f"Done. Check {output_folder} for generated files.")


if __name__ == "__main__":
    main()
