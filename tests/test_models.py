"""Unit tests for core models: Waypoint, FlightPlan, Project."""

import json
from datetime import datetime
from pathlib import Path

import pytest

from waypoints.models.waypoint import Waypoint, WaypointStatus
from waypoints.models.flight_plan import FlightPlan, FlightPlanReader, FlightPlanWriter
from waypoints.models.project import Project, slugify


class TestWaypointStatus:
    """Tests for WaypointStatus enum."""

    def test_pending_status(self) -> None:
        assert WaypointStatus.PENDING.value == "pending"

    def test_in_progress_status(self) -> None:
        assert WaypointStatus.IN_PROGRESS.value == "in_progress"

    def test_complete_status(self) -> None:
        assert WaypointStatus.COMPLETE.value == "complete"

    def test_failed_status(self) -> None:
        assert WaypointStatus.FAILED.value == "failed"

    def test_skipped_status(self) -> None:
        assert WaypointStatus.SKIPPED.value == "skipped"

    def test_from_string(self) -> None:
        """Test creating status from string value."""
        assert WaypointStatus("pending") == WaypointStatus.PENDING
        assert WaypointStatus("complete") == WaypointStatus.COMPLETE


class TestWaypoint:
    """Tests for Waypoint dataclass."""

    def test_create_minimal_waypoint(self) -> None:
        """Create waypoint with required fields only."""
        wp = Waypoint(id="WP-1", title="Test", objective="Test objective")
        assert wp.id == "WP-1"
        assert wp.title == "Test"
        assert wp.objective == "Test objective"
        assert wp.status == WaypointStatus.PENDING
        assert wp.acceptance_criteria == []
        assert wp.dependencies == []
        assert wp.parent_id is None
        assert wp.completed_at is None

    def test_create_full_waypoint(self) -> None:
        """Create waypoint with all fields."""
        wp = Waypoint(
            id="WP-1a",
            title="Sub waypoint",
            objective="Detailed objective",
            acceptance_criteria=["Criterion 1", "Criterion 2"],
            parent_id="WP-1",
            dependencies=["WP-0"],
            status=WaypointStatus.IN_PROGRESS,
        )
        assert wp.acceptance_criteria == ["Criterion 1", "Criterion 2"]
        assert wp.parent_id == "WP-1"
        assert wp.dependencies == ["WP-0"]
        assert wp.status == WaypointStatus.IN_PROGRESS

    def test_to_dict(self) -> None:
        """Serialize waypoint to dictionary."""
        wp = Waypoint(
            id="WP-1",
            title="Test",
            objective="Test objective",
            acceptance_criteria=["Done"],
            status=WaypointStatus.COMPLETE,
        )
        data = wp.to_dict()

        assert data["id"] == "WP-1"
        assert data["title"] == "Test"
        assert data["objective"] == "Test objective"
        assert data["acceptance_criteria"] == ["Done"]
        assert data["status"] == "complete"
        assert data["parent_id"] is None
        assert data["dependencies"] == []
        assert "created_at" in data

    def test_to_dict_with_completed_at(self) -> None:
        """Serialize waypoint with completed_at timestamp."""
        completed = datetime(2026, 1, 10, 12, 0, 0)
        wp = Waypoint(
            id="WP-1",
            title="Test",
            objective="Test objective",
            status=WaypointStatus.COMPLETE,
            completed_at=completed,
        )
        data = wp.to_dict()
        assert data["completed_at"] == completed.isoformat()

    def test_from_dict_minimal(self) -> None:
        """Deserialize waypoint with minimal fields."""
        data = {
            "id": "WP-2",
            "title": "Restored",
            "objective": "Restored objective",
        }
        wp = Waypoint.from_dict(data)

        assert wp.id == "WP-2"
        assert wp.title == "Restored"
        assert wp.objective == "Restored objective"
        assert wp.status == WaypointStatus.PENDING
        assert wp.acceptance_criteria == []

    def test_from_dict_full(self) -> None:
        """Deserialize waypoint with all fields."""
        data = {
            "id": "WP-3",
            "title": "Full waypoint",
            "objective": "Full objective",
            "acceptance_criteria": ["AC1", "AC2"],
            "parent_id": "WP-1",
            "dependencies": ["WP-2"],
            "status": "in_progress",
            "created_at": "2026-01-10T10:00:00",
            "completed_at": None,
        }
        wp = Waypoint.from_dict(data)

        assert wp.id == "WP-3"
        assert wp.parent_id == "WP-1"
        assert wp.dependencies == ["WP-2"]
        assert wp.status == WaypointStatus.IN_PROGRESS
        assert wp.created_at == datetime(2026, 1, 10, 10, 0, 0)
        assert wp.completed_at is None

    def test_serialization_roundtrip(self) -> None:
        """Waypoint survives serialization and deserialization."""
        original = Waypoint(
            id="WP-1",
            title="Roundtrip test",
            objective="Test serialization roundtrip",
            acceptance_criteria=["Roundtrip works"],
            parent_id="WP-0",
            dependencies=["WP-X"],
            status=WaypointStatus.FAILED,
        )
        data = original.to_dict()
        restored = Waypoint.from_dict(data)

        assert restored.id == original.id
        assert restored.title == original.title
        assert restored.objective == original.objective
        assert restored.acceptance_criteria == original.acceptance_criteria
        assert restored.parent_id == original.parent_id
        assert restored.dependencies == original.dependencies
        assert restored.status == original.status


