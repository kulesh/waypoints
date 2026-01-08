#!/usr/bin/env python3
"""Test ChartScreen directly with a product spec file."""

import sys
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from textual.app import App

from waypoints.models.project import Project
from waypoints.tui.screens.chart import ChartScreen


class TestChartApp(App):
    """Minimal app to test ChartScreen."""

    TITLE = "Waypoints"
    SUB_TITLE = "CHART Test"

    CSS = """
    Screen {
        background: $surface;
    }
    """

    def __init__(self, spec: str, project_name: str = "Test Project"):
        super().__init__()
        self.spec = spec
        self.project_name = project_name

    def on_mount(self) -> None:
        # Create or load test project
        project = Project.create(name=self.project_name, idea="Test idea")
        self.push_screen(
            ChartScreen(
                project=project,
                spec=self.spec,
                idea="Test idea",
                brief="Test brief",
            )
        )


def main():
    if len(sys.argv) < 2:
        print("Usage: test_chart_screen.py <spec-file.md> [project-name]")
        print("Example: test_chart_screen.py docs/product-spec.md")
        sys.exit(1)

    spec_path = Path(sys.argv[1])
    if not spec_path.exists():
        print(f"File not found: {spec_path}")
        sys.exit(1)

    spec = spec_path.read_text()
    project_name = sys.argv[2] if len(sys.argv) > 2 else "chart-test"

    print(f"Loaded spec: {len(spec)} chars")
    print(f"Project: {project_name}")
    print("Launching ChartScreen...\n")

    app = TestChartApp(spec=spec, project_name=project_name)
    app.run()


if __name__ == "__main__":
    main()
