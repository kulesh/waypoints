"""Debrief service — project completion analysis.

Extracts debrief business logic (file I/O, subprocess calls, metrics
computation) from the TUI DebriefPanel into a testable, UI-independent
service. The panel becomes a thin wrapper that calls this service
and populates widgets with the returned data.
"""

import json
import logging
import subprocess
from dataclasses import dataclass, field
from typing import Any

from waypoints.fly.execution_log import ExecutionLogReader
from waypoints.git.service import GitService
from waypoints.llm.metrics import MetricsCollector
from waypoints.models.flight_plan import FlightPlan
from waypoints.models.project import Project
from waypoints.models.waypoint import WaypointStatus
from waypoints.tui.utils import format_duration, format_token_count

logger = logging.getLogger(__name__)


def _format_token_summary(tokens_in: int, tokens_out: int) -> str | None:
    """Format token summary text when token counts are available."""
    if not tokens_in and not tokens_out:
        return None
    return (
        "Total tokens were "
        f"{format_token_count(tokens_in)} in / "
        f"{format_token_count(tokens_out)} out."
    )


@dataclass
class DebriefData:
    """All data needed to render the debrief panel."""

    summary: str = ""
    stats: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    execution: list[str] = field(default_factory=list)
    git_context: list[str] = field(default_factory=list)
    waypoint_costs: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    quality_gates: list[str] = field(default_factory=list)
    has_issues: bool = False


