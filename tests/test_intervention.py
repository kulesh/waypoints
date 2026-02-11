"""Tests for the Intervention protocol."""

import pytest

from waypoints.fly.intervention import (
    SUGGESTED_ACTIONS,
    Intervention,
    InterventionAction,
    InterventionNeededError,
    InterventionResult,
    InterventionType,
)
from waypoints.models.waypoint import Waypoint, WaypointStatus
from waypoints.tui.screens.intervention import InterventionModal


@pytest.fixture
def sample_waypoint() -> Waypoint:
    """Create a sample waypoint for testing."""
    return Waypoint(
        id="WP-1",
        title="Test Waypoint",
        objective="Implement a test feature",
        acceptance_criteria=["Tests pass", "Code compiles"],
        status=WaypointStatus.IN_PROGRESS,
    )


class TestInterventionType:
    """Tests for InterventionType enum."""

    def test_all_types_have_suggested_action(self) -> None:
        """Every intervention type should have a suggested action."""
        for intervention_type in InterventionType:
            assert intervention_type in SUGGESTED_ACTIONS

    def test_suggested_actions_are_valid(self) -> None:
        """All suggested actions should be valid InterventionAction values."""
        for action in SUGGESTED_ACTIONS.values():
            assert isinstance(action, InterventionAction)


class TestInterventionAction:
    """Tests for InterventionAction enum."""

    def test_action_values(self) -> None:
        """Test that action values are as expected."""
        assert InterventionAction.WAIT.value == "wait"
        assert InterventionAction.RETRY.value == "retry"
        assert InterventionAction.SKIP.value == "skip"
        assert InterventionAction.EDIT.value == "edit"
        assert InterventionAction.ROLLBACK.value == "rollback"
        assert InterventionAction.ABORT.value == "abort"


class TestIntervention:
    """Tests for Intervention dataclass."""

    def test_create_intervention(self, sample_waypoint: Waypoint) -> None:
        """Test creating an Intervention."""
        intervention = Intervention(
            type=InterventionType.ITERATION_LIMIT,
            waypoint=sample_waypoint,
            iteration=10,
            max_iterations=10,
            error_summary="Max iterations reached",
        )

        assert intervention.type == InterventionType.ITERATION_LIMIT
        assert intervention.waypoint == sample_waypoint
        assert intervention.iteration == 10
        assert intervention.max_iterations == 10
        assert intervention.error_summary == "Max iterations reached"
        assert intervention.context == {}
        assert intervention.timestamp is not None

    def test_suggested_action_iteration_limit(self, sample_waypoint: Waypoint) -> None:
        """Iteration limit should suggest RETRY."""
        intervention = Intervention(
            type=InterventionType.ITERATION_LIMIT,
            waypoint=sample_waypoint,
            iteration=10,
            max_iterations=10,
            error_summary="Max iterations reached",
        )

        assert intervention.suggested_action == InterventionAction.RETRY

    def test_suggested_action_test_failure(self, sample_waypoint: Waypoint) -> None:
        """Test failure should suggest EDIT."""
        intervention = Intervention(
            type=InterventionType.TEST_FAILURE,
            waypoint=sample_waypoint,
            iteration=5,
            max_iterations=10,
            error_summary="Tests failing",
        )

        assert intervention.suggested_action == InterventionAction.EDIT

    def test_suggested_action_user_requested(self, sample_waypoint: Waypoint) -> None:
        """User requested should suggest ABORT."""
        intervention = Intervention(
            type=InterventionType.USER_REQUESTED,
            waypoint=sample_waypoint,
            iteration=3,
            max_iterations=10,
            error_summary="User requested intervention",
        )

        assert intervention.suggested_action == InterventionAction.ABORT

    def test_suggested_action_budget_exceeded(self, sample_waypoint: Waypoint) -> None:
        """Budget exceeded should suggest WAIT."""
        intervention = Intervention(
            type=InterventionType.BUDGET_EXCEEDED,
            waypoint=sample_waypoint,
            iteration=4,
            max_iterations=10,
            error_summary="Budget limit reached",
        )

        assert intervention.suggested_action == InterventionAction.WAIT

    def test_to_dict(self, sample_waypoint: Waypoint) -> None:
        """Test serialization to dict."""
        intervention = Intervention(
            type=InterventionType.ITERATION_LIMIT,
            waypoint=sample_waypoint,
            iteration=10,
            max_iterations=10,
            error_summary="Max iterations reached",
            context={"output": "some output"},
        )

        data = intervention.to_dict()

        assert data["type"] == "iteration_limit"
        assert data["waypoint_id"] == "WP-1"
        assert data["waypoint_title"] == "Test Waypoint"
        assert data["iteration"] == 10
        assert data["max_iterations"] == 10
        assert data["error_summary"] == "Max iterations reached"
        assert data["suggested_action"] == "retry"
        assert data["context"] == {"output": "some output"}
        assert "timestamp" in data


