"""Unit tests for WaypointExecutor."""

import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from waypoints.fly.executor import (
    CRITERION_PATTERN,
    ExecutionContext,
    ExecutionResult,
    ExecutionStep,
    FileOperation,
    WaypointExecutor,
    _extract_file_operation,
)
from waypoints.fly.intervention import InterventionNeededError, InterventionType
from waypoints.fly.stack import ValidationCommand
from waypoints.git.config import Checklist
from waypoints.git.receipt import CapturedEvidence, ChecklistReceipt
from waypoints.llm.metrics import BudgetExceededError
from waypoints.llm.prompts import build_execution_prompt
from waypoints.llm.providers.base import StreamChunk, StreamComplete, StreamToolUse
from waypoints.memory import WaypointMemoryRecord, save_waypoint_memory
from waypoints.models.waypoint import Waypoint
from waypoints.spec import compute_spec_hash


class TestCriterionPattern:
    """Tests for CRITERION_PATTERN regex."""

    def test_matches_single_criterion(self) -> None:
        """Match single criterion marker."""
        text = """<acceptance-criterion>
<index>0</index>
<status>verified</status>
<text>Feature implemented</text>
<evidence>Code review shows implementation is complete.</evidence>
</acceptance-criterion>"""
        matches = CRITERION_PATTERN.findall(text)
        assert len(matches) == 1
        assert matches[0][0] == "0"  # index
        assert matches[0][1] == "verified"  # status
        assert matches[0][2] == "Feature implemented"  # text
        assert "Code review" in matches[0][3]  # evidence

    def test_matches_multiple_criteria(self) -> None:
        """Match multiple criterion markers."""
        text = """Some output text
<acceptance-criterion>
<index>0</index>
<status>verified</status>
<text>First criterion done</text>
<evidence>First evidence.</evidence>
</acceptance-criterion>
More output
<acceptance-criterion>
<index>1</index>
<status>verified</status>
<text>Second criterion done</text>
<evidence>Second evidence.</evidence>
</acceptance-criterion>
<acceptance-criterion>
<index>2</index>
<status>failed</status>
<text>Third criterion done</text>
<evidence>Missing implementation.</evidence>
</acceptance-criterion>"""
        matches = CRITERION_PATTERN.findall(text)
        assert len(matches) == 3
        assert matches[0][0] == "0"
        assert matches[0][1] == "verified"
        assert matches[1][0] == "1"
        assert matches[1][1] == "verified"
        assert matches[2][0] == "2"
        assert matches[2][1] == "failed"

    def test_extracts_index_status_text_evidence(self) -> None:
        """Correctly extract index, status, text, and evidence."""
        text = """<acceptance-criterion>
<index>5</index>
<status>verified</status>
<text>Tests pass with 100% coverage</text>
<evidence>Ran pytest with --cov flag showing 100% coverage.</evidence>
</acceptance-criterion>"""
        matches = CRITERION_PATTERN.findall(text)
        assert matches[0][0] == "5"  # index
        assert matches[0][1] == "verified"  # status
        assert matches[0][2] == "Tests pass with 100% coverage"  # text
        assert "pytest" in matches[0][3]  # evidence

    def test_no_match_on_invalid_format(self) -> None:
        """No match when format is wrong."""
        invalid_texts = [
            # Missing status
            (
                "<acceptance-criterion><index>0</index>"
                "<text>Missing status</text>"
                "<evidence>E</evidence></acceptance-criterion>"
            ),
            # Missing index
            (
                "<acceptance-criterion><status>verified</status>"
                "<text>Missing index</text>"
                "<evidence>E</evidence></acceptance-criterion>"
            ),
            # Missing evidence
            (
                "<acceptance-criterion><index>0</index>"
                "<status>verified</status>"
                "<text>Missing evidence</text></acceptance-criterion>"
            ),
            # Non-numeric index
            (
                "<acceptance-criterion><index>abc</index>"
                "<status>verified</status>"
                "<text>Non-numeric</text>"
                "<evidence>E</evidence></acceptance-criterion>"
            ),
        ]
        for text in invalid_texts:
            matches = CRITERION_PATTERN.findall(text)
            assert matches == [], f"Should not match: {text}"

    def test_multiline_evidence_matched(self) -> None:
        """Evidence can span multiple lines."""
        text = """<acceptance-criterion>
<index>0</index>
<status>verified</status>
<text>Tests pass</text>
<evidence>
Line 1 of evidence.
Line 2 of evidence.
Line 3 of evidence.
</evidence>
</acceptance-criterion>"""
        matches = CRITERION_PATTERN.findall(text)
        assert len(matches) == 1
        assert "Line 1" in matches[0][3]
        assert "Line 3" in matches[0][3]


class TestExtractFileOperation:
    """Tests for _extract_file_operation function."""

    def test_edit_tool(self) -> None:
        """Extract file path from Edit tool."""
        op = _extract_file_operation(
            "Edit", {"file_path": "/project/src/main.py", "content": "..."}
        )
        assert op is not None
        assert op.tool_name == "Edit"
        assert op.file_path == "/project/src/main.py"

    def test_write_tool(self) -> None:
        """Extract file path from Write tool."""
        op = _extract_file_operation(
            "Write", {"file_path": "/project/new_file.py", "content": "..."}
        )
        assert op is not None
        assert op.tool_name == "Write"
        assert op.file_path == "/project/new_file.py"

    def test_read_tool(self) -> None:
        """Extract file path from Read tool."""
        op = _extract_file_operation("Read", {"file_path": "/project/README.md"})
        assert op is not None
        assert op.tool_name == "Read"
        assert op.file_path == "/project/README.md"

    def test_glob_tool(self) -> None:
        """Extract pattern from Glob tool."""
        op = _extract_file_operation("Glob", {"pattern": "**/*.py"})
        assert op is not None
        assert op.tool_name == "Glob"
        assert op.file_path == "**/*.py"

    def test_grep_tool(self) -> None:
        """Extract path from Grep tool."""
        op = _extract_file_operation(
            "Grep", {"pattern": "def main", "path": "/project/src"}
        )
        assert op is not None
        assert op.tool_name == "Grep"
        assert op.file_path == "/project/src"

    def test_bash_tool_short_command(self) -> None:
        """Extract command from Bash tool (short)."""
        op = _extract_file_operation("Bash", {"command": "pytest -v"})
        assert op is not None
        assert op.tool_name == "Bash"
        assert op.file_path == "pytest -v"

    def test_bash_tool_long_command_truncated(self) -> None:
        """Long Bash commands are truncated."""
        long_command = "x" * 100
        op = _extract_file_operation("Bash", {"command": long_command})
        assert op is not None
        assert len(op.file_path) == 63  # 60 + "..."
        assert op.file_path.endswith("...")

    def test_unknown_tool_returns_none(self) -> None:
        """Unknown tool returns None."""
        op = _extract_file_operation("UnknownTool", {"some": "param"})
        assert op is None

    def test_missing_file_path_returns_none(self) -> None:
        """Missing file_path returns None."""
        op = _extract_file_operation("Edit", {"content": "..."})
        assert op is None

    def test_non_string_file_path_returns_none(self) -> None:
        """Non-string file_path returns None."""
        op = _extract_file_operation("Read", {"file_path": 123})
        assert op is None


