"""Land screen for project completion (LAND phase).

Hub screen with four activities:
- Debrief: Completion stats, issues, lessons learned
- Ship: Changelog, release notes, git tagging
- Iterate: Next steps, V2 planning, project close
- Gen Spec: View generative spec details and export to file
"""

import logging
from enum import Enum
from typing import TYPE_CHECKING, Any, cast

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, Markdown, OptionList, Static
from textual.widgets.option_list import Option

if TYPE_CHECKING:
    from waypoints.tui.app import WaypointsApp

from waypoints.fly.execution_log import ExecutionLogReader
from waypoints.git.service import GitService
from waypoints.llm.metrics import MetricsCollector
from waypoints.models import JourneyState, Project
from waypoints.models.flight_plan import FlightPlan, FlightPlanReader
from waypoints.models.waypoint import WaypointStatus
from waypoints.orchestration import JourneyCoordinator
from waypoints.tui.utils import format_duration, format_token_count
from waypoints.tui.widgets.genspec_browser import GenSpecBrowser
from waypoints.tui.widgets.header import StatusHeader


def _format_token_summary(tokens_in: int, tokens_out: int) -> str | None:
    """Format token summary text when token counts are available."""
    if not tokens_in and not tokens_out:
        return None
    return (
        "Total tokens were "
        f"{format_token_count(tokens_in)} in / "
        f"{format_token_count(tokens_out)} out."
    )


logger = logging.getLogger(__name__)


class LandActivity(Enum):
    """Activities available on the Land screen."""

    DEBRIEF = "debrief"
    SHIP = "ship"
    ITERATE = "iterate"
    GENSPEC = "genspec"