class DebriefService:
    """Generates debrief data from project state.

    Analyzes execution logs, metrics, git status, receipts, and flight
    plan to produce a complete debrief summary.
    """

    def __init__(self, project: Project, flight_plan: FlightPlan | None) -> None:
        self._project = project
        self._flight_plan = flight_plan

    def generate(self) -> DebriefData:
        """Generate all debrief data."""
        data = DebriefData()
        data.summary = self._build_summary()
        data.stats = self._build_stats()
        data.outputs = self._build_outputs()
        data.execution = self._build_execution()
        data.git_context = self._build_git_context()
        data.waypoint_costs = self._build_waypoint_costs()
        data.issues = self._build_issues()
        data.quality_gates = self._build_quality_gates()
        no_issues_sentinel = ["└─ No outstanding issues"]
        data.has_issues = bool(data.issues) and data.issues != no_issues_sentinel
        return data

    # ─── Summary ──────────────────────────────────────────────────────

    def _build_summary(self) -> str:
        parts: list[str] = []

        # 1. Completion status
        total = 0
        completed = 0
        if self._flight_plan:
            total = len(self._flight_plan.waypoints)
            completed = sum(
                1
                for wp in self._flight_plan.waypoints
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
            log_files = ExecutionLogReader.list_logs(self._project)
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
            collector = MetricsCollector(self._project)
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
        receipts_path = self._project.get_path() / "receipts"
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

        # 5. Top spender
        try:
            collector = MetricsCollector(self._project)
            costs = collector.cost_by_waypoint()
            if costs:
                top_wp_id, top_cost = max(costs.items(), key=lambda x: x[1])
                if top_cost >= 0.01:
                    wp_title = top_wp_id
                    if self._flight_plan:
                        for wp in self._flight_plan.waypoints:
                            if wp.id == top_wp_id:
                                wp_title = wp.title[:40]
                                break
                    parts.append(
                        f'Highest-cost waypoint was "{wp_title}" at ${top_cost:.2f}.'
                    )
        except Exception:
            pass

        return " ".join(parts)

    def _count_receipt_status(self, receipts: list[Any]) -> tuple[int, int]:
        """Count passed and failed receipts."""
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

    # ─── Stats ────────────────────────────────────────────────────────

    def _build_stats(self) -> list[str]:
        lines: list[str] = []

        if self._flight_plan:
            total = len(self._flight_plan.waypoints)
            completed = sum(
                1
                for wp in self._flight_plan.waypoints
                if wp.status == WaypointStatus.COMPLETE
            )
            failed = sum(
                1
                for wp in self._flight_plan.waypoints
                if wp.status == WaypointStatus.FAILED
            )
            lines.append(f"├─ {completed}/{total} waypoints complete")
            if failed > 0:
                lines.append(f"├─ {failed} waypoints failed")

        try:
            collector = MetricsCollector(self._project)
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

        total_seconds = 0
        total_iterations = 0
        try:
            log_files = ExecutionLogReader.list_logs(self._project)
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

        return lines if lines else ["No statistics available"]

    # ─── Outputs ──────────────────────────────────────────────────────

    def _build_outputs(self) -> list[str]:
        lines: list[str] = []

        lines.append(f"├─ Directory: {self._project.get_path()}")

        docs_path = self._project.get_docs_path()
        if docs_path.exists():
            docs = sorted(docs_path.glob("*.md"))
            if docs:
                for i, doc in enumerate(docs[:5]):
                    prefix = "└─" if i == len(docs) - 1 else "├─"
                    lines.append(f"{prefix} {doc.name}")
            else:
                lines.append("└─ No documents generated")
        else:
            lines.append("└─ No documents generated")

        return lines

    # ─── Execution ────────────────────────────────────────────────────

    def _build_execution(self) -> list[str]:
        lines: list[str] = []

        try:
            collector = MetricsCollector(self._project)
            if collector._calls:
                model_counts: dict[str, int] = {}
                for call in collector._calls:
                    model_counts[call.model] = model_counts.get(call.model, 0) + 1
                primary_model = max(model_counts, key=model_counts.get)  # type: ignore[arg-type]
                lines.append(f"├─ Model: {primary_model}")
        except Exception:
            pass

        try:
            log_files = ExecutionLogReader.list_logs(self._project)
            if log_files:
                lines.append(f"├─ {len(log_files)} waypoint runs")
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
            collector = MetricsCollector(self._project)
            token_summary = _format_token_summary(
                collector.total_tokens_in, collector.total_tokens_out
            )
            if token_summary:
                lines.append(f"└─ {token_summary}")
        except Exception:
            pass

        return lines if lines else ["└─ No execution data"]

    # ─── Git Context ──────────────────────────────────────────────────

    def _build_git_context(self) -> list[str]:
        lines: list[str] = []
        project_path = self._project.get_path()

        try:
            git = GitService(project_path)
            if git.is_git_repo():
                branch = git.get_current_branch() or "HEAD"
                head = git.get_head_commit()

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

                staged = sum(1 for ln in status_lines if ln[0] in "MADRC")
                modified = sum(1 for ln in status_lines if ln[1] in "MD")
                untracked = sum(1 for ln in status_lines if ln.startswith("??"))
                total = len(status_lines)

                if total == 0:
                    icon = "[green]✓[/]"
                    status_text = "clean"
                elif untracked > 0:
                    icon = "[red]●[/]"
                    status_text = f"{total} changed"
                else:
                    icon = "[yellow]●[/]"
                    status_text = f"{total} changed"

                branch_line = f"├─ {branch} {icon} {status_text}"
                if head:
                    branch_line += f" ({head})"
                lines.append(branch_line)

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

        return lines

    # ─── Waypoint Costs ───────────────────────────────────────────────

    def _build_waypoint_costs(self) -> list[str]:
        lines: list[str] = []

        try:
            collector = MetricsCollector(self._project)
            costs = collector.cost_by_waypoint()
            if costs:
                sorted_costs = sorted(costs.items(), key=lambda x: x[1], reverse=True)
                top5 = sorted_costs[:5]

                titles: dict[str, str] = {}
                if self._flight_plan:
                    for wp in self._flight_plan.waypoints:
                        titles[wp.id] = wp.title[:30]

                for i, (wp_id, cost) in enumerate(top5):
                    prefix = "└─" if i == len(top5) - 1 else "├─"
                    title = titles.get(wp_id, wp_id)
                    lines.append(f"{prefix} ${cost:.2f} - {title}")
            else:
                lines.append("└─ No waypoint cost data")
        except Exception:
            lines.append("└─ Cost breakdown unavailable")

        return lines

    # ─── Issues ───────────────────────────────────────────────────────

    def _build_issues(self) -> list[str]:
        issues: list[str] = []

        if self._flight_plan:
            for wp in self._flight_plan.waypoints:
                if wp.status == WaypointStatus.FAILED:
                    issues.append(f"├─ {wp.id}: {wp.title} (failed)")

        return issues if issues else ["└─ No outstanding issues"]

    # ─── Quality Gates ────────────────────────────────────────────────

    def _build_quality_gates(self) -> list[str]:
        lines: list[str] = []

        receipts_path = self._project.get_path() / "receipts"
        if receipts_path.exists():
            receipts = list(receipts_path.glob("*.json"))
            if receipts:
                lines.append(f"├─ {len(receipts)} receipt(s) found")
                passed = 0
                failed = 0
                for receipt_path in receipts:
                    try:
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

        return lines