class TestBuildPrompt:
    """Tests for build_execution_prompt function."""

    @pytest.fixture
    def waypoint(self) -> Waypoint:
        """Create test waypoint."""
        return Waypoint(
            id="WP-1",
            title="Implement feature X",
            objective="Build feature X with full test coverage",
            acceptance_criteria=["Code works", "Tests pass", "Documentation updated"],
            spec_context_summary=(
                "Implement feature X according to the requirements and "
                "validate with focused tests."
            ),
            spec_section_refs=["3.1 Feature X", "6.2 Validation"],
            spec_context_hash="abc123def456abc123de",
        )

    @pytest.fixture
    def checklist(self) -> Checklist:
        """Create test checklist."""
        return Checklist(items=["Code passes linting", "All tests pass"])

    def test_includes_waypoint_id(
        self,
        waypoint: Waypoint,
        checklist: Checklist,
    ) -> None:
        """Prompt includes waypoint ID."""
        prompt = build_execution_prompt(waypoint, "spec", Path("/project"), checklist)
        assert "WP-1" in prompt

    def test_includes_waypoint_title(
        self,
        waypoint: Waypoint,
        checklist: Checklist,
    ) -> None:
        """Prompt includes waypoint title."""
        prompt = build_execution_prompt(waypoint, "spec", Path("/project"), checklist)
        assert "Implement feature X" in prompt

    def test_includes_objective(
        self,
        waypoint: Waypoint,
        checklist: Checklist,
    ) -> None:
        """Prompt includes objective."""
        prompt = build_execution_prompt(waypoint, "spec", Path("/project"), checklist)
        assert "Build feature X with full test coverage" in prompt

    def test_includes_indexed_criteria(
        self,
        waypoint: Waypoint,
        checklist: Checklist,
    ) -> None:
        """Prompt includes indexed acceptance criteria."""
        prompt = build_execution_prompt(waypoint, "spec", Path("/project"), checklist)
        assert "[0] Code works" in prompt
        assert "[1] Tests pass" in prompt
        assert "[2] Documentation updated" in prompt

    def test_includes_waypoint_spec_context(
        self,
        waypoint: Waypoint,
        checklist: Checklist,
    ) -> None:
        """Prompt includes chart-time waypoint summary and section refs."""
        prompt = build_execution_prompt(waypoint, "spec", Path("/project"), checklist)
        assert "Waypoint Spec Context (Chart-Time)" in prompt
        assert "Implement feature X according to the requirements" in prompt
        assert "3.1 Feature X" in prompt
        assert "6.2 Validation" in prompt

    def test_does_not_inline_full_spec_content(
        self,
        waypoint: Waypoint,
        checklist: Checklist,
    ) -> None:
        """Prompt should rely on summary + pointer instead of spec body."""
        long_spec = "x" * 3000
        prompt = build_execution_prompt(
            waypoint, long_spec, Path("/project"), checklist
        )
        assert long_spec[:200] not in prompt
        assert "Canonical file: `docs/product-spec.md`" in prompt

    def test_includes_stale_spec_context_warning(
        self,
        waypoint: Waypoint,
        checklist: Checklist,
    ) -> None:
        """Prompt should surface stale context warning and hash details."""
        prompt = build_execution_prompt(
            waypoint,
            "spec",
            Path("/project"),
            checklist,
            spec_context_stale=True,
            current_spec_hash="feedfacebeadfeedface",
        )
        assert "Spec Context Status" in prompt
        assert "appears stale" in prompt
        assert "waypoint spec hash: abc123def456abc123de" in prompt
        assert "current spec hash: feedfacebeadfeedface" in prompt

    def test_includes_project_path(
        self,
        waypoint: Waypoint,
        checklist: Checklist,
    ) -> None:
        """Prompt includes project path."""
        prompt = build_execution_prompt(
            waypoint, "spec", Path("/my/project"), checklist
        )
        assert "/my/project" in prompt

    def test_includes_safety_rules(
        self,
        waypoint: Waypoint,
        checklist: Checklist,
    ) -> None:
        """Prompt includes safety rules."""
        prompt = build_execution_prompt(waypoint, "spec", Path("/project"), checklist)
        assert "STAY IN THE PROJECT" in prompt
        assert "NEVER" in prompt

    def test_includes_checklist(
        self,
        waypoint: Waypoint,
        checklist: Checklist,
    ) -> None:
        """Prompt includes checklist items."""
        prompt = build_execution_prompt(waypoint, "spec", Path("/project"), checklist)
        assert "Code passes linting" in prompt
        assert "All tests pass" in prompt

    def test_includes_completion_marker(
        self,
        waypoint: Waypoint,
        checklist: Checklist,
    ) -> None:
        """Prompt includes completion marker."""
        prompt = build_execution_prompt(waypoint, "spec", Path("/project"), checklist)
        assert "<waypoint-complete>WP-1</waypoint-complete>" in prompt

    def test_includes_criterion_marker_format(
        self,
        waypoint: Waypoint,
        checklist: Checklist,
    ) -> None:
        """Prompt explains criterion marker format."""
        prompt = build_execution_prompt(waypoint, "spec", Path("/project"), checklist)
        # Check for new nested criterion format
        assert "<acceptance-criterion>" in prompt
        assert "<index>N</index>" in prompt
        assert "<status>verified</status>" in prompt
        assert "<text>" in prompt
        assert "<evidence>" in prompt