class TestFlightPlan:
    """Tests for FlightPlan operations."""

    def test_empty_flight_plan(self) -> None:
        """Empty flight plan has no waypoints."""
        plan = FlightPlan()
        assert plan.waypoints == []
        assert plan.get_root_waypoints() == []

    def test_add_waypoint(self) -> None:
        """Add waypoint to flight plan."""
        plan = FlightPlan()
        wp = Waypoint(id="WP-1", title="First", objective="First objective")
        plan.add_waypoint(wp)

        assert len(plan.waypoints) == 1
        assert plan.waypoints[0].id == "WP-1"

    def test_get_waypoint(self) -> None:
        """Get waypoint by ID."""
        plan = FlightPlan()
        wp1 = Waypoint(id="WP-1", title="First", objective="First")
        wp2 = Waypoint(id="WP-2", title="Second", objective="Second")
        plan.add_waypoint(wp1)
        plan.add_waypoint(wp2)

        assert plan.get_waypoint("WP-1") == wp1
        assert plan.get_waypoint("WP-2") == wp2
        assert plan.get_waypoint("WP-999") is None

    def test_get_root_waypoints(self) -> None:
        """Get waypoints with no parent."""
        plan = FlightPlan()
        wp1 = Waypoint(id="WP-1", title="Root 1", objective="Root")
        wp2 = Waypoint(id="WP-1a", title="Child", objective="Child", parent_id="WP-1")
        wp3 = Waypoint(id="WP-2", title="Root 2", objective="Root")
        plan.add_waypoint(wp1)
        plan.add_waypoint(wp2)
        plan.add_waypoint(wp3)

        roots = plan.get_root_waypoints()
        assert len(roots) == 2
        assert wp1 in roots
        assert wp3 in roots
        assert wp2 not in roots

    def test_get_children(self) -> None:
        """Get direct children of a waypoint."""
        plan = FlightPlan()
        wp1 = Waypoint(id="WP-1", title="Parent", objective="Parent")
        wp1a = Waypoint(id="WP-1a", title="Child 1", objective="Child", parent_id="WP-1")
        wp1b = Waypoint(id="WP-1b", title="Child 2", objective="Child", parent_id="WP-1")
        wp2 = Waypoint(id="WP-2", title="Other", objective="Other")
        plan.add_waypoint(wp1)
        plan.add_waypoint(wp1a)
        plan.add_waypoint(wp1b)
        plan.add_waypoint(wp2)

        children = plan.get_children("WP-1")
        assert len(children) == 2
        assert wp1a in children
        assert wp1b in children

    def test_is_epic(self) -> None:
        """Check if waypoint has children (is an epic)."""
        plan = FlightPlan()
        wp1 = Waypoint(id="WP-1", title="Epic", objective="Epic")
        wp1a = Waypoint(id="WP-1a", title="Child", objective="Child", parent_id="WP-1")
        wp2 = Waypoint(id="WP-2", title="Single", objective="Single")
        plan.add_waypoint(wp1)
        plan.add_waypoint(wp1a)
        plan.add_waypoint(wp2)

        assert plan.is_epic("WP-1") is True
        assert plan.is_epic("WP-2") is False
        assert plan.is_epic("WP-999") is False

    def test_get_dependents(self) -> None:
        """Get waypoints that depend on this one."""
        plan = FlightPlan()
        wp1 = Waypoint(id="WP-1", title="First", objective="First")
        wp2 = Waypoint(id="WP-2", title="Second", objective="Second", dependencies=["WP-1"])
        wp3 = Waypoint(id="WP-3", title="Third", objective="Third", dependencies=["WP-1", "WP-2"])
        plan.add_waypoint(wp1)
        plan.add_waypoint(wp2)
        plan.add_waypoint(wp3)

        dependents = plan.get_dependents("WP-1")
        assert len(dependents) == 2
        assert wp2 in dependents
        assert wp3 in dependents

    def test_update_waypoint(self) -> None:
        """Update an existing waypoint."""
        plan = FlightPlan()
        wp = Waypoint(id="WP-1", title="Original", objective="Original")
        plan.add_waypoint(wp)

        updated = Waypoint(
            id="WP-1",
            title="Updated",
            objective="Updated objective",
            status=WaypointStatus.COMPLETE,
        )
        result = plan.update_waypoint(updated)

        assert result is True
        assert plan.get_waypoint("WP-1").title == "Updated"
        assert plan.get_waypoint("WP-1").status == WaypointStatus.COMPLETE

    def test_update_nonexistent_waypoint(self) -> None:
        """Update returns False for nonexistent waypoint."""
        plan = FlightPlan()
        wp = Waypoint(id="WP-999", title="Ghost", objective="Ghost")
        result = plan.update_waypoint(wp)
        assert result is False

    def test_remove_waypoint(self) -> None:
        """Remove waypoint from plan."""
        plan = FlightPlan()
        wp1 = Waypoint(id="WP-1", title="First", objective="First")
        wp2 = Waypoint(id="WP-2", title="Second", objective="Second", dependencies=["WP-1"])
        plan.add_waypoint(wp1)
        plan.add_waypoint(wp2)

        plan.remove_waypoint("WP-1")

        assert plan.get_waypoint("WP-1") is None
        # Dependency should be cleaned up
        assert plan.get_waypoint("WP-2").dependencies == []

    def test_insert_waypoints_after(self) -> None:
        """Insert waypoints after a parent."""
        plan = FlightPlan()
        wp1 = Waypoint(id="WP-1", title="First", objective="First")
        wp2 = Waypoint(id="WP-2", title="Second", objective="Second")
        wp3 = Waypoint(id="WP-3", title="Third", objective="Third")
        plan.add_waypoint(wp1)
        plan.add_waypoint(wp3)

        plan.insert_waypoints_after("WP-1", [wp2])

        # WP-2 should be between WP-1 and WP-3
        assert plan.waypoints[0].id == "WP-1"
        assert plan.waypoints[1].id == "WP-2"
        assert plan.waypoints[2].id == "WP-3"

    def test_insert_waypoints_after_nonexistent(self) -> None:
        """Insert after nonexistent parent appends at end."""
        plan = FlightPlan()
        wp1 = Waypoint(id="WP-1", title="First", objective="First")
        wp2 = Waypoint(id="WP-2", title="Second", objective="Second")
        plan.add_waypoint(wp1)

        plan.insert_waypoints_after("WP-999", [wp2])

        assert plan.waypoints[-1].id == "WP-2"

    def test_iterate_in_order(self) -> None:
        """Iterate waypoints in tree order with depth."""
        plan = FlightPlan()
        wp1 = Waypoint(id="WP-1", title="Root", objective="Root")
        wp1a = Waypoint(id="WP-1a", title="Child", objective="Child", parent_id="WP-1")
        wp1a1 = Waypoint(id="WP-1a1", title="Grandchild", objective="Grand", parent_id="WP-1a")
        wp2 = Waypoint(id="WP-2", title="Root 2", objective="Root 2")
        plan.add_waypoint(wp1)
        plan.add_waypoint(wp1a)
        plan.add_waypoint(wp1a1)
        plan.add_waypoint(wp2)

        result = list(plan.iterate_in_order())

        assert result[0] == (wp1, 0)
        assert result[1] == (wp1a, 1)
        assert result[2] == (wp1a1, 2)
        assert result[3] == (wp2, 0)

    def test_validate_dependencies_no_cycle(self) -> None:
        """Valid dependencies pass validation."""
        plan = FlightPlan()
        wp1 = Waypoint(id="WP-1", title="First", objective="First")
        wp2 = Waypoint(id="WP-2", title="Second", objective="Second", dependencies=["WP-1"])
        wp3 = Waypoint(id="WP-3", title="Third", objective="Third", dependencies=["WP-2"])
        plan.add_waypoint(wp1)
        plan.add_waypoint(wp2)
        plan.add_waypoint(wp3)

        errors = plan.validate_dependencies()
        assert errors == []

    def test_validate_dependencies_with_cycle(self) -> None:
        """Circular dependencies detected."""
        plan = FlightPlan()
        wp1 = Waypoint(id="WP-1", title="First", objective="First", dependencies=["WP-3"])
        wp2 = Waypoint(id="WP-2", title="Second", objective="Second", dependencies=["WP-1"])
        wp3 = Waypoint(id="WP-3", title="Third", objective="Third", dependencies=["WP-2"])
        plan.add_waypoint(wp1)
        plan.add_waypoint(wp2)
        plan.add_waypoint(wp3)

        errors = plan.validate_dependencies()
        assert len(errors) > 0
        assert any("Circular" in e for e in errors)

    def test_to_dict(self) -> None:
        """Serialize flight plan to dictionary."""
        plan = FlightPlan()
        wp = Waypoint(id="WP-1", title="Test", objective="Test")
        plan.add_waypoint(wp)

        data = plan.to_dict()

        assert "created_at" in data
        assert "updated_at" in data
        assert len(data["waypoints"]) == 1
        assert data["waypoints"][0]["id"] == "WP-1"


