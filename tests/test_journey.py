"""Tests for the Journey state machine."""

from datetime import UTC, datetime

import pytest

from waypoints.models.journey import (
    PHASE_TO_STATE,
    RECOVERABLE_STATES,
    RECOVERY_MAP,
    STATE_TO_PHASE,
    VALID_TRANSITIONS,
    InvalidTransitionError,
    Journey,
    JourneyState,
)


class TestJourneyState:
    """Tests for JourneyState enum and transition table."""

    def test_all_states_in_transition_table(self) -> None:
        """Every state should be in the VALID_TRANSITIONS table."""
        for state in JourneyState:
            assert state in VALID_TRANSITIONS, f"{state} missing from VALID_TRANSITIONS"

    def test_land_review_transitions(self) -> None:
        """LAND_REVIEW can transition to FLY_READY or SPARK_IDLE."""
        expected = {JourneyState.FLY_READY, JourneyState.SPARK_IDLE}
        assert VALID_TRANSITIONS[JourneyState.LAND_REVIEW] == expected

    def test_all_states_reachable(self) -> None:
        """Every state (except SPARK_IDLE) should be reachable from SPARK_IDLE."""
        reachable: set[JourneyState] = {JourneyState.SPARK_IDLE}
        changed = True

        while changed:
            changed = False
            for state, targets in VALID_TRANSITIONS.items():
                if state in reachable:
                    for target in targets:
                        if target not in reachable:
                            reachable.add(target)
                            changed = True

        for state in JourneyState:
            assert state in reachable, f"{state} is not reachable from SPARK_IDLE"

    def test_all_states_have_phase_mapping(self) -> None:
        """Every state should have a phase mapping."""
        for state in JourneyState:
            assert state in STATE_TO_PHASE, f"{state} missing from STATE_TO_PHASE"

    def test_phase_to_state_consistency(self) -> None:
        """PHASE_TO_STATE should map to valid states."""
        for phase, state in PHASE_TO_STATE.items():
            assert isinstance(state, JourneyState)
            assert STATE_TO_PHASE[state] == phase


class TestJourney:
    """Tests for Journey dataclass."""

    def test_create_new_journey(self) -> None:
        """New journey should start at SPARK_IDLE."""
        journey = Journey.new("test-project")

        assert journey.state == JourneyState.SPARK_IDLE
        assert journey.project_slug == "test-project"
        assert journey.state_history == []
        assert journey.updated_at is not None

    def test_can_transition_valid(self) -> None:
        """can_transition should return True for valid transitions."""
        journey = Journey.new("test")

        assert journey.can_transition(JourneyState.SPARK_ENTERING)

    def test_can_transition_invalid(self) -> None:
        """can_transition should return False for invalid transitions."""
        journey = Journey.new("test")

        assert not journey.can_transition(JourneyState.FLY_EXECUTING)
        assert not journey.can_transition(JourneyState.LAND_REVIEW)
        assert not journey.can_transition(JourneyState.CHART_REVIEW)

    def test_valid_transition(self) -> None:
        """Transition should return new Journey with updated state."""
        journey = Journey.new("test")

        new_journey = journey.transition(JourneyState.SPARK_ENTERING)

        assert new_journey.state == JourneyState.SPARK_ENTERING
        assert new_journey.project_slug == "test"
        assert len(new_journey.state_history) == 1
        assert new_journey.state_history[0]["from"] == "spark:idle"
        assert new_journey.state_history[0]["to"] == "spark:entering"

    def test_transition_is_immutable(self) -> None:
        """Original journey should not be modified by transition."""
        journey = Journey.new("test")
        original_state = journey.state

        new_journey = journey.transition(JourneyState.SPARK_ENTERING)

        assert journey.state == original_state
        assert new_journey.state != original_state

    def test_invalid_transition_raises(self) -> None:
        """Invalid transition should raise InvalidTransitionError."""
        journey = Journey.new("test")

        with pytest.raises(InvalidTransitionError) as exc_info:
            journey.transition(JourneyState.FLY_EXECUTING)

        assert exc_info.value.current == JourneyState.SPARK_IDLE
        assert exc_info.value.target == JourneyState.FLY_EXECUTING
        assert "spark:idle" in str(exc_info.value)
        assert "fly:executing" in str(exc_info.value)

    def test_phase_property(self) -> None:
        """Phase property should return correct phase name."""
        journey = Journey.new("test")

        assert journey.phase == "ideation"

        journey = journey.transition(JourneyState.SPARK_ENTERING)
        assert journey.phase == "ideation"

        journey = journey.transition(JourneyState.SHAPE_QA)
        assert journey.phase == "ideation-qa"

    def test_is_recoverable_property(self) -> None:
        """is_recoverable should return True for recoverable states."""
        journey = Journey.new("test")
        assert journey.is_recoverable  # SPARK_IDLE is recoverable

        journey = journey.transition(JourneyState.SPARK_ENTERING)
        assert not journey.is_recoverable  # SPARK_ENTERING is not

        journey = journey.transition(JourneyState.SHAPE_QA)
        assert journey.is_recoverable  # SHAPE_QA is recoverable