class TestWaypointExecutor:
    """Tests for WaypointExecutor class."""

    @pytest.fixture
    def mock_project(self, tmp_path: Path) -> MagicMock:
        """Create mock project."""
        project = MagicMock()
        project.get_path.return_value = tmp_path
        project.slug = "test-project"
        return project

    @pytest.fixture
    def waypoint(self) -> Waypoint:
        """Create test waypoint."""
        return Waypoint(
            id="WP-1",
            title="Test waypoint",
            objective="Test objective for the waypoint",
            acceptance_criteria=["Criterion 1", "Criterion 2"],
        )

    def test_executor_init(self, mock_project: MagicMock, waypoint: Waypoint) -> None:
        """Executor initializes correctly."""
        executor = WaypointExecutor(
            project=mock_project,
            waypoint=waypoint,
            spec="Product spec",
        )
        assert executor.project == mock_project
        assert executor.waypoint == waypoint
        assert executor.spec == "Product spec"
        assert executor.max_iterations == 10
        assert executor._cancelled is False

    def test_cancel(self, mock_project: MagicMock, waypoint: Waypoint) -> None:
        """Cancel sets _cancelled flag."""
        executor = WaypointExecutor(
            project=mock_project,
            waypoint=waypoint,
            spec="spec",
        )
        assert executor._cancelled is False
        executor.cancel()
        assert executor._cancelled is True

    def test_needs_intervention_detects_cannot_proceed(
        self, mock_project: MagicMock, waypoint: Waypoint
    ) -> None:
        """_needs_intervention detects 'cannot proceed'."""
        executor = WaypointExecutor(mock_project, waypoint, "spec")
        assert executor._needs_intervention("I cannot proceed with this task") is True

    def test_needs_intervention_detects_need_human_help(
        self, mock_project: MagicMock, waypoint: Waypoint
    ) -> None:
        """_needs_intervention detects 'need human help'."""
        executor = WaypointExecutor(mock_project, waypoint, "spec")
        assert executor._needs_intervention("I need human help to resolve this") is True

    def test_needs_intervention_detects_blocked_by(
        self, mock_project: MagicMock, waypoint: Waypoint
    ) -> None:
        """_needs_intervention detects 'blocked by'."""
        executor = WaypointExecutor(mock_project, waypoint, "spec")
        assert (
            executor._needs_intervention("This is blocked by missing dependency")
            is True
        )

    def test_needs_intervention_detects_unable_to_complete(
        self, mock_project: MagicMock, waypoint: Waypoint
    ) -> None:
        """_needs_intervention detects 'unable to complete'."""
        executor = WaypointExecutor(mock_project, waypoint, "spec")
        assert executor._needs_intervention("I am unable to complete this") is True

    def test_needs_intervention_detects_requires_manual(
        self, mock_project: MagicMock, waypoint: Waypoint
    ) -> None:
        """_needs_intervention detects 'requires manual'."""
        executor = WaypointExecutor(mock_project, waypoint, "spec")
        assert (
            executor._needs_intervention("This requires manual configuration") is True
        )

    def test_needs_intervention_case_insensitive(
        self, mock_project: MagicMock, waypoint: Waypoint
    ) -> None:
        """_needs_intervention is case insensitive."""
        executor = WaypointExecutor(mock_project, waypoint, "spec")
        assert executor._needs_intervention("I CANNOT PROCEED") is True
        assert executor._needs_intervention("NEED HUMAN HELP") is True

    def test_needs_intervention_normal_output(
        self, mock_project: MagicMock, waypoint: Waypoint
    ) -> None:
        """_needs_intervention returns False for normal output."""
        executor = WaypointExecutor(mock_project, waypoint, "spec")
        assert executor._needs_intervention("Writing test file...") is False
        assert executor._needs_intervention("All tests pass!") is False
        assert executor._needs_intervention("Implementation complete") is False

    def test_extract_intervention_reason(
        self, mock_project: MagicMock, waypoint: Waypoint
    ) -> None:
        """_extract_intervention_reason extracts context around marker."""
        executor = WaypointExecutor(mock_project, waypoint, "spec")
        output = (
            "Some context. I cannot proceed because the API key is missing. More text."
        )
        reason = executor._extract_intervention_reason(output)
        assert "cannot proceed" in reason.lower()
        assert "API key" in reason or "api key" in reason.lower()

    def test_extract_intervention_reason_default(
        self, mock_project: MagicMock, waypoint: Waypoint
    ) -> None:
        """_extract_intervention_reason returns default message."""
        executor = WaypointExecutor(mock_project, waypoint, "spec")
        reason = executor._extract_intervention_reason("No markers here")
        assert "Agent requested human intervention" in reason

    def test_get_system_prompt(
        self, mock_project: MagicMock, waypoint: Waypoint
    ) -> None:
        """_get_system_prompt includes project path."""
        executor = WaypointExecutor(mock_project, waypoint, "spec")
        prompt = executor._get_system_prompt()
        assert str(mock_project.get_path.return_value) in prompt
        assert "NEVER" in prompt
        assert "working directory" in prompt.lower()

    def test_build_iteration_kickoff_prompt(
        self, mock_project: MagicMock, waypoint: Waypoint
    ) -> None:
        """Kickoff prompt includes explicit reason and strict completion rule."""
        executor = WaypointExecutor(mock_project, waypoint, "spec")
        prompt = executor._build_iteration_kickoff_prompt(
            reason_code="protocol_violation",
            reason_detail="missing completion marker",
            completion_marker="<waypoint-complete>WP-1</waypoint-complete>",
            captured_criteria={},
        )
        assert "Reason: protocol_violation" in prompt
        assert "missing completion marker" in prompt
        assert "<waypoint-complete>WP-1</waypoint-complete>" in prompt
        assert "Do not use aliases" in prompt

    def test_refresh_project_memory_builds_waypoint_context(
        self, mock_project: MagicMock, waypoint: Waypoint, tmp_path: Path
    ) -> None:
        """Project memory refresh should include prior waypoint memory context."""
        mock_project.get_path.return_value = tmp_path
        dependency_record = WaypointMemoryRecord(
            schema_version="v1",
            saved_at_utc="2026-02-07T02:00:00+00:00",
            waypoint_id="WP-000",
            title="Bootstrap stack",
            objective="Initialize project",
            dependencies=(),
            result="success",
            iterations_used=1,
            max_iterations=10,
            protocol_derailments=(),
            error_summary=None,
            changed_files=("src/main.py",),
            approx_tokens_changed=120,
            validation_commands=("pytest -v",),
            useful_commands=("pytest -v",),
            verified_criteria=(0,),
        )
        save_waypoint_memory(tmp_path, dependency_record)

        waypoint.dependencies = ["WP-000"]
        executor = WaypointExecutor(mock_project, waypoint, "spec")
        executor._refresh_project_memory(tmp_path)

        assert executor._waypoint_memory_context is not None
        assert "WP-000 (dependency" in executor._waypoint_memory_context
        assert executor._waypoint_memory_ids == ("WP-000",)

    def test_detect_protocol_issues_claimed_complete_without_marker(
        self, mock_project: MagicMock, waypoint: Waypoint
    ) -> None:
        """Alias completion text should be treated as protocol violation."""
        executor = WaypointExecutor(mock_project, waypoint, "spec")
        issues = executor._detect_protocol_issues(
            iteration_output="Implementation is complete. **WP-1 COMPLETE**",
            completion_marker="<waypoint-complete>WP-1</waypoint-complete>",
            stage_reports_logged=0,
            scope_drift_detected=True,
        )
        assert "claimed completion without exact completion marker" in issues
        assert "missing structured stage report" in issues
        assert "attempted tool access to blocked project areas" in issues


