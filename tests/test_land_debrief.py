"""Tests for land debrief formatting and analysis helpers."""

from datetime import UTC, datetime
from pathlib import Path

from waypoints.fly.execution_log import ExecutionEntry, ExecutionLog
from waypoints.models.flight_plan import FlightPlan
from waypoints.models.waypoint import Waypoint
from waypoints.orchestration.debrief import (
    DebriefService,
    _extract_workspace_diff_stats,
    _format_token_summary,
)


def test_format_token_summary_returns_none_when_empty() -> None:
    """No summary for zero token counts."""
    assert _format_token_summary(0, 0) is None


def test_format_token_summary_formats_counts() -> None:
    """Summary includes formatted token counts."""
    summary = _format_token_summary(1234, 5678)
    assert summary == "Total tokens were 1,234 in / 5,678 out."


def test_format_token_summary_formats_zero_in_or_out() -> None:
    """Zero counts should still render when the other side is non-zero."""
    assert _format_token_summary(0, 42) == "Total tokens were 0 in / 42 out."
    assert _format_token_summary(42, 0) == "Total tokens were 42 in / 0 out."


# ─── Contiguous Iteration Counting ───────────────────────────────────


def _make_log_with_iterations(iterations: list[int]) -> ExecutionLog:
    """Build an ExecutionLog with iteration_start entries."""
    log = ExecutionLog(waypoint_id="WP-TEST", waypoint_title="Test")
    for it in iterations:
        log.entries.append(
            ExecutionEntry(
                entry_type="iteration_start",
                content=f"Iteration {it}",
                iteration=it,
            )
        )
    return log


def test_count_contiguous_sequential() -> None:
    """Sequential iterations 1..5 → 5."""
    log = _make_log_with_iterations([1, 2, 3, 4, 5])
    assert DebriefService._count_contiguous_iterations(log) == 5


def test_count_contiguous_gap_in_sequence() -> None:
    """Gap at iteration 3 (1, 2, 4, 5) → 2."""
    log = _make_log_with_iterations([1, 2, 4, 5])
    assert DebriefService._count_contiguous_iterations(log) == 2


def test_count_contiguous_empty_log() -> None:
    """Empty log → 0."""
    log = ExecutionLog(waypoint_id="WP-TEST", waypoint_title="Test")
    assert DebriefService._count_contiguous_iterations(log) == 0


def test_count_contiguous_no_iteration_entries() -> None:
    """Log with entries but no iteration_start → 0."""
    log = ExecutionLog(waypoint_id="WP-TEST", waypoint_title="Test")
    log.entries.append(
        ExecutionEntry(entry_type="output", content="some output", iteration=1)
    )
    assert DebriefService._count_contiguous_iterations(log) == 0


def test_extract_workspace_diff_stats_none_when_missing() -> None:
    """No workspace diff entry yields None."""
    log = ExecutionLog(waypoint_id="WP-TEST", waypoint_title="Test")
    log.entries.append(
        ExecutionEntry(entry_type="output", content="some output", iteration=1)
    )
    assert _extract_workspace_diff_stats(log) is None


def test_extract_workspace_diff_stats_from_latest_entry() -> None:
    """Latest workspace diff entry is used for stats extraction."""
    log = ExecutionLog(waypoint_id="WP-TEST", waypoint_title="Test")
    log.entries.append(
        ExecutionEntry(
            entry_type="workspace_diff",
            content="",
            metadata={
                "approx_tokens_changed": 120,
                "total_files_changed": 3,
                "files_added": 1,
                "files_modified": 1,
                "files_deleted": 1,
            },
        )
    )
    log.entries.append(
        ExecutionEntry(
            entry_type="workspace_diff",
            content="",
            metadata={
                "approx_tokens_changed": 240,
                "total_files_changed": 5,
                "files_added": 2,
                "files_modified": 2,
                "files_deleted": 1,
            },
        )
    )

    stats = _extract_workspace_diff_stats(log)
    assert stats is not None
    assert stats.approx_tokens_changed == 240
    assert stats.total_files_changed == 5
    assert stats.files_added == 2
    assert stats.files_modified == 2
    assert stats.files_deleted == 1


class _DummyProject:
    """Project stub for debrief tests."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self.slug = "demo"

    def get_path(self) -> Path:
        return self._path

    def get_docs_path(self) -> Path:
        docs = self._path / "docs"
        docs.mkdir(parents=True, exist_ok=True)
        return docs

    def get_sessions_path(self) -> Path:
        sessions = self._path / "sessions"
        sessions.mkdir(parents=True, exist_ok=True)
        return sessions


def test_waypoint_costs_include_provenance_proxy(tmp_path: Path) -> None:
    """Waypoint cost rows include provenance proxy from workspace diff logs."""
    project_path = tmp_path / "proj"
    project_path.mkdir()
    project = _DummyProject(project_path)
    flight_plan = FlightPlan(
        waypoints=[
            Waypoint(id="WP-001", title="First waypoint", objective="o1"),
            Waypoint(id="WP-002", title="Second waypoint", objective="o2"),
        ]
    )

    # Seed metrics.jsonl with costs.
    metrics_path = project_path / "metrics.jsonl"
    metrics_path.write_text(
        "\n".join(
            [
                '{"_schema":"metrics","_version":"1.0","created_at":"2026-01-01T00:00:00+00:00"}',
                '{"call_id":"c1","phase":"fly","waypoint_id":"WP-001","cost_usd":1.2,"latency_ms":1,"model":"x","timestamp":"2026-01-01T00:00:01+00:00","success":true,"error":null,"tokens_in":null,"tokens_out":null,"cached_tokens_in":null}',
                '{"call_id":"c2","phase":"fly","waypoint_id":"WP-002","cost_usd":0.8,"latency_ms":1,"model":"x","timestamp":"2026-01-01T00:00:02+00:00","success":true,"error":null,"tokens_in":null,"tokens_out":null,"cached_tokens_in":null}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    # Seed execution log for WP-001 with workspace_diff.
    fly_dir = project.get_sessions_path() / "fly"
    fly_dir.mkdir(parents=True, exist_ok=True)
    started = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC).isoformat()
    completed = datetime(2026, 1, 1, 0, 1, 0, tzinfo=UTC).isoformat()
    (fly_dir / "wp001-20260101-000000.jsonl").write_text(
        "\n".join(
            [
                (
                    '{"type":"header","_schema":"execution_log","_version":"1.0",'
                    '"execution_id":"e1","waypoint_id":"WP-001","waypoint_title":"First",'
                    '"waypoint_objective":"o1","acceptance_criteria":[],"started_at":"'
                    + started
                    + '","project_slug":"demo"}'
                ),
                (
                    '{"type":"workspace_diff","iteration":1,"result":"success",'
                    '"approx_tokens_changed":1200,"total_files_changed":4,'
                    '"files_added":1,"files_modified":2,"files_deleted":1,'
                    '"timestamp":"' + completed + '"}'
                ),
                (
                    '{"type":"completion","result":"success","total_cost_usd":1.2,'
                    '"started_at":"'
                    + started
                    + '","completed_at":"'
                    + completed
                    + '","duration_seconds":60}'
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    service = DebriefService(project=project, flight_plan=flight_plan)
    lines = service._build_waypoint_costs()

    wp1_line = next(line for line in lines if "First waypoint" in line)
    assert "~1,200 prov tok" in wp1_line
    assert "4 files (+1 ~2 -1)" in wp1_line