class ActivityListPanel(Vertical):
    """Left panel showing list of activities."""

    DEFAULT_CSS = """
    ActivityListPanel {
        width: 20;
        height: 100%;
        border-right: solid $surface-lighten-1;
    }

    ActivityListPanel .panel-title {
        text-style: bold;
        color: $text;
        padding: 1 0 0 0;
        text-align: center;
        border-bottom: solid $surface-lighten-1;
    }

    ActivityListPanel OptionList {
        height: 1fr;
        background: transparent;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static("ACTIVITIES", classes="panel-title")
        yield OptionList(
            Option("Debrief", id="debrief"),
            Option("Ship", id="ship"),
            Option("Iterate", id="iterate"),
            Option("Gen Spec", id="genspec"),
            id="activity-list",
        )


class DebriefPanel(VerticalScroll):
    """Debrief content panel - shows completion stats, issues, and project context."""

    DEFAULT_CSS = """
    DebriefPanel {
        width: 1fr;
        height: 100%;
        padding: 1 2;
    }

    DebriefPanel .section-title {
        text-style: bold;
        color: $text;
        margin-top: 1;
        margin-bottom: 0;
    }

    DebriefPanel .stat-line {
        color: $text-muted;
        padding-left: 2;
    }

    DebriefPanel .issue-line {
        color: $warning;
        padding-left: 2;
    }

    DebriefPanel .success-line {
        color: $success;
        padding-left: 2;
    }

    DebriefPanel .failed-line {
        color: $error;
        padding-left: 2;
    }

    DebriefPanel .muted-line {
        color: $text-muted;
        padding-left: 2;
    }

    DebriefPanel .summary-paragraph {
        color: $text-muted;
        padding: 0 0 1 0;
    }
    """

    def __init__(self, project: Project, flight_plan: FlightPlan | None, **kwargs: Any):
        super().__init__(**kwargs)
        self.project = project
        self.flight_plan = flight_plan

    def compose(self) -> ComposeResult:
        yield Static("DEBRIEF", classes="section-title")
        yield Static("", id="summary-content", classes="summary-paragraph")
        yield Static("", id="stats-content")
        yield Static("Project Outputs", classes="section-title")
        yield Static("", id="outputs-content")
        yield Static("Execution Details", classes="section-title")
        yield Static("", id="execution-content")
        yield Static("Git Context", classes="section-title")
        yield Static("", id="git-content")
        yield Static("Top Spenders", classes="section-title")
        yield Static("", id="waypoint-costs-content")
        yield Static("Outstanding Issues", classes="section-title")
        yield Static("", id="issues-content")
        yield Static("Quality Gates", classes="section-title")
        yield Static("", id="quality-content")

    def on_mount(self) -> None:
        """Load and display debrief data."""
        self._update_summary()
        self._update_stats()
        self._update_outputs()
        self._update_execution()
        self._update_git()
        self._update_waypoint_costs()
        self._update_issues()
        self._update_quality_gates()

    def _update_summary(self) -> None:
        """Generate a narrative summary of the project completion."""
        parts: list[str] = []

        # 1. Completion status
        total = 0
        completed = 0
        if self.flight_plan:
            total = len(self.flight_plan.waypoints)
            completed = sum(
                1
                for wp in self.flight_plan.waypoints
                if wp.status == WaypointStatus.COMPLETE
            )
            if completed == total and total > 0:
                parts.append(
                    f"Project completed successfully with all {total} waypoints built"
                )
            elif total > 0:
                incomplete = total - completed
                parts.append(
                    f"Project completed {completed}/{total} waypoints "
                    f"({incomplete} incomplete)"
                )

        # 2. Iterations
        total_iterations = 0
        total_seconds = 0
        try:
            log_files = ExecutionLogReader.list_logs(self.project)
            for log_path in log_files:
                log = ExecutionLogReader.load(log_path)
                if log.completed_at and log.started_at:
                    total_seconds += int(
                        (log.completed_at - log.started_at).total_seconds()
                    )
                if log.entries:
                    iteration_entries = [
                        e for e in log.entries if e.entry_type == "iteration_start"
                    ]
                    if iteration_entries:
                        iterations = sorted(e.iteration for e in iteration_entries)
                        max_iter = 0
                        for i, it in enumerate(iterations, start=1):
                            if it == i:
                                max_iter = it
                            else:
                                break
                        total_iterations += max_iter
        except Exception:
            pass

        if parts and total_iterations > 0:
            parts[-1] += f" over {total_iterations} iterations."
        elif parts:
            parts[-1] += "."

        # 3. Cost, time, tokens
        cost = 0.0
        tokens_in = 0
        tokens_out = 0
        try:
            collector = MetricsCollector(self.project)
            cost = collector.total_cost
            tokens_in = collector.total_tokens_in
            tokens_out = collector.total_tokens_out
        except Exception:
            pass

        if cost > 0 and total_seconds > 0:
            parts.append(
                f"Total LLM cost was ${cost:.2f} across "
                f"{format_duration(total_seconds)} of execution time."
            )
        elif cost > 0:
            parts.append(f"Total LLM cost was ${cost:.2f}.")
        token_summary = _format_token_summary(tokens_in, tokens_out)
        if token_summary:
            parts.append(token_summary)

        # 4. Quality gates outcome
        receipts_path = self.project.get_path() / "receipts"
        if receipts_path.exists():
            receipts = list(receipts_path.glob("*.json"))
            if receipts:
                passed, failed = self._count_receipt_status(receipts)
                if failed == 0 and passed > 0:
                    parts.append(
                        f"All {passed} quality gate receipts passed verification."
                    )
                elif passed > 0 or failed > 0:
                    parts.append(
                        f"{passed} receipts passed, {failed} failed verification."
                    )

        # 5. Top spender (optional)
        try:
            collector = MetricsCollector(self.project)
            costs = collector.cost_by_waypoint()
            if costs:
                top_wp_id, top_cost = max(costs.items(), key=lambda x: x[1])
                if top_cost >= 0.01:
                    wp_title = top_wp_id
                    if self.flight_plan:
                        for wp in self.flight_plan.waypoints:
                            if wp.id == top_wp_id:
                                wp_title = wp.title[:40]
                                break
                    parts.append(
                        f'Highest-cost waypoint was "{wp_title}" at ${top_cost:.2f}.'
                    )
        except Exception:
            pass

        summary = " ".join(parts)
        content = self.query_one("#summary-content", Static)
        content.update(summary if summary else "")

    def _count_receipt_status(self, receipts: list[Any]) -> tuple[int, int]:
        """Count passed and failed receipts."""
        import json

        passed = 0
        failed = 0
        for path in receipts:
            try:
                data = json.loads(path.read_text())
                checklist = data.get("checklist", [])
                all_passed = all(
                    item.get("status") in ("passed", "skipped") for item in checklist
                )
                if all_passed:
                    passed += 1
                else:
                    failed += 1
            except Exception:
                continue
        return passed, failed

    def _update_stats(self) -> None:
        """Update completion statistics."""
        lines: list[str] = []

        # Waypoint stats
        if self.flight_plan:
            total = len(self.flight_plan.waypoints)
            completed = sum(
                1
                for wp in self.flight_plan.waypoints
                if wp.status == WaypointStatus.COMPLETE
            )
            failed = sum(
                1
                for wp in self.flight_plan.waypoints
                if wp.status == WaypointStatus.FAILED
            )
            lines.append(f"├─ {completed}/{total} waypoints complete")
            if failed > 0:
                lines.append(f"├─ {failed} waypoints failed")

        # Time, cost, tokens
        try:
            collector = MetricsCollector(self.project)
            cost = collector.total_cost
            if cost > 0:
                lines.append(f"├─ ${cost:.2f} total cost")
            tokens_in = collector.total_tokens_in
            tokens_out = collector.total_tokens_out
            token_summary = _format_token_summary(tokens_in, tokens_out)
            if token_summary:
                lines.append(f"├─ {token_summary}")
        except Exception:
            pass

        # Total time from execution logs
        total_seconds = 0
        total_iterations = 0
        try:
            log_files = ExecutionLogReader.list_logs(self.project)
            for log_path in log_files:
                log = ExecutionLogReader.load(log_path)
                if log.completed_at and log.started_at:
                    total_seconds += int(
                        (log.completed_at - log.started_at).total_seconds()
                    )
                if log.entries:
                    iteration_entries = [
                        e for e in log.entries if e.entry_type == "iteration_start"
                    ]
                    if iteration_entries:
                        iterations = sorted(e.iteration for e in iteration_entries)
                        max_iter = 0
                        for i, it in enumerate(iterations, start=1):
                            if it == i:
                                max_iter = it
                            else:
                                break
                        total_iterations += max_iter
        except Exception:
            pass

        if total_seconds > 0:
            lines.append(f"├─ {format_duration(total_seconds)} total time")
        if total_iterations > 0:
            lines.append(f"└─ {total_iterations} iterations")

        content = self.query_one("#stats-content", Static)
        content.update("\n".join(lines) if lines else "No statistics available")

    def _update_outputs(self) -> None:
        """Update project outputs section."""
        lines: list[str] = []

        # Project directory
        lines.append(f"├─ Directory: {self.project.get_path()}")

        # Generated documents
        docs_path = self.project.get_docs_path()
        if docs_path.exists():
            docs = sorted(docs_path.glob("*.md"))
            if docs:
                for i, doc in enumerate(docs[:5]):  # Show up to 5 docs
                    prefix = "└─" if i == len(docs) - 1 else "├─"
                    lines.append(f"{prefix} {doc.name}")
            else:
                lines.append("└─ No documents generated")
        else:
            lines.append("└─ No documents generated")

        content = self.query_one("#outputs-content", Static)
        content.update("\n".join(lines))
        content.add_class("muted-line")

    def _update_execution(self) -> None:
        """Update execution details section."""
        lines: list[str] = []

        # Model used (most common model from metrics)
        try:
            collector = MetricsCollector(self.project)
            if collector._calls:
                model_counts: dict[str, int] = {}
                for call in collector._calls:
                    model_counts[call.model] = model_counts.get(call.model, 0) + 1
                primary_model = max(model_counts, key=model_counts.get)  # type: ignore
                lines.append(f"├─ Model: {primary_model}")
        except Exception:
            pass

        # Waypoint run count and total time
        try:
            log_files = ExecutionLogReader.list_logs(self.project)
            if log_files:
                lines.append(f"├─ {len(log_files)} waypoint runs")
                # Calculate total execution time
                total_seconds = 0
                for log_path in log_files:
                    log = ExecutionLogReader.load(log_path)
                    if log.completed_at and log.started_at:
                        total_seconds += int(
                            (log.completed_at - log.started_at).total_seconds()
                        )
                if total_seconds > 0:
                    lines.append(f"├─ {format_duration(total_seconds)} build time")
        except Exception:
            pass
        try:
            collector = MetricsCollector(self.project)
            token_summary = _format_token_summary(
                collector.total_tokens_in, collector.total_tokens_out
            )
            if token_summary:
                lines.append(f"└─ {token_summary}")
        except Exception:
            pass

        content = self.query_one("#execution-content", Static)
        content.update("\n".join(lines) if lines else "└─ No execution data")
        content.add_class("muted-line")

    def _update_git(self) -> None:
        """Update git context section with status icons and file counts."""
        import subprocess

        lines: list[str] = []
        project_path = self.project.get_path()

        try:
            git = GitService(project_path)
            if git.is_git_repo():
                branch = git.get_current_branch() or "HEAD"
                head = git.get_head_commit()

                # Get detailed status
                status_result = subprocess.run(
                    ["git", "status", "--porcelain"],
                    cwd=project_path,
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                status_lines = [
                    line for line in status_result.stdout.strip().split("\n") if line
                ]

                # Count file types
                staged = sum(1 for ln in status_lines if ln[0] in "MADRC")
                modified = sum(1 for ln in status_lines if ln[1] in "MD")
                untracked = sum(1 for ln in status_lines if ln.startswith("??"))
                total = len(status_lines)

                # Determine status icon
                if total == 0:
                    icon = "[green]✓[/]"
                    status_text = "clean"
                elif untracked > 0:
                    icon = "[red]●[/]"
                    status_text = f"{total} changed"
                else:
                    icon = "[yellow]●[/]"
                    status_text = f"{total} changed"

                # Branch with icon
                branch_line = f"├─ {branch} {icon} {status_text}"
                if head:
                    branch_line += f" ({head})"
                lines.append(branch_line)

                # File breakdown if there are changes
                if total > 0:
                    parts = []
                    if staged > 0:
                        parts.append(f"{staged} staged")
                    if modified > 0:
                        parts.append(f"{modified} modified")
                    if untracked > 0:
                        parts.append(f"{untracked} untracked")
                    lines.append(f"└─ {', '.join(parts)}")
                else:
                    lines.append("└─ Working tree clean")
            else:
                lines.append("└─ Not a git repository")
        except Exception:
            lines.append("└─ Git info unavailable")

        content = self.query_one("#git-content", Static)
        content.update("\n".join(lines))
        content.add_class("muted-line")

    def _update_waypoint_costs(self) -> None:
        """Update top waypoint costs section (top 5)."""
        lines: list[str] = []

        try:
            collector = MetricsCollector(self.project)
            costs = collector.cost_by_waypoint()
            if costs:
                # Sort by cost descending, take top 5
                sorted_costs = sorted(costs.items(), key=lambda x: x[1], reverse=True)
                top5 = sorted_costs[:5]

                # Get waypoint titles from flight plan
                titles: dict[str, str] = {}
                if self.flight_plan:
                    for wp in self.flight_plan.waypoints:
                        titles[wp.id] = wp.title[:30]  # Truncate long titles

                for i, (wp_id, cost) in enumerate(top5):
                    prefix = "└─" if i == len(top5) - 1 else "├─"
                    title = titles.get(wp_id, wp_id)
                    lines.append(f"{prefix} ${cost:.2f} - {title}")
            else:
                lines.append("└─ No waypoint cost data")
        except Exception:
            lines.append("└─ Cost breakdown unavailable")

        content = self.query_one("#waypoint-costs-content", Static)
        content.update("\n".join(lines))
        content.add_class("muted-line")

    def _update_issues(self) -> None:
        """Update outstanding issues list."""
        issues: list[str] = []

        # Check for failed waypoints
        if self.flight_plan:
            for wp in self.flight_plan.waypoints:
                if wp.status == WaypointStatus.FAILED:
                    issues.append(f"├─ {wp.id}: {wp.title} (failed)")

        content = self.query_one("#issues-content", Static)
        if issues:
            content.update("\n".join(issues))
            content.add_class("issue-line")
        else:
            content.update("└─ No outstanding issues")
            content.add_class("success-line")

    def _update_quality_gates(self) -> None:
        """Update quality gate results."""
        lines: list[str] = []

        # Check receipts directory for quality gate data
        receipts_path = self.project.get_path() / "receipts"
        if receipts_path.exists():
            receipts = list(receipts_path.glob("*.json"))
            if receipts:
                lines.append(f"├─ {len(receipts)} receipt(s) found")
                # Count pass/fail from most recent receipts per waypoint
                passed = 0
                failed = 0
                for receipt_path in receipts:
                    try:
                        import json

                        data = json.loads(receipt_path.read_text())
                        checklist = data.get("checklist", [])
                        all_passed = all(
                            item.get("status") in ("passed", "skipped")
                            for item in checklist
                        )
                        if all_passed:
                            passed += 1
                        else:
                            failed += 1
                    except Exception:
                        continue
                lines.append(f"└─ {passed} passed, {failed} failed")
            else:
                lines.append("└─ No quality receipts")
        else:
            lines.append("└─ No quality data")

        content = self.query_one("#quality-content", Static)
        content.update("\n".join(lines))
        content.add_class("muted-line")


class ShipPanel(VerticalScroll):
    """Ship content panel - changelog, release notes, versioning."""

    DEFAULT_CSS = """
    ShipPanel {
        width: 1fr;
        height: 100%;
        padding: 1 2;
    }

    ShipPanel .section-title {
        text-style: bold;
        color: $text;
        margin-top: 1;
        margin-bottom: 0;
    }

    ShipPanel .content {
        color: $text-muted;
        padding-left: 2;
    }

    ShipPanel .hint {
        color: $text-disabled;
        text-style: italic;
        margin-top: 2;
    }
    """

    def __init__(self, project: Project, flight_plan: FlightPlan | None, **kwargs: Any):
        super().__init__(**kwargs)
        self.project = project
        self.flight_plan = flight_plan

    def compose(self) -> ComposeResult:
        yield Static("SHIP", classes="section-title")
        yield Markdown("", id="changelog-content", classes="content")
        yield Static("", classes="hint", id="ship-hint")

    def on_mount(self) -> None:
        """Show release notes or changelog preview."""
        self._update_changelog()

    def _update_changelog(self) -> None:
        """Show release notes if available, otherwise show changelog preview."""
        content = self.query_one("#changelog-content", Markdown)

        # Check for generated release notes
        release_notes_path = self.project.get_docs_path() / "release-notes.md"
        if release_notes_path.exists():
            notes = release_notes_path.read_text()
            content.update(notes)
            return

        # Fallback to basic changelog preview
        lines: list[str] = ["Changelog Preview:", ""]

        if self.flight_plan:
            completed = [
                wp
                for wp in self.flight_plan.waypoints
                if wp.status == WaypointStatus.COMPLETE and not wp.parent_id
            ]
            for wp in completed:
                lines.append(f"- {wp.title}")

        content.update("\n".join(lines) if lines else "No completed waypoints")


class IteratePanel(VerticalScroll):
    """Iterate content panel - next steps and project closure."""

    DEFAULT_CSS = """
    IteratePanel {
        width: 1fr;
        height: 100%;
        padding: 1 2;
    }

    IteratePanel .section-title {
        text-style: bold;
        color: $text;
        margin-top: 1;
        margin-bottom: 0;
    }

    IteratePanel .content {
        color: $text-muted;
        padding-left: 2;
    }

    IteratePanel .hint {
        color: $text-disabled;
        text-style: italic;
        margin-top: 2;
    }
    """

    def __init__(self, project: Project, **kwargs: Any):
        super().__init__(**kwargs)
        self.project = project

    def compose(self) -> ComposeResult:
        yield Static("ITERATE", classes="section-title")
        yield Static("", id="iterate-content", classes="content")
        yield Static("", classes="hint", id="iterate-hint")

    def on_mount(self) -> None:
        """Show iteration options."""
        content = self.query_one("#iterate-content", Static)
        content.update(
            "What's next?\n\n"
            "├─ Start V2 iteration (new features)\n"
            "├─ Mark project as closed\n"
            "└─ Return to project list"
        )


class GenSpecPanel(Vertical):
    """Gen Spec panel with browseable tree and preview."""

    def __init__(self, project: Project, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.project = project
        self._spec: Any = None

    def compose(self) -> ComposeResult:
        yield GenSpecBrowser(
            legend_items=["[e] Export", "[Enter] Detail"],
            id="genspec-browser",
        )

    def on_mount(self) -> None:
        """Load the spec and populate the browser."""
        from waypoints.genspec import export_project

        try:
            self._spec = export_project(self.project)
            browser = self.query_one("#genspec-browser", GenSpecBrowser)
            browser.set_spec(self._spec, select_first=True)
            browser.focus_tree()
        except Exception as e:
            logger.exception("Failed to load genspec: %s", e)
            self.app.notify(f"Error loading spec: {e}", severity="error")

    def export_spec(self) -> None:
        """Export the generative spec to a file via modal."""
        from pathlib import Path

        from waypoints.genspec import export_bundle
        from waypoints.tui.widgets.genspec import ExportModal

        if not self._spec:
            self.app.notify("No spec to export", severity="warning")
            return

        def handle_export(result: tuple[str, str] | None) -> None:
            if result is None:
                return  # User cancelled
            directory, filename = result
            output_path = Path(directory) / filename

            try:
                self.app.notify("Exporting...")
                export_bundle(self._spec, output_path)
                self.app.notify(f"Exported bundle to {output_path}")
                logger.info("Exported genspec bundle to %s", output_path)
            except Exception as e:
                self.app.notify(f"Export failed: {e}", severity="error")
                logger.exception("Failed to export genspec: %s", e)

        self.app.push_screen(ExportModal(self.project.slug), handle_export)


class LandScreen(Screen[None]):
    """
    Land screen - Project completion hub.

    Four activities accessible via left panel:
    - Debrief: Stats, issues, lessons
    - Ship: Changelog, release notes, git tag
    - Iterate: V2 planning, close project
    - Gen Spec: View generative spec details and export
    """

    BINDINGS = [
        Binding("ctrl+q", "quit", "Quit", show=True),
        Binding("escape", "back", "Back", show=True),
        Binding("d", "show_debrief", "Debrief", show=True),
        Binding("s", "show_ship", "Ship", show=True),
        Binding("i", "show_iterate", "Iterate", show=True),
        Binding("v", "show_genspec", "Gen Spec", show=True),
        Binding("f", "fix_issues", "Fix Issues", show=True),
        Binding("n", "new_iteration", "New V2", show=True),
        Binding("c", "close_project", "Close", show=True),
        Binding("g", "generate_release", "Generate", show=False),
        Binding("t", "create_tag", "Tag", show=False),
        Binding("e", "export_genspec", "Export", show=False),
        Binding("h", "toggle_host_validations", "HostVal", show=True),
        Binding("r", "regenerate", "Regenerate", show=True),
    ]

    DEFAULT_CSS = """
    LandScreen {
        background: $surface;
        overflow: hidden;
    }

    LandScreen .main-container {
        width: 100%;
        height: 1fr;
    }

    LandScreen .content-area {
        width: 1fr;
        height: 100%;
    }
    """

    def __init__(
        self,
        project: Project,
        flight_plan: FlightPlan | None = None,
        spec: str = "",
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.project = project
        self.flight_plan = flight_plan or FlightPlanReader.load(project)
        self.spec = spec
        self._coordinator: JourneyCoordinator | None = None
        self.current_activity: LandActivity = LandActivity.DEBRIEF

    @property
    def coordinator(self) -> JourneyCoordinator:
        """Get the coordinator, creating if needed."""
        if self._coordinator is None:
            self._coordinator = JourneyCoordinator(
                project=self.project,
                flight_plan=self.flight_plan,
            )
        return self._coordinator
        self.current_activity = LandActivity.DEBRIEF

    @property
    def waypoints_app(self) -> "WaypointsApp":
        """Get the app as WaypointsApp for type checking."""
        return cast("WaypointsApp", self.app)

    def compose(self) -> ComposeResult:
        yield StatusHeader()
        with Horizontal(classes="main-container"):
            yield ActivityListPanel(id="activity-panel")
            with Vertical(classes="content-area", id="content-area"):
                yield DebriefPanel(self.project, self.flight_plan, id="debrief-panel")
                yield ShipPanel(self.project, self.flight_plan, id="ship-panel")
                yield IteratePanel(self.project, id="iterate-panel")
                yield GenSpecPanel(self.project, id="genspec-panel")
        yield Footer()

    def on_mount(self) -> None:
        """Initialize the Land screen."""
        self.app.sub_title = f"{self.project.name} · Land"

        # Set up metrics collection
        self.waypoints_app.set_project_for_metrics(self.project)
        # Load persisted host validation preference for this project
        self.waypoints_app.host_validations_enabled = (
            self.waypoints_app.load_host_validation_preference(self.project)
        )

        # Show only debrief panel initially
        self._show_activity(LandActivity.DEBRIEF)

        # Focus the activity list
        activity_list = self.query_one("#activity-list", OptionList)
        activity_list.focus()

        logger.info("Land screen mounted for project: %s", self.project.slug)

    def _show_activity(self, activity: LandActivity) -> None:
        """Show the specified activity panel, hide others."""
        self.current_activity = activity

        debrief = self.query_one("#debrief-panel", DebriefPanel)
        ship = self.query_one("#ship-panel", ShipPanel)
        iterate = self.query_one("#iterate-panel", IteratePanel)
        genspec = self.query_one("#genspec-panel", GenSpecPanel)

        debrief.display = activity == LandActivity.DEBRIEF
        ship.display = activity == LandActivity.SHIP
        iterate.display = activity == LandActivity.ITERATE
        genspec.display = activity == LandActivity.GENSPEC

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Handle activity selection from the list."""
        if event.option.id == "debrief":
            self._show_activity(LandActivity.DEBRIEF)
        elif event.option.id == "ship":
            self._show_activity(LandActivity.SHIP)
        elif event.option.id == "iterate":
            self._show_activity(LandActivity.ITERATE)
        elif event.option.id == "genspec":
            self._show_activity(LandActivity.GENSPEC)

    def action_show_debrief(self) -> None:
        """Show the Debrief panel."""
        self._show_activity(LandActivity.DEBRIEF)
        self._select_activity_option("debrief")

    def action_show_ship(self) -> None:
        """Show the Ship panel."""
        self._show_activity(LandActivity.SHIP)
        self._select_activity_option("ship")

    def action_show_iterate(self) -> None:
        """Show the Iterate panel."""
        self._show_activity(LandActivity.ITERATE)
        self._select_activity_option("iterate")

    def action_show_genspec(self) -> None:
        """Show the Gen Spec panel."""
        self._show_activity(LandActivity.GENSPEC)
        self._select_activity_option("genspec")

    def action_toggle_host_validations(self) -> None:
        """Toggle host validations (used in Fly phase) from Land."""
        app = self.waypoints_app
        app.host_validations_enabled = not app.host_validations_enabled
        state = "ON" if app.host_validations_enabled else "OFF (LLM-as-judge only)"
        app.save_host_validation_preference(self.project)
        self.notify(f"Host validations {state}")
        self.app.bell()
        logger.info("Host validations toggled to %s from Land", state)

    def _select_activity_option(self, option_id: str) -> None:
        """Select the specified option in the activity list."""
        activity_list = self.query_one("#activity-list", OptionList)
        for i, option in enumerate(activity_list._options):
            if option.id == option_id:
                activity_list.highlighted = i
                break

    def action_back(self) -> None:
        """Go back to Fly screen (view only)."""
        self.waypoints_app.switch_phase(
            "fly",
            {
                "project": self.project,
                "flight_plan": self.flight_plan,
                "spec": self.spec,
            },
        )

    def action_fix_issues(self) -> None:
        """Return to Fly screen to fix issues."""
        # Transition back to FLY_READY
        self.coordinator.transition(
            JourneyState.FLY_READY,
            reason="land.fix_issues",
        )
        self.waypoints_app.switch_phase(
            "fly",
            {
                "project": self.project,
                "flight_plan": self.flight_plan,
                "spec": self.spec,
            },
        )

    def action_new_iteration(self) -> None:
        """Start a new V2 iteration."""
        # Transition to SPARK_IDLE for new ideation
        self.coordinator.transition(
            JourneyState.SPARK_IDLE,
            reason="land.start_v2",
        )
        self.notify("Starting V2 iteration...")
        from waypoints.tui.screens.ideation import IdeationScreen

        # Start fresh ideation (new project will be created)
        self.app.switch_screen(IdeationScreen())

    def action_close_project(self) -> None:
        """Mark project as closed."""
        # TODO: Add status field to Project model
        self.notify(f"Project '{self.project.name}' marked as closed")
        from waypoints.tui.screens.project_selection import ProjectSelectionScreen

        self.app.switch_screen(ProjectSelectionScreen())

    def action_generate_release(self) -> None:
        """Regenerate release notes."""
        if self.current_activity == LandActivity.SHIP:
            self.project._generate_release_notes()
            # Refresh the ship panel
            ship_panel = self.query_one("#ship-panel", ShipPanel)
            ship_panel._update_changelog()
            self.notify("Release notes regenerated")

    def action_create_tag(self) -> None:
        """Create git tag (placeholder)."""
        if self.current_activity == LandActivity.SHIP:
            self.notify("Git tagging not yet implemented")

    def action_export_genspec(self) -> None:
        """Export the generative spec when in Gen Spec panel."""
        if self.current_activity == LandActivity.GENSPEC:
            genspec_panel = self.query_one("#genspec-panel", GenSpecPanel)
            genspec_panel.export_spec()

    def action_regenerate(self) -> None:
        """Start regeneration from the generative specification."""
        from waypoints.tui.widgets.genspec import RegenerateModal

        self.app.push_screen(RegenerateModal(self.project))
