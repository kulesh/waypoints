"""Tests for JourneyCoordinator business logic."""

from pathlib import Path

import pytest

from waypoints.models.flight_plan import FlightPlan
from waypoints.models.waypoint import Waypoint, WaypointStatus
from waypoints.orchestration import JourneyCoordinator


class MockProject:
    """Mock project for testing coordinator."""

    def __init__(self, path: Path | None = None):
        self._path = path or Path("/tmp/test-project")

    def get_path(self) -> Path:
        return self._path


class TestWaypointSelection:
    """Tests for waypoint selection logic."""

    @pytest.fixture
    def linear_flight_plan(self) -> FlightPlan:
        """Create a simple linear flight plan: WP-001 -> WP-002 -> WP-003."""
        fp = FlightPlan()
        fp.add_waypoint(
            Waypoint(
                id="WP-001",
                title="First",
                objective="First task",
                status=WaypointStatus.COMPLETE,
            )
        )
        fp.add_waypoint(
            Waypoint(
                id="WP-002",
                title="Second",
                objective="Second task",
                status=WaypointStatus.PENDING,
                dependencies=["WP-001"],
            )
        )
        fp.add_waypoint(
            Waypoint(
                id="WP-003",
                title="Third",
                objective="Third task",
                status=WaypointStatus.PENDING,
                dependencies=["WP-002"],
            )
        )
        return fp

    @pytest.fixture
    def coordinator(self, linear_flight_plan: FlightPlan) -> JourneyCoordinator:
        """Create a coordinator with the linear flight plan."""
        return JourneyCoordinator(
            project=MockProject(),  # type: ignore
            flight_plan=linear_flight_plan,
        )

    def test_selects_first_pending_with_met_deps(
        self, coordinator: JourneyCoordinator
    ) -> None:
        """Test that selection picks first pending waypoint with met dependencies."""
        wp = coordinator.select_next_waypoint()

        assert wp is not None
        assert wp.id == "WP-002"
        assert coordinator.current_waypoint == wp

    def test_skips_blocked_waypoints(self, coordinator: JourneyCoordinator) -> None:
        """Test that blocked waypoints are skipped."""
        # WP-003 depends on WP-002 which is PENDING, so WP-003 is blocked
        wp = coordinator.select_next_waypoint()

        assert wp is not None
        assert wp.id == "WP-002"
        assert wp.id != "WP-003"

    def test_returns_none_when_all_complete(self) -> None:
        """Test that None is returned when all waypoints are complete."""
        fp = FlightPlan()
        fp.add_waypoint(
            Waypoint(
                id="WP-001",
                title="Done",
                objective="Done",
                status=WaypointStatus.COMPLETE,
            )
        )
        coordinator = JourneyCoordinator(
            project=MockProject(),  # type: ignore
            flight_plan=fp,
        )

        wp = coordinator.select_next_waypoint()

        assert wp is None
        assert coordinator.current_waypoint is None

    def test_include_failed_selects_failed_waypoint(self) -> None:
        """Test that include_failed=True selects failed waypoints."""
        fp = FlightPlan()
        fp.add_waypoint(
            Waypoint(
                id="WP-001",
                title="Failed",
                objective="Failed task",
                status=WaypointStatus.FAILED,
            )
        )
        fp.add_waypoint(
            Waypoint(
                id="WP-002",
                title="Pending",
                objective="Pending task",
                status=WaypointStatus.PENDING,
            )
        )
        coordinator = JourneyCoordinator(
            project=MockProject(),  # type: ignore
            flight_plan=fp,
        )

        # Without include_failed, should skip failed and select pending
        wp = coordinator.select_next_waypoint(include_failed=False)
        assert wp is not None
        assert wp.id == "WP-002"

        # Reset current waypoint
        coordinator.current_waypoint = None

        # With include_failed, should select the failed waypoint
        wp = coordinator.select_next_waypoint(include_failed=True)
        assert wp is not None
        assert wp.id == "WP-001"

    def test_include_failed_selects_in_progress_waypoint(self) -> None:
        """Test that include_failed=True selects in-progress waypoints."""
        fp = FlightPlan()
        fp.add_waypoint(
            Waypoint(
                id="WP-001",
                title="In Progress",
                objective="In progress task",
                status=WaypointStatus.IN_PROGRESS,
            )
        )
        fp.add_waypoint(
            Waypoint(
                id="WP-002",
                title="Pending",
                objective="Pending task",
                status=WaypointStatus.PENDING,
            )
        )
        coordinator = JourneyCoordinator(
            project=MockProject(),  # type: ignore
            flight_plan=fp,
        )

        wp = coordinator.select_next_waypoint(include_failed=True)
        assert wp is not None
        assert wp.id == "WP-001"

    def test_skips_epics(self) -> None:
        """Test that epics (waypoints with children) are skipped."""
        fp = FlightPlan()
        # Parent epic
        fp.add_waypoint(
            Waypoint(
                id="WP-001",
                title="Epic",
                objective="Parent epic",
                status=WaypointStatus.PENDING,
            )
        )
        # Child of epic
        fp.add_waypoint(
            Waypoint(
                id="WP-001a",
                title="Child",
                objective="Child task",
                status=WaypointStatus.PENDING,
                parent_id="WP-001",
            )
        )
        coordinator = JourneyCoordinator(
            project=MockProject(),  # type: ignore
            flight_plan=fp,
        )

        wp = coordinator.select_next_waypoint()

        # Should select child, not epic
        assert wp is not None
        assert wp.id == "WP-001a"