class TestJourneySerialization:
    """Tests for Journey serialization."""

    def test_to_dict(self) -> None:
        """to_dict should serialize all fields."""
        journey = Journey.new("test")
        journey = journey.transition(JourneyState.SPARK_ENTERING)

        data = journey.to_dict()

        assert data["state"] == "spark:entering"
        assert data["project_slug"] == "test"
        assert "updated_at" in data
        assert len(data["state_history"]) == 1

    def test_from_dict(self) -> None:
        """from_dict should deserialize correctly."""
        data = {
            "state": "shape:qa",
            "project_slug": "test-project",
            "updated_at": "2025-01-01T12:00:00+00:00",
            "state_history": [
                {"from": "spark:idle", "to": "spark:entering", "at": "..."},
            ],
        }

        journey = Journey.from_dict(data)

        assert journey.state == JourneyState.SHAPE_QA
        assert journey.project_slug == "test-project"
        assert len(journey.state_history) == 1

    def test_serialization_roundtrip(self) -> None:
        """Serialization should be reversible."""
        journey = Journey.new("test")
        journey = journey.transition(JourneyState.SPARK_ENTERING)
        journey = journey.transition(JourneyState.SHAPE_QA)

        data = journey.to_dict()
        restored = Journey.from_dict(data)

        assert restored.state == journey.state
        assert restored.project_slug == journey.project_slug
        assert len(restored.state_history) == len(journey.state_history)

    def test_from_dict_missing_history(self) -> None:
        """from_dict should handle missing state_history gracefully."""
        data = {
            "state": "spark:idle",
            "project_slug": "test",
            "updated_at": "2025-01-01T12:00:00+00:00",
        }

        journey = Journey.from_dict(data)

        assert journey.state_history == []


