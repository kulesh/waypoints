"""Tests for schema versioning and migration."""

import json
from pathlib import Path

import pytest

from waypoints.models.schema import (
    CURRENT_VERSIONS,
    InvalidSchemaError,
    MigrationNotFoundError,
    SchemaHeader,
    migrate_if_needed,
    read_schema_header,
    write_schema_fields,
)


class TestSchemaHeader:
    """Tests for SchemaHeader dataclass."""

    def test_is_legacy_true(self) -> None:
        """Test that version 0.0 is legacy."""
        header = SchemaHeader(schema_type="flight_plan", schema_version="0.0")
        assert header.is_legacy is True

    def test_is_legacy_false(self) -> None:
        """Test that current version is not legacy."""
        header = SchemaHeader(schema_type="flight_plan", schema_version="1.0")
        assert header.is_legacy is False

    def test_is_current_true(self) -> None:
        """Test that current version matches CURRENT_VERSIONS."""
        header = SchemaHeader(schema_type="flight_plan", schema_version="1.0")
        assert header.is_current is True

    def test_is_current_false(self) -> None:
        """Test that old version is not current."""
        header = SchemaHeader(schema_type="flight_plan", schema_version="0.0")
        assert header.is_current is False


class TestWriteSchemaFields:
    """Tests for write_schema_fields function."""

    def test_returns_schema_and_version(self) -> None:
        """Test that schema fields include type and current version."""
        fields = write_schema_fields("flight_plan")
        assert fields == {"_schema": "flight_plan", "_version": "1.0"}

    def test_all_schema_types(self) -> None:
        """Test schema fields for all known types."""
        for schema_type, version in CURRENT_VERSIONS.items():
            fields = write_schema_fields(schema_type)
            assert fields["_schema"] == schema_type
            assert fields["_version"] == version

    def test_unknown_schema_type(self) -> None:
        """Test that unknown schema type raises ValueError."""
        with pytest.raises(ValueError, match="Unknown schema type"):
            write_schema_fields("unknown_type")


class TestReadSchemaHeader:
    """Tests for read_schema_header function."""

    def test_read_versioned_header(self, tmp_path: Path) -> None:
        """Read header with schema fields."""
        file_path = tmp_path / "test.jsonl"
        header = {
            "_schema": "flight_plan",
            "_version": "1.0",
            "created_at": "2026-01-01T00:00:00",
        }
        file_path.write_text(json.dumps(header) + "\n")

        result = read_schema_header(file_path, "flight_plan")
        assert result.schema_type == "flight_plan"
        assert result.schema_version == "1.0"
        assert result.is_current is True

    def test_read_legacy_header(self, tmp_path: Path) -> None:
        """Legacy file returns version 0.0."""
        file_path = tmp_path / "test.jsonl"
        # Legacy header without schema fields
        header = {"created_at": "2026-01-01T00:00:00"}
        file_path.write_text(json.dumps(header) + "\n")

        result = read_schema_header(file_path, "flight_plan")
        assert result.schema_type == "flight_plan"
        assert result.schema_version == "0.0"
        assert result.is_legacy is True

    def test_read_empty_file(self, tmp_path: Path) -> None:
        """Empty file is treated as legacy."""
        file_path = tmp_path / "test.jsonl"
        file_path.write_text("")

        result = read_schema_header(file_path, "flight_plan")
        assert result.schema_version == "0.0"

    def test_file_not_exists(self, tmp_path: Path) -> None:
        """Non-existent file raises InvalidSchemaError."""
        file_path = tmp_path / "nonexistent.jsonl"

        with pytest.raises(InvalidSchemaError, match="does not exist"):
            read_schema_header(file_path, "flight_plan")

    def test_invalid_json(self, tmp_path: Path) -> None:
        """Invalid JSON raises InvalidSchemaError."""
        file_path = tmp_path / "test.jsonl"
        file_path.write_text("not valid json")

        with pytest.raises(InvalidSchemaError, match="Invalid JSON"):
            read_schema_header(file_path, "flight_plan")

    def test_wrong_schema_type(self, tmp_path: Path) -> None:
        """Wrong schema type raises InvalidSchemaError."""
        file_path = tmp_path / "test.jsonl"
        header = {"_schema": "session", "_version": "1.0"}
        file_path.write_text(json.dumps(header) + "\n")

        with pytest.raises(InvalidSchemaError, match="Expected schema 'flight_plan'"):
            read_schema_header(file_path, "flight_plan")