class TestExecutionResult:
    """Tests for ExecutionResult enum."""

    def test_success_value(self) -> None:
        assert ExecutionResult.SUCCESS.value == "success"

    def test_failed_value(self) -> None:
        assert ExecutionResult.FAILED.value == "failed"

    def test_max_iterations_value(self) -> None:
        assert ExecutionResult.MAX_ITERATIONS.value == "max_iterations"

    def test_cancelled_value(self) -> None:
        assert ExecutionResult.CANCELLED.value == "cancelled"

    def test_intervention_needed_value(self) -> None:
        assert ExecutionResult.INTERVENTION_NEEDED.value == "intervention_needed"


class _TestProject:
    """Minimal project double for execute-loop tests."""

    def __init__(self, root: Path) -> None:
        self._root = root
        self.slug = "test-project"

    def get_path(self) -> Path:
        return self._root

    def get_sessions_path(self) -> Path:
        return self._root / "sessions"


class _StubFinalizer:
    """Finalize stub used by execute-loop tests."""

    async def finalize(self, **_: object) -> bool:
        return True


class _RetryFinalizer:
    """Finalize stub that fails once, then succeeds."""

    def __init__(self) -> None:
        self.calls = 0

    async def finalize(self, **_: object) -> bool:
        self.calls += 1
        return self.calls > 1

    def last_failure_summary(self, max_chars: int = 1000) -> str:
        del max_chars
        return (
            "Host validation failed. cargo clippy -- -D warnings exited 101: "
            "unused assignment in validator.rs:90"
        )


class _AlwaysInvalidFinalizer:
    """Finalize stub that always fails with host validation diagnostics."""

    async def finalize(self, **_: object) -> bool:
        return False

    def last_failure_summary(self, max_chars: int = 1000) -> str:
        del max_chars
        return "Host validation failed. cargo clippy -- -D warnings exited 101."


