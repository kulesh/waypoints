"""Tests for LLM output validation."""

import json

import pytest

from waypoints.llm.validation import (
    WaypointValidationError,
    extract_json_array,
    validate_schema,
    validate_semantics,
    validate_waypoints,
)


class TestExtractJson:
    """Tests for JSON extraction from LLM responses."""

    def test_plain_array(self) -> None:
        """Extract plain JSON array."""
        response = '[{"id": "WP-1", "title": "Test"}]'
        result = extract_json_array(response)
        assert result == [{"id": "WP-1", "title": "Test"}]

    def test_markdown_fenced(self) -> None:
        """Extract JSON from markdown code block."""
        response = """Here are the waypoints:

```json
[{"id": "WP-1", "title": "Test"}]
```

Let me know if you need changes."""
        result = extract_json_array(response)
        assert result == [{"id": "WP-1", "title": "Test"}]

    def test_markdown_fenced_no_lang(self) -> None:
        """Extract JSON from markdown code block without language tag."""
        response = """```
[{"id": "WP-1", "title": "Test"}]
```"""
        result = extract_json_array(response)
        assert result == [{"id": "WP-1", "title": "Test"}]

    def test_with_surrounding_text(self) -> None:
        """Extract JSON array with surrounding text."""
        response = """Based on the spec, here are the waypoints:
[{"id": "WP-1", "title": "Test"}]
These waypoints cover the main requirements."""
        result = extract_json_array(response)
        assert result == [{"id": "WP-1", "title": "Test"}]

    def test_no_array_raises(self) -> None:
        """Raise error when no JSON array found."""
        response = "Here is the waypoint: {id: WP-1}"
        with pytest.raises(ValueError, match="No JSON array"):
            extract_json_array(response)

    def test_empty_response_raises(self) -> None:
        """Raise error on empty response."""
        with pytest.raises(ValueError, match="No JSON array"):
            extract_json_array("")

    def test_malformed_json_raises(self) -> None:
        """Raise error on malformed JSON."""
        response = '[{"id": "WP-1", "title": }]'
        with pytest.raises(json.JSONDecodeError):
            extract_json_array(response)