class TestMigration:
    """Tests for migrate_if_needed function."""

    def test_migrate_legacy_flight_plan(self, tmp_path: Path) -> None:
        """Legacy flight plan gets schema fields added."""
        file_path = tmp_path / "flight-plan.jsonl"
        # Create legacy flight plan (no schema fields)
        header = {
            "created_at": "2026-01-01T00:00:00",
            "updated_at": "2026-01-01T00:00:00",
        }
        waypoint = {"id": "WP-001", "title": "Test", "status": "pending"}
        file_path.write_text(json.dumps(header) + "\n" + json.dumps(waypoint) + "\n")

        # Migrate
        result = migrate_if_needed(file_path, "flight_plan")
        assert result is True

        # Verify migration
        with open(file_path) as f:
            lines = f.readlines()
        new_header = json.loads(lines[0])
        assert new_header["_schema"] == "flight_plan"
        assert new_header["_version"] == "1.0"
        assert new_header["created_at"] == "2026-01-01T00:00:00"

        # Verify waypoint preserved
        waypoint_data = json.loads(lines[1])
        assert waypoint_data["id"] == "WP-001"

    def test_migrate_legacy_session(self, tmp_path: Path) -> None:
        """Legacy session gets schema fields added."""
        file_path = tmp_path / "session.jsonl"
        header = {
            "session_id": "abc123",
            "phase": "ideation",
            "created_at": "2026-01-01T00:00:00",
        }
        message = {"role": "user", "content": "Hello"}
        file_path.write_text(json.dumps(header) + "\n" + json.dumps(message) + "\n")

        result = migrate_if_needed(file_path, "session")
        assert result is True

        with open(file_path) as f:
            lines = f.readlines()
        new_header = json.loads(lines[0])
        assert new_header["_schema"] == "session"
        assert new_header["_version"] == "1.0"
        assert new_header["session_id"] == "abc123"

    def test_migrate_legacy_execution_log(self, tmp_path: Path) -> None:
        """Legacy execution log gets schema fields added."""
        file_path = tmp_path / "execution.jsonl"
        header = {
            "type": "header",
            "execution_id": "exec-001",
            "waypoint_id": "WP-001",
            "started_at": "2026-01-01T00:00:00",
        }
        entry = {
            "type": "output",
            "content": "Test output",
            "timestamp": "2026-01-01T00:00:01",
        }
        file_path.write_text(json.dumps(header) + "\n" + json.dumps(entry) + "\n")

        result = migrate_if_needed(file_path, "execution_log")
        assert result is True

        with open(file_path) as f:
            lines = f.readlines()
        new_header = json.loads(lines[0])
        # type should be preserved as first key
        assert new_header["type"] == "header"
        assert new_header["_schema"] == "execution_log"
        assert new_header["_version"] == "1.0"

    def test_migrate_legacy_metrics(self, tmp_path: Path) -> None:
        """Legacy metrics file gets header added."""
        file_path = tmp_path / "metrics.jsonl"
        # Legacy metrics has no header, just entries
        call1 = {
            "call_id": "abc",
            "phase": "fly",
            "waypoint_id": None,
            "cost_usd": 0.01,
            "latency_ms": 100,
            "model": "claude-3-5-sonnet",
            "timestamp": "2026-01-01T00:00:00",
            "success": True,
            "error": None,
        }
        call2 = {
            "call_id": "def",
            "phase": "fly",
            "waypoint_id": None,
            "cost_usd": 0.02,
            "latency_ms": 150,
            "model": "claude-3-5-sonnet",
            "timestamp": "2026-01-01T00:00:01",
            "success": True,
            "error": None,
        }
        file_path.write_text(json.dumps(call1) + "\n" + json.dumps(call2) + "\n")

        result = migrate_if_needed(file_path, "metrics")
        assert result is True

        with open(file_path) as f:
            lines = f.readlines()
        # Should now have 3 lines: header + 2 entries
        assert len(lines) == 3
        header = json.loads(lines[0])
        assert header["_schema"] == "metrics"
        assert header["_version"] == "1.0"
        assert "created_at" in header

        # Verify entries preserved
        entry1 = json.loads(lines[1])
        assert entry1["call_id"] == "abc"
        entry2 = json.loads(lines[2])
        assert entry2["call_id"] == "def"

    def test_no_migration_current_version(self, tmp_path: Path) -> None:
        """Current version files not modified."""
        file_path = tmp_path / "test.jsonl"
        header = {
            "_schema": "flight_plan",
            "_version": "1.0",
            "created_at": "2026-01-01T00:00:00",
        }
        file_path.write_text(json.dumps(header) + "\n")
        original_content = file_path.read_text()

        result = migrate_if_needed(file_path, "flight_plan")
        assert result is False
        assert file_path.read_text() == original_content

    def test_migration_preserves_data(self, tmp_path: Path) -> None:
        """All data entries preserved after migration."""
        file_path = tmp_path / "flight-plan.jsonl"
        header = {
            "created_at": "2026-01-01T00:00:00",
            "updated_at": "2026-01-01T00:00:00",
        }
        waypoints = [
            {"id": "WP-001", "title": "First", "status": "pending"},
            {"id": "WP-002", "title": "Second", "status": "completed"},
            {"id": "WP-003", "title": "Third", "status": "in_progress"},
        ]
        content = json.dumps(header) + "\n"
        for wp in waypoints:
            content += json.dumps(wp) + "\n"
        file_path.write_text(content)

        migrate_if_needed(file_path, "flight_plan")

        with open(file_path) as f:
            lines = f.readlines()
        # Header + 3 waypoints
        assert len(lines) == 4

        for i, wp in enumerate(waypoints):
            loaded = json.loads(lines[i + 1])
            assert loaded == wp

    def test_nonexistent_file(self, tmp_path: Path) -> None:
        """Non-existent file returns False without error."""
        file_path = tmp_path / "nonexistent.jsonl"
        result = migrate_if_needed(file_path, "flight_plan")
        assert result is False

    def test_migration_not_found(self, tmp_path: Path) -> None:
        """Unknown version transition raises MigrationNotFoundError."""
        file_path = tmp_path / "test.jsonl"
        header = {"_schema": "flight_plan", "_version": "99.0"}
        file_path.write_text(json.dumps(header) + "\n")

        with pytest.raises(MigrationNotFoundError) as exc_info:
            migrate_if_needed(file_path, "flight_plan")
        assert exc_info.value.from_version == "99.0"
        assert exc_info.value.to_version == "1.0"