class TestFlightPlanPersistence:
    """Tests for FlightPlan read/write operations."""

    @pytest.fixture
    def mock_project(self, tmp_path: Path):
        """Create a mock project for testing."""

        class MockProject:
            def __init__(self, path: Path):
                self._path = path

            def get_path(self) -> Path:
                return self._path

        return MockProject(tmp_path)

    def test_save_and_load(self, mock_project) -> None:
        """Save and load flight plan."""
        # Create and save
        plan = FlightPlan()
        wp1 = Waypoint(id="WP-1", title="First", objective="First objective")
        wp2 = Waypoint(id="WP-2", title="Second", objective="Second objective")
        plan.add_waypoint(wp1)
        plan.add_waypoint(wp2)

        writer = FlightPlanWriter(mock_project)
        writer.save(plan)

        # Load and verify
        loaded = FlightPlanReader.load(mock_project)

        assert loaded is not None
        assert len(loaded.waypoints) == 2
        assert loaded.waypoints[0].id == "WP-1"
        assert loaded.waypoints[1].id == "WP-2"

    def test_file_format(self, mock_project) -> None:
        """Verify JSONL file format with schema header."""
        plan = FlightPlan()
        wp = Waypoint(id="WP-1", title="Test", objective="Test objective")
        plan.add_waypoint(wp)

        writer = FlightPlanWriter(mock_project)
        writer.save(plan)

        # Read file directly
        file_path = mock_project.get_path() / "flight-plan.jsonl"
        with open(file_path) as f:
            lines = f.readlines()

        # Header + 1 waypoint
        assert len(lines) == 2

        header = json.loads(lines[0])
        assert header["_schema"] == "flight_plan"
        assert header["_version"] == "1.0"

        waypoint_data = json.loads(lines[1])
        assert waypoint_data["id"] == "WP-1"

    def test_load_nonexistent(self, mock_project) -> None:
        """Load returns None for nonexistent file."""
        loaded = FlightPlanReader.load(mock_project)
        assert loaded is None

    def test_exists(self, mock_project) -> None:
        """Check if flight plan exists."""
        assert FlightPlanReader.exists(mock_project) is False

        plan = FlightPlan()
        writer = FlightPlanWriter(mock_project)
        writer.save(plan)

        assert FlightPlanReader.exists(mock_project) is True

    def test_append_waypoint(self, mock_project) -> None:
        """Append waypoint to existing file."""
        plan = FlightPlan()
        wp1 = Waypoint(id="WP-1", title="First", objective="First")
        plan.add_waypoint(wp1)

        writer = FlightPlanWriter(mock_project)
        writer.save(plan)

        # Append another
        wp2 = Waypoint(id="WP-2", title="Second", objective="Second")
        writer.append_waypoint(wp2)

        # Reload and verify
        loaded = FlightPlanReader.load(mock_project)
        assert len(loaded.waypoints) == 2