class TestSchemaValidation:
    """Tests for JSON schema validation."""

    def test_valid_waypoints(self) -> None:
        """Valid waypoints pass schema validation."""
        data = [
            {
                "id": "WP-1",
                "title": "First waypoint",
                "objective": "Implement the first feature completely",
                "acceptance_criteria": ["Feature works", "Tests pass"],
            }
        ]
        errors = validate_schema(data)
        assert errors == []

    def test_valid_with_optional_fields(self) -> None:
        """Valid waypoints with optional fields."""
        data = [
            {
                "id": "WP-1",
                "title": "First waypoint",
                "objective": "Implement the first feature completely",
                "acceptance_criteria": ["Feature works"],
                "parent_id": None,
                "dependencies": [],
            }
        ]
        errors = validate_schema(data)
        assert errors == []

    def test_valid_with_parent_and_deps(self) -> None:
        """Valid waypoint with parent and dependencies."""
        data = [
            {
                "id": "WP-1a",
                "title": "Sub waypoint",
                "objective": "Implement a sub-feature completely",
                "acceptance_criteria": ["Sub-feature works"],
                "parent_id": "WP-1",
                "dependencies": ["WP-0"],
            }
        ]
        errors = validate_schema(data)
        assert errors == []

    def test_missing_required_field(self) -> None:
        """Detect missing required fields."""
        data = [
            {
                "id": "WP-1",
                "title": "Missing objective",
                # objective is missing
                "acceptance_criteria": ["Works"],
            }
        ]
        errors = validate_schema(data)
        assert len(errors) > 0
        assert any("objective" in e for e in errors)

    def test_invalid_id_format(self) -> None:
        """Detect invalid waypoint ID format."""
        data = [
            {
                "id": "TASK-1",  # Wrong format
                "title": "Bad ID waypoint",
                "objective": "This has an invalid ID format",
                "acceptance_criteria": ["Works"],
            }
        ]
        errors = validate_schema(data)
        assert len(errors) > 0
        assert any("id" in e.lower() or "pattern" in e.lower() for e in errors)

    def test_valid_id_formats(self) -> None:
        """Test various valid ID formats."""
        valid_ids = ["WP-1", "WP-123", "WP-1a", "WP-99z"]
        for wp_id in valid_ids:
            data = [
                {
                    "id": wp_id,
                    "title": f"Waypoint {wp_id}",
                    "objective": "Test objective that is long enough",
                    "acceptance_criteria": ["Works"],
                }
            ]
            errors = validate_schema(data)
            assert errors == [], f"ID {wp_id} should be valid"

    def test_empty_acceptance_criteria(self) -> None:
        """Detect empty acceptance criteria."""
        data = [
            {
                "id": "WP-1",
                "title": "No criteria",
                "objective": "This waypoint has no criteria",
                "acceptance_criteria": [],
            }
        ]
        errors = validate_schema(data)
        assert len(errors) > 0
        assert any("acceptance_criteria" in e or "minItems" in e for e in errors)

    def test_title_too_short(self) -> None:
        """Detect title that is too short."""
        data = [
            {
                "id": "WP-1",
                "title": "AB",  # Too short
                "objective": "This objective is long enough",
                "acceptance_criteria": ["Works"],
            }
        ]
        errors = validate_schema(data)
        assert len(errors) > 0

    def test_objective_too_short(self) -> None:
        """Detect objective that is too short."""
        data = [
            {
                "id": "WP-1",
                "title": "Valid title",
                "objective": "Short",  # Too short
                "acceptance_criteria": ["Works"],
            }
        ]
        errors = validate_schema(data)
        assert len(errors) > 0


class TestSemanticValidation:
    """Tests for semantic validation."""

    def test_valid_semantics(self) -> None:
        """Valid waypoints pass semantic validation."""
        data = [
            {
                "id": "WP-1",
                "title": "First",
                "objective": "First objective",
                "acceptance_criteria": ["Works"],
            },
            {
                "id": "WP-2",
                "title": "Second",
                "objective": "Second objective",
                "acceptance_criteria": ["Works"],
                "dependencies": ["WP-1"],
            },
        ]
        errors = validate_semantics(data)
        assert errors == []

    def test_duplicate_ids(self) -> None:
        """Detect duplicate waypoint IDs."""
        data = [
            {
                "id": "WP-1",
                "title": "First",
                "objective": "First",
                "acceptance_criteria": ["Works"],
            },
            {
                "id": "WP-1",  # Duplicate!
                "title": "Second",
                "objective": "Second",
                "acceptance_criteria": ["Works"],
            },
        ]
        errors = validate_semantics(data)
        assert len(errors) > 0
        assert any("Duplicate" in e for e in errors)

    def test_invalid_parent_ref(self) -> None:
        """Detect invalid parent_id reference."""
        data = [
            {
                "id": "WP-1a",
                "title": "Child",
                "objective": "Child waypoint",
                "acceptance_criteria": ["Works"],
                "parent_id": "WP-999",  # Doesn't exist
            }
        ]
        errors = validate_semantics(data)
        assert len(errors) > 0
        assert any("parent_id" in e and "not found" in e for e in errors)

    def test_valid_parent_ref_existing(self) -> None:
        """Valid parent_id referencing existing waypoint."""
        data = [
            {
                "id": "WP-1a",
                "title": "Child",
                "objective": "Child waypoint",
                "acceptance_criteria": ["Works"],
                "parent_id": "WP-1",
            }
        ]
        # Parent exists in existing_ids
        errors = validate_semantics(data, existing_ids={"WP-1"})
        assert errors == []

    def test_valid_parent_ref_in_batch(self) -> None:
        """Valid parent_id referencing waypoint in same batch."""
        data = [
            {
                "id": "WP-1",
                "title": "Parent",
                "objective": "Parent waypoint",
                "acceptance_criteria": ["Works"],
            },
            {
                "id": "WP-1a",
                "title": "Child",
                "objective": "Child waypoint",
                "acceptance_criteria": ["Works"],
                "parent_id": "WP-1",
            },
        ]
        errors = validate_semantics(data)
        assert errors == []

    def test_invalid_dependency_ref(self) -> None:
        """Detect invalid dependency reference."""
        data = [
            {
                "id": "WP-1",
                "title": "Waypoint",
                "objective": "Depends on nonexistent",
                "acceptance_criteria": ["Works"],
                "dependencies": ["WP-999"],  # Doesn't exist
            }
        ]
        errors = validate_semantics(data)
        assert len(errors) > 0
        assert any("dependency" in e and "not found" in e for e in errors)

    def test_valid_dependency_ref(self) -> None:
        """Valid dependency referencing existing waypoint."""
        data = [
            {
                "id": "WP-2",
                "title": "Depends on WP-1",
                "objective": "This depends on WP-1",
                "acceptance_criteria": ["Works"],
                "dependencies": ["WP-1"],
            }
        ]
        errors = validate_semantics(data, existing_ids={"WP-1"})
        assert errors == []


