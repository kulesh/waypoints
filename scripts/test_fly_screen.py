#!/usr/bin/env python3
"""Test FlyScreen with an existing project's flight plan.

This script loads an existing project and its flight plan, then launches
the FLY screen with code generation in the workspace folder.

Usage:
    python scripts/test_fly_screen.py <workspace-folder> <project-name>

Arguments:
    workspace-folder  Folder containing .waypoints/projects/ (also used for output)
    project-name      Name or slug of existing project to load

Examples:
    python scripts/test_fly_screen.py .waypoints/src2 avaiator
    python scripts/test_fly_screen.py ~/my-workspace my-project
"""

import os
import sys
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from textual.app import App
from textual.binding import Binding

from waypoints.models.flight_plan import FlightPlanReader
from waypoints.models.project import Project
from waypoints.tui.screens.fly import FlyScreen


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
        flight_plan,
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


def setup_output_folder(output_folder: Path) -> None:
    """Set up the output folder with src/ directory."""
    output_folder.mkdir(parents=True, exist_ok=True)

    # Create src/ directory for generated code
    src_dir = output_folder / "src"
    src_dir.mkdir(exist_ok=True)

    # Create a simple README
    readme = output_folder / "README.md"
    if not readme.exists():
        readme.write_text(
            "# FLY Test Output\n\n"
            "This folder contains code generated during FLY screen testing.\n"
            "Generated source code is in the `src/` directory.\n"
        )

    print(f"✓ Output folder ready: {output_folder}")
    print(f"  Source code will be generated in: {src_dir}")


def load_project(project_name: str) -> Project:
    """Load an existing project by name/slug."""
    # Try loading by slug first
    try:
        project = Project.load(project_name)
        print(f"✓ Loaded project: {project.name} ({project.slug})")
        return project
    except FileNotFoundError:
        pass

    # Try to find by name in all projects
    all_projects = Project.list_all()
    for p in all_projects:
        if p.name.lower() == project_name.lower():
            print(f"✓ Loaded project: {p.name} ({p.slug})")
            return p
        if p.slug == project_name.lower():
            print(f"✓ Loaded project: {p.name} ({p.slug})")
            return p

    # List available projects
    print(f"Project not found: {project_name}")
    if all_projects:
        print("\nAvailable projects:")
        for p in all_projects:
            print(f"  - {p.slug} ({p.name})")
    else:
        print("\nNo projects found. Create one first with: uv run waypoints")
    sys.exit(1)


def load_flight_plan(project: Project):
    """Load the flight plan for a project."""
    flight_plan = FlightPlanReader.load(project)

    if not flight_plan:
        print(f"No flight plan found for project: {project.name}")
        print("Create a flight plan first in the CHART phase.")
        sys.exit(1)

    pending = sum(1 for wp in flight_plan.waypoints if wp.status.value == "pending")
    complete = sum(1 for wp in flight_plan.waypoints if wp.status.value == "complete")
    print(f"✓ Loaded flight plan: {len(flight_plan.waypoints)} waypoints")
    print(f"  {pending} pending, {complete} complete")

    return flight_plan


def load_spec(project: Project) -> str:
    """Load the product spec for a project."""
    spec_path = project.get_docs_path() / "product-spec.md"
    if spec_path.exists():
        spec = spec_path.read_text()
        print(f"✓ Loaded product spec: {len(spec)} chars")
        return spec

    # Try alternative locations
    for alt_name in ["spec.md", "specification.md", "product_spec.md"]:
        alt_path = project.get_docs_path() / alt_name
        if alt_path.exists():
            spec = alt_path.read_text()
            print(f"✓ Loaded spec from {alt_name}: {len(spec)} chars")
            return spec

    print("⚠ No product spec found, using placeholder")
    idea = project.initial_idea or "No specification available."
    return f"# {project.name}\n\n{idea}"


def main():
    if len(sys.argv) < 3:
        print("Usage: test_fly_screen.py <workspace-folder> <project-name>")
        print()
        print("Arguments:")
        print("  workspace-folder  Folder containing .waypoints/projects/")
        print("  project-name      Name or slug of existing project to load")
        print()
        print("Examples:")
        print("  python scripts/test_fly_screen.py .waypoints/src2 avaiator")
        print("  python scripts/test_fly_screen.py ~/my-workspace my-project")
        print()

        # List available projects in cwd
        all_projects = Project.list_all()
        if all_projects:
            print("Available projects in current directory:")
            for p in all_projects:
                print(f"  - {p.slug} ({p.name})")

        sys.exit(1)

    workspace_folder = Path(sys.argv[1]).resolve()
    project_name = sys.argv[2]

    # Change to workspace folder FIRST so project paths resolve correctly
    if not workspace_folder.exists():
        print(f"Workspace folder not found: {workspace_folder}")
        sys.exit(1)

    print(f"Workspace: {workspace_folder}")
    os.chdir(workspace_folder)

    # Load project data from the workspace
    project = load_project(project_name)
    flight_plan = load_flight_plan(project)
    spec = load_spec(project)

    # Set up output folder (same as workspace)
    setup_output_folder(workspace_folder)

    print()
    print("Waypoints to execute:")
    for wp in flight_plan.waypoints:
        status = wp.status.value
        if status == "complete":
            marker = "◉"
        elif status == "in_progress":
            marker = "◎"
        else:
            marker = "○"
        deps = f" (deps: {', '.join(wp.dependencies)})" if wp.dependencies else ""
        print(f"  {marker} {wp.id}: {wp.title}{deps}")

    print()
    print(f"Workspace: {workspace_folder}")
    print(f"Source code will be generated in: {workspace_folder}/src/")
    print()
    print("Launching FlyScreen...")
    print("Press 'r' to start execution, Ctrl+Q to quit")
    print()

    app = TestFlyApp(
        project=project,
        flight_plan=flight_plan,
        spec=spec,
    )
    app.run()

    print()
    print(f"Done. Check {workspace_folder}/src/ for generated files.")


if __name__ == "__main__":
    main()