class TestCompletionStatus:
    """Tests for completion status calculation."""

    def test_all_complete(self) -> None:
        """Test status when all waypoints are complete."""
        fp = FlightPlan()
        fp.add_waypoint(
            Waypoint(
                id="WP-001", title="A", objective="A", status=WaypointStatus.COMPLETE
            )
        )
        fp.add_waypoint(
            Waypoint(
                id="WP-002", title="B", objective="B", status=WaypointStatus.COMPLETE
            )
        )
        coordinator = JourneyCoordinator(
            project=MockProject(),  # type: ignore
            flight_plan=fp,
        )

        status = coordinator.get_completion_status()

        assert status.total == 2
        assert status.complete == 2
        assert status.pending == 0
        assert status.failed == 0
        assert status.blocked == 0
        assert status.all_complete is True

    def test_with_pending(self) -> None:
        """Test status with pending waypoints."""
        fp = FlightPlan()
        fp.add_waypoint(
            Waypoint(
                id="WP-001", title="A", objective="A", status=WaypointStatus.COMPLETE
            )
        )
        fp.add_waypoint(
            Waypoint(
                id="WP-002", title="B", objective="B", status=WaypointStatus.PENDING
            )
        )
        coordinator = JourneyCoordinator(
            project=MockProject(),  # type: ignore
            flight_plan=fp,
        )

        status = coordinator.get_completion_status()

        assert status.total == 2
        assert status.complete == 1
        assert status.pending == 1
        assert status.all_complete is False

    def test_with_failed(self) -> None:
        """Test status with failed waypoints."""
        fp = FlightPlan()
        fp.add_waypoint(
            Waypoint(
                id="WP-001", title="A", objective="A", status=WaypointStatus.FAILED
            )
        )
        coordinator = JourneyCoordinator(
            project=MockProject(),  # type: ignore
            flight_plan=fp,
        )

        status = coordinator.get_completion_status()

        assert status.failed == 1
        assert status.has_failed is True

    def test_with_blocked(self) -> None:
        """Test status with blocked waypoints (pending with failed dependency)."""
        fp = FlightPlan()
        fp.add_waypoint(
            Waypoint(
                id="WP-001", title="A", objective="A", status=WaypointStatus.FAILED
            )
        )
        fp.add_waypoint(
            Waypoint(
                id="WP-002",
                title="B",
                objective="B",
                status=WaypointStatus.PENDING,
                dependencies=["WP-001"],
            )
        )
        coordinator = JourneyCoordinator(
            project=MockProject(),  # type: ignore
            flight_plan=fp,
        )

        status = coordinator.get_completion_status()

        assert status.failed == 1
        assert status.blocked == 1
        assert status.has_blocked is True

    def test_skipped_counts_as_complete(self) -> None:
        """Test that skipped waypoints count as complete."""
        fp = FlightPlan()
        fp.add_waypoint(
            Waypoint(
                id="WP-001", title="A", objective="A", status=WaypointStatus.SKIPPED
            )
        )
        coordinator = JourneyCoordinator(
            project=MockProject(),  # type: ignore
            flight_plan=fp,
        )

        status = coordinator.get_completion_status()

        assert status.complete == 1
        assert status.all_complete is True