class TestSlugify:
    """Tests for slugify function."""

    def test_simple_name(self) -> None:
        assert slugify("My Project") == "my-project"

    def test_with_numbers(self) -> None:
        assert slugify("Project 2.0") == "project-20"

    def test_special_characters(self) -> None:
        assert slugify("AI Task Manager!") == "ai-task-manager"

    def test_underscores(self) -> None:
        assert slugify("my_project_name") == "my-project-name"

    def test_consecutive_spaces(self) -> None:
        assert slugify("My   Project") == "my-project"

    def test_leading_trailing_hyphens(self) -> None:
        assert slugify("-Project-") == "project"

    def test_empty_string(self) -> None:
        assert slugify("") == "unnamed-project"

    def test_only_special_chars(self) -> None:
        assert slugify("!!!") == "unnamed-project"


class TestProject:
    """Tests for Project model."""

    @pytest.fixture
    def temp_projects_dir(self, tmp_path: Path, monkeypatch):
        """Set up temporary projects directory."""
        from waypoints import config

        # Create temp settings with custom project directory
        monkeypatch.setattr(config.settings, "project_directory", tmp_path)
        return tmp_path

    def test_create_project(self, temp_projects_dir: Path) -> None:
        """Create a new project."""
        project = Project.create("Test Project", idea="Build something cool")

        assert project.name == "Test Project"
        assert project.slug == "test-project"
        assert project.initial_idea == "Build something cool"
        assert project.journey is not None
        assert (temp_projects_dir / "test-project" / "project.json").exists()

    def test_project_directories_created(self, temp_projects_dir: Path) -> None:
        """Project directories are created on creation."""
        project = Project.create("Dir Test")

        assert project.get_path().exists()
        assert project.get_sessions_path().exists()
        assert project.get_docs_path().exists()

    def test_to_dict(self, temp_projects_dir: Path) -> None:
        """Serialize project to dictionary."""
        project = Project.create("Serialize Test", idea="Test idea")
        data = project.to_dict()

        assert data["name"] == "Serialize Test"
        assert data["slug"] == "serialize-test"
        assert data["initial_idea"] == "Test idea"
        assert "created_at" in data
        assert "updated_at" in data
        assert "journey" in data

    def test_from_dict(self) -> None:
        """Deserialize project from dictionary."""
        data = {
            "name": "Restored Project",
            "slug": "restored-project",
            "created_at": "2026-01-10T10:00:00",
            "updated_at": "2026-01-10T11:00:00",
            "initial_idea": "Original idea",
        }
        project = Project.from_dict(data)

        assert project.name == "Restored Project"
        assert project.slug == "restored-project"
        assert project.initial_idea == "Original idea"
        assert project.created_at == datetime(2026, 1, 10, 10, 0, 0)

    def test_load_project(self, temp_projects_dir: Path) -> None:
        """Load project by slug."""
        original = Project.create("Load Test")
        loaded = Project.load("load-test")

        assert loaded.name == original.name
        assert loaded.slug == original.slug

    def test_load_nonexistent(self, temp_projects_dir: Path) -> None:
        """Load raises FileNotFoundError for nonexistent project."""
        with pytest.raises(FileNotFoundError, match="Project not found"):
            Project.load("nonexistent")

    def test_list_all(self, temp_projects_dir: Path) -> None:
        """List all projects."""
        Project.create("Project A")
        Project.create("Project B")

        projects = Project.list_all()

        assert len(projects) == 2
        slugs = [p.slug for p in projects]
        assert "project-a" in slugs
        assert "project-b" in slugs

    def test_list_all_empty(self, temp_projects_dir: Path) -> None:
        """List returns empty when no projects exist."""
        projects = Project.list_all()
        assert projects == []

    def test_save_updates_timestamp(self, temp_projects_dir: Path) -> None:
        """Save updates the updated_at timestamp."""
        project = Project.create("Timestamp Test")
        original_updated = project.updated_at

        # Small delay to ensure timestamp changes
        import time

        time.sleep(0.01)

        project.name = "Updated Name"
        project.save()

        assert project.updated_at > original_updated

    def test_delete_project(self, temp_projects_dir: Path) -> None:
        """Delete removes project directory."""
        project = Project.create("Delete Test")
        project_path = project.get_path()
        assert project_path.exists()

        project.delete()

        assert not project_path.exists()

    def test_transition_journey(self, temp_projects_dir: Path) -> None:
        """Transition project journey state."""
        from waypoints.models.journey import JourneyState

        project = Project.create("Journey Test")
        assert project.journey.state == JourneyState.SPARK_IDLE

        project.transition_journey(JourneyState.SPARK_ENTERING)

        # Reload and verify
        loaded = Project.load("journey-test")
        assert loaded.journey.state == JourneyState.SPARK_ENTERING

    def test_transition_journey_idempotent(self, temp_projects_dir: Path) -> None:
        """Transitioning to same state is idempotent."""
        from waypoints.models.journey import JourneyState

        project = Project.create("Idempotent Test")
        project.transition_journey(JourneyState.SPARK_IDLE)  # Same state

        assert project.journey.state == JourneyState.SPARK_IDLE