class TestJourneyRecovery:
    """Tests for Journey recovery from non-recoverable states."""

    def test_recover_from_recoverable_state(self) -> None:
        """Recovery from recoverable state should return self."""
        journey = Journey.new("test")  # SPARK_IDLE is recoverable

        recovered = journey.recover()

        assert recovered.state == JourneyState.SPARK_IDLE

    def test_recover_from_spark_entering(self) -> None:
        """SPARK_ENTERING should recover to SPARK_IDLE."""
        journey = Journey.new("test")
        journey = journey.transition(JourneyState.SPARK_ENTERING)

        recovered = journey.recover()

        assert recovered.state == JourneyState.SPARK_IDLE
        assert len(recovered.state_history) == 2
        assert recovered.state_history[-1]["reason"] == "recovery"

    def test_recover_from_brief_generating(self) -> None:
        """SHAPE_BRIEF_GENERATING should recover to SHAPE_QA."""
        # Build up to SHAPE_BRIEF_GENERATING
        journey = Journey.new("test")
        journey = journey.transition(JourneyState.SPARK_ENTERING)
        journey = journey.transition(JourneyState.SHAPE_QA)
        journey = journey.transition(JourneyState.SHAPE_BRIEF_GENERATING)

        recovered = journey.recover()

        assert recovered.state == JourneyState.SHAPE_QA

    def test_recover_from_fly_executing(self) -> None:
        """FLY_EXECUTING should recover to FLY_READY."""
        # Create journey at FLY_EXECUTING
        data = {
            "state": "fly:executing",
            "project_slug": "test",
            "updated_at": datetime.now(UTC).isoformat(),
            "state_history": [],
        }
        journey = Journey.from_dict(data)

        recovered = journey.recover()

        assert recovered.state == JourneyState.FLY_READY

    def test_all_non_recoverable_states_have_recovery(self) -> None:
        """Every non-recoverable state should have a recovery mapping."""
        for state in JourneyState:
            if state not in RECOVERABLE_STATES:
                assert state in RECOVERY_MAP, f"{state} has no recovery mapping"