class TestResetStaleInProgress:
    """Tests for resetting stale in-progress waypoints."""

    def test_resets_in_progress_to_pending(self) -> None:
        """Test that IN_PROGRESS waypoints are reset to PENDING."""
        fp = FlightPlan()
        fp.add_waypoint(
            Waypoint(
                id="WP-001",
                title="Stale",
                objective="Stale in-progress",
                status=WaypointStatus.IN_PROGRESS,
            )
        )
        coordinator = JourneyCoordinator(
            project=MockProject(),  # type: ignore
            flight_plan=fp,
        )

        changed = coordinator.reset_stale_in_progress()

        assert changed is True
        assert fp.waypoints[0].status == WaypointStatus.PENDING

    def test_returns_false_when_no_changes(self) -> None:
        """Test that False is returned when no waypoints need reset."""
        fp = FlightPlan()
        fp.add_waypoint(
            Waypoint(
                id="WP-001",
                title="Pending",
                objective="Already pending",
                status=WaypointStatus.PENDING,
            )
        )
        coordinator = JourneyCoordinator(
            project=MockProject(),  # type: ignore
            flight_plan=fp,
        )

        changed = coordinator.reset_stale_in_progress()

        assert changed is False


class TestWaypointCRUD:
    """Tests for waypoint CRUD operations."""

    def test_update_waypoint(self) -> None:
        """Test updating a waypoint."""
        fp = FlightPlan()
        wp = Waypoint(id="WP-001", title="Original", objective="Original objective")
        fp.add_waypoint(wp)
        coordinator = JourneyCoordinator(
            project=MockProject(),  # type: ignore
            flight_plan=fp,
        )

        # Update the waypoint
        wp.title = "Updated"
        coordinator.update_waypoint(wp)

        # Verify the update
        updated = fp.get_waypoint("WP-001")
        assert updated is not None
        assert updated.title == "Updated"

    def test_delete_waypoint(self) -> None:
        """Test deleting a waypoint."""
        fp = FlightPlan()
        fp.add_waypoint(Waypoint(id="WP-001", title="To Delete", objective="Delete me"))
        fp.add_waypoint(Waypoint(id="WP-002", title="Keep", objective="Keep me"))
        coordinator = JourneyCoordinator(
            project=MockProject(),  # type: ignore
            flight_plan=fp,
        )

        orphaned = coordinator.delete_waypoint("WP-001")

        assert len(fp.waypoints) == 1
        assert fp.waypoints[0].id == "WP-002"
        assert orphaned == []

    def test_delete_waypoint_returns_orphaned_dependents(self) -> None:
        """Test that deleting returns IDs of waypoints that depended on it."""
        fp = FlightPlan()
        fp.add_waypoint(Waypoint(id="WP-001", title="Dep", objective="Dependency"))
        fp.add_waypoint(
            Waypoint(
                id="WP-002",
                title="Dependent",
                objective="Depends on WP-001",
                dependencies=["WP-001"],
            )
        )
        coordinator = JourneyCoordinator(
            project=MockProject(),  # type: ignore
            flight_plan=fp,
        )

        orphaned = coordinator.delete_waypoint("WP-001")

        assert "WP-002" in orphaned

    def test_add_sub_waypoints(self) -> None:
        """Test adding sub-waypoints to a parent."""
        fp = FlightPlan()
        fp.add_waypoint(Waypoint(id="WP-001", title="Parent", objective="Parent task"))
        coordinator = JourneyCoordinator(
            project=MockProject(),  # type: ignore
            flight_plan=fp,
        )

        sub_waypoints = [
            Waypoint(id="WP-001a", title="Child A", objective="Child A"),
            Waypoint(id="WP-001b", title="Child B", objective="Child B"),
        ]
        coordinator.add_sub_waypoints("WP-001", sub_waypoints)

        assert len(fp.waypoints) == 3
        # Children should have parent_id set
        assert fp.get_waypoint("WP-001a").parent_id == "WP-001"
        assert fp.get_waypoint("WP-001b").parent_id == "WP-001"


class TestIsEpic:
    """Tests for epic detection."""

    def test_is_epic_with_children(self) -> None:
        """Test that waypoint with children is detected as epic."""
        fp = FlightPlan()
        fp.add_waypoint(Waypoint(id="WP-001", title="Parent", objective="Parent"))
        fp.add_waypoint(
            Waypoint(id="WP-001a", title="Child", objective="Child", parent_id="WP-001")
        )
        coordinator = JourneyCoordinator(
            project=MockProject(),  # type: ignore
            flight_plan=fp,
        )

        assert coordinator.is_epic("WP-001") is True
        assert coordinator.is_epic("WP-001a") is False

    def test_is_epic_without_children(self) -> None:
        """Test that waypoint without children is not an epic."""
        fp = FlightPlan()
        fp.add_waypoint(Waypoint(id="WP-001", title="Leaf", objective="Leaf task"))
        coordinator = JourneyCoordinator(
            project=MockProject(),  # type: ignore
            flight_plan=fp,
        )

        assert coordinator.is_epic("WP-001") is False