class TestValidateWaypoints:
    """Tests for full validation pipeline."""

    def test_valid_response(self) -> None:
        """Valid response passes all validation."""
        response = """[
            {
                "id": "WP-1",
                "title": "First waypoint",
                "objective": "Implement the first feature",
                "acceptance_criteria": ["Feature works", "Tests pass"]
            }
        ]"""
        result = validate_waypoints(response)
        assert result.valid is True
        assert result.data is not None
        assert len(result.data) == 1

    def test_invalid_json(self) -> None:
        """Invalid JSON returns validation error."""
        response = "not json at all"
        result = validate_waypoints(response)
        assert result.valid is False
        assert "No JSON array" in result.errors[0]

    def test_schema_error(self) -> None:
        """Schema violation returns validation error."""
        response = (
            '[{"id": "BAD-ID", "title": "X", '
            '"objective": "Y", "acceptance_criteria": []}]'
        )
        result = validate_waypoints(response)
        assert result.valid is False
        assert len(result.errors) > 0

    def test_semantic_error(self) -> None:
        """Semantic violation returns validation error."""
        response = """[
            {"id": "WP-1", "title": "First",
             "objective": "First objective", "acceptance_criteria": ["OK"]},
            {"id": "WP-1", "title": "Duplicate",
             "objective": "Duplicate ID", "acceptance_criteria": ["OK"]}
        ]"""
        result = validate_waypoints(response)
        assert result.valid is False
        assert any("Duplicate" in e for e in result.errors)

    def test_with_existing_ids(self) -> None:
        """Validation with existing IDs for sub-waypoints."""
        response = """[
            {
                "id": "WP-1a",
                "title": "Sub waypoint",
                "objective": "Sub waypoint objective here",
                "acceptance_criteria": ["Works"],
                "parent_id": "WP-1"
            }
        ]"""
        # Without existing_ids, parent_id validation would fail
        result = validate_waypoints(response, existing_ids={"WP-1"})
        assert result.valid is True


class TestWaypointValidationError:
    """Tests for the validation error exception."""

    def test_error_message(self) -> None:
        """Error message includes errors list."""
        error = WaypointValidationError(["Error 1", "Error 2"])
        assert "Error 1" in str(error)
        assert error.errors == ["Error 1", "Error 2"]

    def test_can_be_raised_caught(self) -> None:
        """Error can be raised and caught properly."""
        with pytest.raises(WaypointValidationError) as exc_info:
            raise WaypointValidationError(["Test error"])
        assert exc_info.value.errors == ["Test error"]