@pytest.mark.anyio
async def test_execute_resumes_session_and_uses_protocol_nudge(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Iteration 2 should resume prior session and include protocol-violation reason."""
    calls: list[dict[str, object]] = []

    async def fake_agent_query(**kwargs: object):
        calls.append(kwargs)
        if len(calls) == 1:
            yield StreamChunk(text="Implementation is complete.\n**WP-1 COMPLETE**")
            yield StreamComplete(
                full_text="Implementation is complete.\n**WP-1 COMPLETE**",
                session_id="session-abc",
            )
            return
        yield StreamChunk(text="<waypoint-complete>WP-1</waypoint-complete>")
        yield StreamComplete(
            full_text="<waypoint-complete>WP-1</waypoint-complete>",
            session_id="session-abc",
        )

    monkeypatch.setattr("waypoints.fly.executor.agent_query", fake_agent_query)
    monkeypatch.setattr(
        WaypointExecutor,
        "_make_finalizer",
        lambda self: _StubFinalizer(),
    )
    monkeypatch.setattr(
        WaypointExecutor,
        "_resolve_validation_commands",
        lambda self, project_path, checklist: [],
    )

    project = _TestProject(tmp_path)
    waypoint = Waypoint(
        id="WP-1",
        title="Protocol recovery",
        objective="Validate session continuity",
        acceptance_criteria=["Criterion 1"],
    )
    executor = WaypointExecutor(project=project, waypoint=waypoint, spec="spec")

    result = await executor.execute()

    assert result == ExecutionResult.SUCCESS
    assert len(calls) == 2
    assert calls[0]["resume_session_id"] is None
    assert calls[1]["resume_session_id"] == "session-abc"
    assert isinstance(calls[1]["prompt"], str)
    assert "Reason: protocol_violation" in calls[1]["prompt"]
    assert "<waypoint-complete>WP-1</waypoint-complete>" in calls[1]["prompt"]


@pytest.mark.anyio
async def test_execute_surfaces_failed_bash_command_in_intervention(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Intervention summary should include the exact failed bash command."""

    async def fake_agent_query(**kwargs: object):
        del kwargs
        yield StreamToolUse(
            tool_name="Bash",
            tool_input={
                "command": (
                    "/Users/kulesh/.cargo/bin/cargo run -- "
                    "/Users/kulesh/dev/flight-test/projects/"
                    "actuator 2>&1 | head -30 &\n"
                    "sleep 2\n"
                    'echo "Started app"\n'
                    "sleep 1\n"
                    'pkill -f "canopy.*actuator" 2>/dev/null || true'
                )
            },
            tool_output=(
                "error: could not find `Cargo.toml` in "
                "`/Users/kulesh/dev/flight-test/projects/actuator` or any parent "
                "directory\nStarted app\n"
            ),
        )
        raise RuntimeError(
            "Command failed with exit code -15 (exit code: -15)\n"
            "Error output: Check stderr output for details"
        )

    monkeypatch.setattr("waypoints.fly.executor.agent_query", fake_agent_query)
    monkeypatch.setattr(
        WaypointExecutor,
        "_resolve_validation_commands",
        lambda self, project_path, checklist: [],
    )

    project = _TestProject(tmp_path)
    waypoint = Waypoint(
        id="WP-1",
        title="Intervention diagnostics",
        objective="Show failed command details in intervention",
        acceptance_criteria=["Criterion 1"],
    )
    executor = WaypointExecutor(project=project, waypoint=waypoint, spec="spec")

    with pytest.raises(InterventionNeededError) as exc_info:
        await executor.execute()

    intervention = exc_info.value.intervention
    assert "Failed command:" in intervention.error_summary
    assert "/Users/kulesh/.cargo/bin/cargo run --" in intervention.error_summary
    assert 'pkill -f "canopy.*actuator"' in intervention.error_summary
    assert "could not find `Cargo.toml`" in intervention.error_summary
    assert intervention.context["last_tool_name"] == "Bash"


@pytest.mark.anyio
async def test_execute_classifies_rate_limit_intervention(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Rate-limit failures should map to RATE_LIMITED interventions."""

    async def fake_agent_query(**kwargs: object):
        del kwargs
        if False:  # pragma: no cover - force async-generator shape
            yield StreamChunk(text="")
        raise RuntimeError("429 Too Many Requests: rate limit exceeded")

    monkeypatch.setattr("waypoints.fly.executor.agent_query", fake_agent_query)
    monkeypatch.setattr(
        WaypointExecutor,
        "_resolve_validation_commands",
        lambda self, project_path, checklist: [],
    )

    project = _TestProject(tmp_path)
    waypoint = Waypoint(
        id="WP-1",
        title="Rate limit handling",
        objective="Classify provider rate limits",
        acceptance_criteria=["Criterion 1"],
    )
    executor = WaypointExecutor(project=project, waypoint=waypoint, spec="spec")

    with pytest.raises(InterventionNeededError) as exc_info:
        await executor.execute()

    intervention = exc_info.value.intervention
    assert intervention.type == InterventionType.RATE_LIMITED
    assert intervention.context["api_error_type"] == "rate_limited"
    assert "rate limit" in intervention.error_summary.lower()


@pytest.mark.anyio
async def test_execute_classifies_budget_intervention_with_context(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Budget errors should map to budget interventions with budget context."""

    async def fake_agent_query(**kwargs: object):
        del kwargs
        if False:  # pragma: no cover - force async-generator shape
            yield StreamChunk(text="")
        raise BudgetExceededError("cost", current_value=11.0, limit_value=10.0)

    monkeypatch.setattr("waypoints.fly.executor.agent_query", fake_agent_query)
    monkeypatch.setattr(
        WaypointExecutor,
        "_resolve_validation_commands",
        lambda self, project_path, checklist: [],
    )

    from waypoints.config.settings import settings

    settings.llm_budget_usd = 25.0

    project = _TestProject(tmp_path)
    waypoint = Waypoint(
        id="WP-1",
        title="Budget handling",
        objective="Classify budget exhaustion and preserve context",
        acceptance_criteria=["Criterion 1"],
    )
    metrics_stub = SimpleNamespace(total_cost=11.5)
    executor = WaypointExecutor(
        project=project,
        waypoint=waypoint,
        spec="spec",
        metrics_collector=metrics_stub,  # type: ignore[arg-type]
    )

    with pytest.raises(InterventionNeededError) as exc_info:
        await executor.execute()

    intervention = exc_info.value.intervention
    assert intervention.type == InterventionType.BUDGET_EXCEEDED
    assert intervention.context["api_error_type"] == "budget_exceeded"
    assert intervention.context["configured_budget_usd"] == 25.0
    assert intervention.context["current_cost_usd"] == 11.5
    assert "configured budget $10.00 reached" in intervention.error_summary.lower()


@pytest.mark.anyio
async def test_execute_classifies_api_unavailable_intervention(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Provider-unavailable failures should map to API_UNAVAILABLE."""

    async def fake_agent_query(**kwargs: object):
        del kwargs
        if False:  # pragma: no cover - force async-generator shape
            yield StreamChunk(text="")
        raise RuntimeError("503 service unavailable: provider overloaded")

    monkeypatch.setattr("waypoints.fly.executor.agent_query", fake_agent_query)
    monkeypatch.setattr(
        WaypointExecutor,
        "_resolve_validation_commands",
        lambda self, project_path, checklist: [],
    )

    project = _TestProject(tmp_path)
    waypoint = Waypoint(
        id="WP-1",
        title="Provider unavailable handling",
        objective="Classify transient provider outages",
        acceptance_criteria=["Criterion 1"],
    )
    executor = WaypointExecutor(project=project, waypoint=waypoint, spec="spec")

    with pytest.raises(InterventionNeededError) as exc_info:
        await executor.execute()

    intervention = exc_info.value.intervention
    assert intervention.type == InterventionType.API_UNAVAILABLE
    assert intervention.context["api_error_type"] == "api_unavailable"
    assert "temporarily unavailable" in intervention.error_summary.lower()


@pytest.mark.anyio
async def test_execute_reports_non_file_tool_use_progress(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Unknown tool-use events should still surface in progress updates."""
    progress_updates: list[ExecutionContext] = []

    async def fake_agent_query(**kwargs: object):
        del kwargs
        yield StreamToolUse(
            tool_name="TodoWrite",
            tool_input={"todos": [{"content": "Check logs", "status": "in_progress"}]},
        )
        yield StreamChunk(text="<waypoint-complete>WP-1</waypoint-complete>")
        yield StreamComplete(
            full_text="<waypoint-complete>WP-1</waypoint-complete>",
            session_id="session-1",
        )

    monkeypatch.setattr("waypoints.fly.executor.agent_query", fake_agent_query)
    monkeypatch.setattr(
        WaypointExecutor,
        "_make_finalizer",
        lambda self: _StubFinalizer(),
    )
    monkeypatch.setattr(
        WaypointExecutor,
        "_resolve_validation_commands",
        lambda self, project_path, checklist: [],
    )

    project = _TestProject(tmp_path)
    waypoint = Waypoint(
        id="WP-1",
        title="Progress visibility",
        objective="Surface non-file tool use in live logs",
        acceptance_criteria=["Criterion 1"],
    )
    executor = WaypointExecutor(
        project=project,
        waypoint=waypoint,
        spec="spec",
        on_progress=progress_updates.append,
    )

    result = await executor.execute()

    assert result == ExecutionResult.SUCCESS
    tool_updates = [ctx for ctx in progress_updates if ctx.step == "tool_use"]
    assert len(tool_updates) == 1
    assert "TodoWrite" in tool_updates[0].output
    assert "todos" in tool_updates[0].output
    assert tool_updates[0].file_operations == []


@pytest.mark.anyio
async def test_execute_retries_when_receipt_is_invalid(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Invalid receipt should trigger another iteration, not a false success."""
    calls: list[dict[str, object]] = []

    async def fake_agent_query(**kwargs: object):
        calls.append(kwargs)
        yield StreamChunk(text="<waypoint-complete>WP-1</waypoint-complete>")
        yield StreamComplete(
            full_text="<waypoint-complete>WP-1</waypoint-complete>",
            session_id="session-abc",
        )

    retry_finalizer = _RetryFinalizer()
    monkeypatch.setattr("waypoints.fly.executor.agent_query", fake_agent_query)
    monkeypatch.setattr(
        WaypointExecutor,
        "_make_finalizer",
        lambda self: retry_finalizer,
    )
    monkeypatch.setattr(
        WaypointExecutor,
        "_resolve_validation_commands",
        lambda self, project_path, checklist: [],
    )

    project = _TestProject(tmp_path)
    waypoint = Waypoint(
        id="WP-1",
        title="Receipt retry",
        objective="Retry on host validation failure",
        acceptance_criteria=["Criterion 1"],
    )
    executor = WaypointExecutor(project=project, waypoint=waypoint, spec="spec")

    result = await executor.execute()

    assert result == ExecutionResult.SUCCESS
    assert retry_finalizer.calls == 2
    assert len(calls) == 2
    assert calls[1]["resume_session_id"] == "session-abc"
    assert isinstance(calls[1]["prompt"], str)
    assert "Reason: host_validation_failed" in calls[1]["prompt"]
    assert "cargo clippy -- -D warnings exited 101" in calls[1]["prompt"]


@pytest.mark.anyio
async def test_execute_invalid_receipt_does_not_return_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Persistent receipt failure should escalate instead of returning success."""

    async def fake_agent_query(**kwargs: object):
        del kwargs
        yield StreamChunk(text="<waypoint-complete>WP-1</waypoint-complete>")
        yield StreamComplete(
            full_text="<waypoint-complete>WP-1</waypoint-complete>",
            session_id="session-abc",
        )

    monkeypatch.setattr("waypoints.fly.executor.agent_query", fake_agent_query)
    monkeypatch.setattr(
        WaypointExecutor,
        "_make_finalizer",
        lambda self: _AlwaysInvalidFinalizer(),
    )
    monkeypatch.setattr(
        WaypointExecutor,
        "_resolve_validation_commands",
        lambda self, project_path, checklist: [],
    )

    project = _TestProject(tmp_path)
    waypoint = Waypoint(
        id="WP-1",
        title="Persistent invalid receipt",
        objective="Never return success on invalid receipt",
        acceptance_criteria=["Criterion 1"],
    )
    executor = WaypointExecutor(
        project=project,
        waypoint=waypoint,
        spec="spec",
        max_iterations=2,
    )

    with pytest.raises(InterventionNeededError):
        await executor.execute()


@pytest.mark.anyio
async def test_execute_logs_spec_context_usage_and_staleness(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Iteration start log should capture spec context usage metadata."""

    async def fake_agent_query(**kwargs: object):
        del kwargs
        yield StreamChunk(text="<waypoint-complete>WP-1</waypoint-complete>")
        yield StreamComplete(
            full_text="<waypoint-complete>WP-1</waypoint-complete>",
            session_id="session-1",
        )

    monkeypatch.setattr("waypoints.fly.executor.agent_query", fake_agent_query)
    monkeypatch.setattr(
        WaypointExecutor,
        "_make_finalizer",
        lambda self: _StubFinalizer(),
    )
    monkeypatch.setattr(
        WaypointExecutor,
        "_resolve_validation_commands",
        lambda self, project_path, checklist: [],
    )

    project = _TestProject(tmp_path)
    spec = "# Product Spec\n\n## Current Scope\nNew requirements."
    waypoint_summary = "Implement current scope behavior with matching tests."
    waypoint = Waypoint(
        id="WP-1",
        title="Context logging",
        objective="Capture spec context metadata",
        acceptance_criteria=["Criterion 1"],
        spec_context_summary=waypoint_summary,
        spec_section_refs=["Current Scope", "Validation"],
        spec_context_hash=compute_spec_hash("# Product Spec\n\n## Current Scope\nOld"),
    )
    executor = WaypointExecutor(project=project, waypoint=waypoint, spec=spec)

    result = await executor.execute()

    assert result == ExecutionResult.SUCCESS

    logs = sorted((tmp_path / "sessions" / "fly").glob("*.jsonl"))
    assert logs
    entries = [json.loads(line) for line in logs[-1].read_text().splitlines()]
    iteration_start = next(
        entry for entry in entries if entry["type"] == "iteration_start"
    )

    assert iteration_start["spec_context_summary_chars"] == len(waypoint_summary)
    assert iteration_start["spec_section_ref_count"] == 2
    assert iteration_start["spec_context_hash"] == waypoint.spec_context_hash
    assert iteration_start["current_spec_hash"] == compute_spec_hash(spec)
    assert iteration_start["spec_context_stale"] is True
    assert iteration_start["full_spec_pointer"] == "docs/product-spec.md"


class TestExecutionStep:
    """Tests for ExecutionStep dataclass."""

    def test_create_step(self) -> None:
        """Create execution step."""
        step = ExecutionStep(iteration=1, action="execute", output="Running tests...")
        assert step.iteration == 1
        assert step.action == "execute"
        assert step.output == "Running tests..."
        assert step.timestamp is not None


class TestFileOperation:
    """Tests for FileOperation dataclass."""

    def test_create_operation(self) -> None:
        """Create file operation."""
        op = FileOperation(tool_name="Edit", file_path="/src/main.py", line_number=42)
        assert op.tool_name == "Edit"
        assert op.file_path == "/src/main.py"
        assert op.line_number == 42

    def test_optional_line_number(self) -> None:
        """Line number is optional."""
        op = FileOperation(tool_name="Read", file_path="/README.md")
        assert op.line_number is None


class TestExecutionContext:
    """Tests for ExecutionContext dataclass."""

    def test_create_context(self) -> None:
        """Create execution context."""
        waypoint = Waypoint(id="WP-1", title="Test", objective="Test objective")
        ctx = ExecutionContext(
            waypoint=waypoint,
            iteration=3,
            total_iterations=10,
            step="executing",
            output="Running...",
        )
        assert ctx.waypoint == waypoint
        assert ctx.iteration == 3
        assert ctx.total_iterations == 10
        assert ctx.step == "executing"
        assert ctx.output == "Running..."
        assert ctx.criteria_completed == set()
        assert ctx.file_operations == []

    def test_context_with_criteria(self) -> None:
        """Context with completed criteria."""
        waypoint = Waypoint(id="WP-1", title="Test", objective="Test")
        ctx = ExecutionContext(
            waypoint=waypoint,
            iteration=5,
            total_iterations=10,
            step="complete",
            output="Done",
            criteria_completed={0, 2, 3},
        )
        assert ctx.criteria_completed == {0, 2, 3}

    def test_context_with_file_operations(self) -> None:
        """Context with file operations."""
        waypoint = Waypoint(id="WP-1", title="Test", objective="Test")
        ops = [
            FileOperation(tool_name="Read", file_path="/src/main.py"),
            FileOperation(tool_name="Edit", file_path="/src/main.py"),
        ]
        ctx = ExecutionContext(
            waypoint=waypoint,
            iteration=2,
            total_iterations=10,
            step="tool_use",
            output="Editing file",
            file_operations=ops,
        )
        assert len(ctx.file_operations) == 2


class DummyLogWriter:
    """Lightweight log writer stub for finalize tests."""

    def __init__(self) -> None:
        self.tool_calls: list[tuple[str, dict[str, object], str]] = []
        self.validated: tuple[str, bool, str] | None = None

    def log_finalize_start(self) -> None:
        """Log start placeholder."""

    def log_finalize_tool_call(
        self, name: str, tool_input: dict[str, object], output: str
    ) -> None:
        """Record finalize tool calls."""
        self.tool_calls.append((name, tool_input, output))

    def log_finalize_end(self) -> None:
        """Log end placeholder."""

    def log_receipt_validated(
        self, receipt_path: str, valid: bool, reason: str
    ) -> None:
        """Record validation outcome."""
        self.validated = (receipt_path, valid, reason)

    def log_finalize_output(self, output: str) -> None:
        """Log finalize output placeholder."""
        self.output = output  # type: ignore[attr-defined]

    def log_error(self, iteration: int, message: str) -> None:
        """Log error placeholder."""
        self.error = (iteration, message)  # type: ignore[attr-defined]


@pytest.mark.anyio
async def test_finalize_runs_host_validation_commands(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Host-run validation output is captured into the receipt."""

    async def fake_agent_query(**_: object):
        yield StreamChunk(
            text='<receipt-verdict status="valid">looks good</receipt-verdict>'
        )

    monkeypatch.setattr("waypoints.fly.receipt_finalizer.agent_query", fake_agent_query)

    project = SimpleNamespace(get_path=lambda: tmp_path)
    waypoint = Waypoint(
        id="WP-123",
        title="Test waypoint",
        objective="Validate receipt capture",
        acceptance_criteria=[],
    )
    executor = WaypointExecutor(project=project, waypoint=waypoint, spec="spec")
    executor._log_writer = DummyLogWriter()

    command = "python -c \"print('host evidence')\""
    validation_commands = [
        ValidationCommand(name="tests", command=command, category="test")
    ]
    tool_evidence = CapturedEvidence(
        command=command,
        exit_code=0,
        stdout="host evidence",
        stderr="",
        captured_at=datetime.now(),
    )

    finalizer = executor._make_finalizer()
    result = await finalizer.finalize(
        project_path=tmp_path,
        captured_criteria={},
        validation_commands=validation_commands,
        reported_validation_commands=[],
        tool_validation_categories={"tests": tool_evidence},
        max_iterations=executor.max_iterations,
    )

    assert result is True
    receipts = list((tmp_path / "receipts").glob("*.json"))
    assert len(receipts) == 1

    receipt = ChecklistReceipt.load(receipts[0])
    outputs = [item.stdout for item in receipt.checklist]
    assert any("host evidence" in out for out in outputs)


@pytest.mark.anyio
async def test_finalize_host_validation_records_soft_evidence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Host validation should also record tool evidence for soft validation."""

    async def fake_agent_query(**_: object):
        yield StreamChunk(
            text='<receipt-verdict status="valid">looks good</receipt-verdict>'
        )

    monkeypatch.setattr("waypoints.fly.receipt_finalizer.agent_query", fake_agent_query)

    project = SimpleNamespace(get_path=lambda: tmp_path)
    waypoint = Waypoint(
        id="WP-124",
        title="Soft evidence",
        objective="Record tool output",
        acceptance_criteria=[],
    )
    executor = WaypointExecutor(project=project, waypoint=waypoint, spec="spec")
    executor._log_writer = DummyLogWriter()

    command = "python -c \"print('host evidence')\""
    validation_commands = [
        ValidationCommand(name="tests", command=command, category="test")
    ]
    tool_evidence = CapturedEvidence(
        command="cargo clippy -- -D warnings",
        exit_code=0,
        stdout="tool linting output",
        stderr="",
        captured_at=datetime.now(),
    )

    finalizer = executor._make_finalizer()
    result = await finalizer.finalize(
        project_path=tmp_path,
        captured_criteria={},
        validation_commands=validation_commands,
        reported_validation_commands=[],
        tool_validation_categories={"linting": tool_evidence},
        max_iterations=executor.max_iterations,
    )

    assert result is True
    receipts = list((tmp_path / "receipts").glob("*.json"))
    assert len(receipts) == 1

    receipt = ChecklistReceipt.load(receipts[0])
    assert receipt.soft_checklist
    item = next(item for item in receipt.soft_checklist if item.item == "linting")
    assert "tool linting output" in item.stdout


@pytest.mark.anyio
async def test_finalize_falls_back_to_model_commands(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Model-reported validation commands are executed on host when needed."""

    async def fake_agent_query(**_: object):
        yield StreamChunk(
            text='<receipt-verdict status="valid">looks good</receipt-verdict>'
        )

    monkeypatch.setattr("waypoints.fly.receipt_finalizer.agent_query", fake_agent_query)

    project = SimpleNamespace(get_path=lambda: tmp_path)
    waypoint = Waypoint(
        id="WP-456",
        title="Test waypoint",
        objective="Fallback validation",
        acceptance_criteria=[],
    )
    executor = WaypointExecutor(project=project, waypoint=waypoint, spec="spec")
    executor._log_writer = DummyLogWriter()

    reported_commands = ["python -c \"print('fallback evidence')\""]
    tool_evidence = CapturedEvidence(
        command=reported_commands[0],
        exit_code=0,
        stdout="fallback evidence",
        stderr="",
        captured_at=datetime.now(),
    )

    finalizer = executor._make_finalizer()
    result = await finalizer.finalize(
        project_path=tmp_path,
        captured_criteria={},
        validation_commands=[],
        reported_validation_commands=reported_commands,
        tool_validation_evidence={reported_commands[0]: tool_evidence},
        max_iterations=executor.max_iterations,
    )

    assert result is True
    receipts = list((tmp_path / "receipts").glob("*.json"))
    assert len(receipts) == 1

    receipt = ChecklistReceipt.load(receipts[0])
    outputs = [item.stdout for item in receipt.checklist]
    assert any("fallback evidence" in out for out in outputs)


@pytest.mark.anyio
async def test_finalize_soft_validation_uses_tool_evidence(
    tmp_path: Path,
) -> None:
    """Soft validation should reuse tool evidence when available."""
    project = SimpleNamespace(get_path=lambda: tmp_path)
    waypoint = Waypoint(
        id="WP-789",
        title="Soft validation",
        objective="Capture tool evidence",
        acceptance_criteria=[],
    )
    executor = WaypointExecutor(project=project, waypoint=waypoint, spec="spec")
    executor._log_writer = DummyLogWriter()

    command = "python -c \"print('tool evidence')\""
    validation_commands = [
        ValidationCommand(name="tests", command=command, category="test")
    ]
    tool_evidence = {
        command: CapturedEvidence(
            command=command,
            exit_code=0,
            stdout="tool evidence",
            stderr="",
            captured_at=datetime.now(),
        )
    }

    finalizer = executor._make_finalizer()
    result = await finalizer.finalize(
        project_path=tmp_path,
        captured_criteria={},
        validation_commands=validation_commands,
        reported_validation_commands=[],
        tool_validation_evidence=tool_evidence,
        host_validations_enabled=False,
        max_iterations=executor.max_iterations,
    )

    assert result is True
    receipts = list((tmp_path / "receipts").glob("*.json"))
    assert len(receipts) == 1

    receipt = ChecklistReceipt.load(receipts[0])
    item = next(item for item in receipt.checklist if item.item == "tests")
    assert item.status == "passed"
    assert "tool evidence" in item.stdout


@pytest.mark.anyio
async def test_finalize_requires_soft_evidence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Receipt should be invalid when soft evidence is missing."""

    async def fake_agent_query(**_: object):
        yield StreamChunk(
            text='<receipt-verdict status="valid">looks good</receipt-verdict>'
        )

    monkeypatch.setattr("waypoints.fly.receipt_finalizer.agent_query", fake_agent_query)

    project = SimpleNamespace(get_path=lambda: tmp_path)
    waypoint = Waypoint(
        id="WP-790",
        title="Soft missing",
        objective="Require soft validation",
        acceptance_criteria=[],
    )
    executor = WaypointExecutor(project=project, waypoint=waypoint, spec="spec")
    executor._log_writer = DummyLogWriter()

    command = "python -c \"print('host evidence')\""
    validation_commands = [
        ValidationCommand(name="tests", command=command, category="test")
    ]

    finalizer = executor._make_finalizer()
    result = await finalizer.finalize(
        project_path=tmp_path,
        captured_criteria={},
        validation_commands=validation_commands,
        reported_validation_commands=[],
        max_iterations=executor.max_iterations,
    )

    assert result is False
    assert "Soft validation evidence missing" in finalizer.last_failure_summary()


@pytest.mark.anyio
async def test_finalize_exposes_host_failure_details(tmp_path: Path) -> None:
    """Finalizer should surface command/exit/stderr details for retry prompts."""
    project = SimpleNamespace(get_path=lambda: tmp_path)
    waypoint = Waypoint(
        id="WP-791",
        title="Host failure details",
        objective="Expose failed validation diagnostics",
        acceptance_criteria=[],
    )
    executor = WaypointExecutor(project=project, waypoint=waypoint, spec="spec")
    executor._log_writer = DummyLogWriter()

    command = (
        'python -c "import sys; '
        "print('unused assignment in validator.rs:90', file=sys.stderr); "
        'sys.exit(101)"'
    )
    validation_commands = [
        ValidationCommand(name="linting", command=command, category="lint")
    ]

    finalizer = executor._make_finalizer()
    result = await finalizer.finalize(
        project_path=tmp_path,
        captured_criteria={},
        validation_commands=validation_commands,
        reported_validation_commands=[],
        max_iterations=executor.max_iterations,
    )

    assert result is False
    summary = finalizer.last_failure_summary()
    assert "Host validation failed" in summary
    assert "exited 101" in summary
    assert "unused assignment in validator.rs:90" in summary
