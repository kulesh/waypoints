"""Unit tests for WaypointExecutor."""

from pathlib import Path
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
from waypoints.git.config import Checklist
from waypoints.llm.prompts import build_execution_prompt
from waypoints.models.waypoint import Waypoint


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
            "<acceptance-criterion><index>0</index><text>Missing status</text><evidence>E</evidence></acceptance-criterion>",
            # Missing index
            "<acceptance-criterion><status>verified</status><text>Missing index</text><evidence>E</evidence></acceptance-criterion>",
            # Missing evidence
            "<acceptance-criterion><index>0</index><status>verified</status><text>Missing evidence</text></acceptance-criterion>",
            # Non-numeric index
            "<acceptance-criterion><index>abc</index><status>verified</status><text>Non-numeric</text><evidence>E</evidence></acceptance-criterion>",
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

    def test_includes_spec(
        self,
        waypoint: Waypoint,
        checklist: Checklist,
    ) -> None:
        """Prompt includes product spec."""
        spec = "This is the product specification."
        prompt = build_execution_prompt(waypoint, spec, Path("/project"), checklist)
        assert "This is the product specification" in prompt

    def test_truncates_long_spec(
        self,
        waypoint: Waypoint,
        checklist: Checklist,
    ) -> None:
        """Long spec is truncated with ellipsis."""
        long_spec = "x" * 3000
        prompt = build_execution_prompt(
            waypoint, long_spec, Path("/project"), checklist
        )
        assert "..." in prompt
        # Should truncate to 2000 chars + ...
        assert long_spec[:2000] in prompt

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