class TestInterventionResult:
    """Tests for InterventionResult dataclass."""

    def test_create_retry_result(self) -> None:
        """Test creating a retry result."""
        result = InterventionResult(
            action=InterventionAction.RETRY,
            additional_iterations=5,
        )

        assert result.action == InterventionAction.RETRY
        assert result.additional_iterations == 5
        assert result.modified_waypoint is None
        assert result.rollback_ref is None
        assert result.rollback_tag is None

    def test_create_wait_result(self) -> None:
        """Test creating a wait result."""
        result = InterventionResult(action=InterventionAction.WAIT)

        assert result.action == InterventionAction.WAIT
        assert result.modified_waypoint is None
        assert result.rollback_ref is None
        assert result.rollback_tag is None

    def test_create_skip_result(self) -> None:
        """Test creating a skip result."""
        result = InterventionResult(
            action=InterventionAction.SKIP,
        )

        assert result.action == InterventionAction.SKIP

    def test_create_edit_result(self, sample_waypoint: Waypoint) -> None:
        """Test creating an edit result."""
        result = InterventionResult(
            action=InterventionAction.EDIT,
            modified_waypoint=sample_waypoint,
        )

        assert result.action == InterventionAction.EDIT
        assert result.modified_waypoint == sample_waypoint

    def test_create_rollback_result(self) -> None:
        """Test creating a rollback result."""
        result = InterventionResult(
            action=InterventionAction.ROLLBACK,
            rollback_ref="project/WP-2",
        )

        assert result.action == InterventionAction.ROLLBACK
        assert result.rollback_ref == "project/WP-2"
        assert result.rollback_tag == "project/WP-2"

    def test_create_abort_result(self) -> None:
        """Test creating an abort result."""
        result = InterventionResult(
            action=InterventionAction.ABORT,
        )

        assert result.action == InterventionAction.ABORT

    def test_to_dict(self, sample_waypoint: Waypoint) -> None:
        """Test serialization to dict."""
        result = InterventionResult(
            action=InterventionAction.EDIT,
            modified_waypoint=sample_waypoint,
            additional_iterations=3,
        )

        data = result.to_dict()

        assert data["action"] == "edit"
        assert data["modified_waypoint_id"] == "WP-1"
        assert data["additional_iterations"] == 3
        assert data["rollback_ref"] is None
        assert data["rollback_tag"] is None


class TestInterventionNeededError:
    """Tests for InterventionNeededError exception."""

    def test_exception_message(self, sample_waypoint: Waypoint) -> None:
        """Test that exception has informative message."""
        intervention = Intervention(
            type=InterventionType.ITERATION_LIMIT,
            waypoint=sample_waypoint,
            iteration=10,
            max_iterations=10,
            error_summary="Max iterations reached",
        )

        error = InterventionNeededError(intervention)

        assert "iteration_limit" in str(error)
        assert "10" in str(error)

    def test_exception_stores_intervention(self, sample_waypoint: Waypoint) -> None:
        """Test that exception stores the intervention."""
        intervention = Intervention(
            type=InterventionType.TEST_FAILURE,
            waypoint=sample_waypoint,
            iteration=5,
            max_iterations=10,
            error_summary="Tests failing",
        )

        error = InterventionNeededError(intervention)

        assert error.intervention == intervention
        assert error.intervention.type == InterventionType.TEST_FAILURE

    def test_exception_can_be_raised_and_caught(
        self, sample_waypoint: Waypoint
    ) -> None:
        """Test that exception can be raised and caught properly."""
        intervention = Intervention(
            type=InterventionType.EXECUTION_ERROR,
            waypoint=sample_waypoint,
            iteration=3,
            max_iterations=10,
            error_summary="Something went wrong",
        )

        with pytest.raises(InterventionNeededError) as exc_info:
            raise InterventionNeededError(intervention)

        assert exc_info.value.intervention.type == InterventionType.EXECUTION_ERROR
        assert exc_info.value.intervention.waypoint.id == "WP-1"


class TestInterventionModalRollbackContext:
    """Tests rollback context resolution in InterventionModal."""

    def test_action_rollback_prefers_last_safe_ref(
        self, sample_waypoint: Waypoint
    ) -> None:
        intervention = Intervention(
            type=InterventionType.EXECUTION_ERROR,
            waypoint=sample_waypoint,
            iteration=2,
            max_iterations=10,
            error_summary="Needs rollback",
            context={"last_safe_ref": "HEAD", "last_safe_tag": "demo/WP-1"},
        )
        modal = InterventionModal(intervention)
        captured: list[InterventionResult | None] = []
        modal.dismiss = lambda result=None: captured.append(result)  # type: ignore[method-assign]

        modal.action_rollback()

        assert captured
        assert captured[0] is not None
        assert captured[0].action == InterventionAction.ROLLBACK
        assert captured[0].rollback_ref == "HEAD"
        assert captured[0].rollback_tag == "HEAD"

    def test_action_rollback_falls_back_to_last_safe_tag(
        self, sample_waypoint: Waypoint
    ) -> None:
        intervention = Intervention(
            type=InterventionType.EXECUTION_ERROR,
            waypoint=sample_waypoint,
            iteration=2,
            max_iterations=10,
            error_summary="Needs rollback",
            context={"last_safe_tag": "demo/WP-1"},
        )
        modal = InterventionModal(intervention)
        captured: list[InterventionResult | None] = []
        modal.dismiss = lambda result=None: captured.append(result)  # type: ignore[method-assign]

        modal.action_rollback()

        assert captured
        assert captured[0] is not None
        assert captured[0].rollback_ref == "demo/WP-1"
        assert captured[0].rollback_tag == "demo/WP-1"