class TestTransitionPaths:
    """Tests for complete transition paths (happy paths)."""

    def test_full_happy_path(self) -> None:
        """Test complete journey from SPARK to LAND_REVIEW."""
        journey = Journey.new("test")

        # SPARK -> SHAPE
        journey = journey.transition(JourneyState.SPARK_ENTERING)
        journey = journey.transition(JourneyState.SHAPE_QA)
        journey = journey.transition(JourneyState.SHAPE_BRIEF_GENERATING)
        journey = journey.transition(JourneyState.SHAPE_BRIEF_REVIEW)
        journey = journey.transition(JourneyState.SHAPE_SPEC_GENERATING)
        journey = journey.transition(JourneyState.SHAPE_SPEC_REVIEW)

        # CHART
        journey = journey.transition(JourneyState.CHART_GENERATING)
        journey = journey.transition(JourneyState.CHART_REVIEW)

        # FLY
        journey = journey.transition(JourneyState.FLY_READY)
        journey = journey.transition(JourneyState.FLY_EXECUTING)
        journey = journey.transition(JourneyState.LAND_REVIEW)

        assert journey.state == JourneyState.LAND_REVIEW
        assert len(journey.state_history) == 11

    def test_regenerate_brief(self) -> None:
        """Test regenerating a brief (loop back)."""
        journey = Journey.new("test")
        journey = journey.transition(JourneyState.SPARK_ENTERING)
        journey = journey.transition(JourneyState.SHAPE_QA)
        journey = journey.transition(JourneyState.SHAPE_BRIEF_GENERATING)
        journey = journey.transition(JourneyState.SHAPE_BRIEF_REVIEW)

        # Regenerate
        journey = journey.transition(JourneyState.SHAPE_BRIEF_GENERATING)
        assert journey.state == JourneyState.SHAPE_BRIEF_GENERATING

        # Continue forward
        journey = journey.transition(JourneyState.SHAPE_BRIEF_REVIEW)
        assert journey.state == JourneyState.SHAPE_BRIEF_REVIEW

    def test_regenerate_spec(self) -> None:
        """Test regenerating a spec (loop back)."""
        journey = Journey.new("test")
        journey = journey.transition(JourneyState.SPARK_ENTERING)
        journey = journey.transition(JourneyState.SHAPE_QA)
        journey = journey.transition(JourneyState.SHAPE_BRIEF_GENERATING)
        journey = journey.transition(JourneyState.SHAPE_BRIEF_REVIEW)
        journey = journey.transition(JourneyState.SHAPE_SPEC_GENERATING)
        journey = journey.transition(JourneyState.SHAPE_SPEC_REVIEW)

        # Regenerate
        journey = journey.transition(JourneyState.SHAPE_SPEC_GENERATING)
        assert journey.state == JourneyState.SHAPE_SPEC_GENERATING

    def test_regenerate_waypoints(self) -> None:
        """Test regenerating waypoints (loop back)."""
        journey = Journey.new("test")
        journey = journey.transition(JourneyState.SPARK_ENTERING)
        journey = journey.transition(JourneyState.SHAPE_QA)
        journey = journey.transition(JourneyState.SHAPE_BRIEF_GENERATING)
        journey = journey.transition(JourneyState.SHAPE_BRIEF_REVIEW)
        journey = journey.transition(JourneyState.SHAPE_SPEC_GENERATING)
        journey = journey.transition(JourneyState.SHAPE_SPEC_REVIEW)
        journey = journey.transition(JourneyState.CHART_GENERATING)
        journey = journey.transition(JourneyState.CHART_REVIEW)

        # Regenerate
        journey = journey.transition(JourneyState.CHART_GENERATING)
        assert journey.state == JourneyState.CHART_GENERATING

    def test_intervention_retry(self) -> None:
        """Test intervention and retry path."""
        # Fast forward to FLY
        journey = Journey.new("test")
        journey = journey.transition(JourneyState.SPARK_ENTERING)
        journey = journey.transition(JourneyState.SHAPE_QA)
        journey = journey.transition(JourneyState.SHAPE_BRIEF_GENERATING)
        journey = journey.transition(JourneyState.SHAPE_BRIEF_REVIEW)
        journey = journey.transition(JourneyState.SHAPE_SPEC_GENERATING)
        journey = journey.transition(JourneyState.SHAPE_SPEC_REVIEW)
        journey = journey.transition(JourneyState.CHART_GENERATING)
        journey = journey.transition(JourneyState.CHART_REVIEW)
        journey = journey.transition(JourneyState.FLY_READY)
        journey = journey.transition(JourneyState.FLY_EXECUTING)

        # Intervention needed
        journey = journey.transition(JourneyState.FLY_INTERVENTION)
        assert journey.state == JourneyState.FLY_INTERVENTION

        # Retry
        journey = journey.transition(JourneyState.FLY_EXECUTING)
        assert journey.state == JourneyState.FLY_EXECUTING

        # Complete
        journey = journey.transition(JourneyState.LAND_REVIEW)
        assert journey.state == JourneyState.LAND_REVIEW

    def test_intervention_edit_plan(self) -> None:
        """Test intervention with edit plan path."""
        # Fast forward to intervention
        data = {
            "state": "fly:intervention",
            "project_slug": "test",
            "updated_at": datetime.now(UTC).isoformat(),
            "state_history": [],
        }
        journey = Journey.from_dict(data)

        # Go back to chart
        journey = journey.transition(JourneyState.CHART_REVIEW)
        assert journey.state == JourneyState.CHART_REVIEW

    def test_pause_and_resume(self) -> None:
        """Test pause and resume execution."""
        # Fast forward to executing
        data = {
            "state": "fly:executing",
            "project_slug": "test",
            "updated_at": datetime.now(UTC).isoformat(),
            "state_history": [],
        }
        journey = Journey.from_dict(data)

        # Pause
        journey = journey.transition(JourneyState.FLY_PAUSED)
        assert journey.state == JourneyState.FLY_PAUSED

        # Resume
        journey = journey.transition(JourneyState.FLY_EXECUTING)
        assert journey.state == JourneyState.FLY_EXECUTING

    def test_pause_back_to_ready(self) -> None:
        """Test going from paused back to ready."""
        data = {
            "state": "fly:paused",
            "project_slug": "test",
            "updated_at": datetime.now(UTC).isoformat(),
            "state_history": [],
        }
        journey = Journey.from_dict(data)

        journey = journey.transition(JourneyState.FLY_READY)
        assert journey.state == JourneyState.FLY_READY
