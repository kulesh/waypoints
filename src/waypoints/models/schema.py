"""Schema versioning for JSONL persistence files.

Provides version headers for all JSONL files to enable:
- Format validation on load
- Automatic migration of legacy files
- Future-proof persistence format

Schema Types:
- flight_plan: Flight plan with waypoints
- session: Dialogue session history
- execution_log: Waypoint execution logs
- metrics: LLM call metrics
- genspec: Generative specification (prompt sequences)
"""
from __future__ import annotations

import json
import logging
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

# Current schema versions
CURRENT_VERSIONS: dict[str, str] = {
    "flight_plan": "1.0",
    "session": "1.0",
    "execution_log": "1.0",
    "metrics": "1.0",
    "genspec": "1.0",
}


class SchemaError(Exception):
    """Base exception for schema-related errors."""

    pass


class MigrationNotFoundError(SchemaError):
    """Raised when no migration path exists."""

    def __init__(self, schema_type: str, from_version: str, to_version: str) -> None:
        self.schema_type = schema_type
        self.from_version = from_version
        self.to_version = to_version
        super().__init__(
            f"No migration for {schema_type} from {from_version} to {to_version}"
        )


class InvalidSchemaError(SchemaError):
    """Raised when schema validation fails."""

    def __init__(self, path: Path, message: str) -> None:
        self.path = path
        super().__init__(f"Invalid schema in {path}: {message}")


@dataclass
class SchemaHeader:
    """Parsed schema header from JSONL file."""

    schema_type: str
    schema_version: str

    @property
    def is_legacy(self) -> bool:
        """Check if this is a legacy file (version 0.0)."""
        return self.schema_version == "0.0"

    @property
    def is_current(self) -> bool:
        """Check if this file is at the current version."""
        current = CURRENT_VERSIONS.get(self.schema_type)
        return self.schema_version == current


def read_schema_header(path: Path, expected_type: str) -> SchemaHeader:
    """Read and validate schema header from JSONL file.

    Args:
        path: Path to the JSONL file.
        expected_type: The expected schema type (e.g., "flight_plan").

    Returns:
        SchemaHeader with version "0.0" for legacy files without schema fields.

    Raises:
        InvalidSchemaError: If the file is corrupt or has wrong schema type.
    """
    if not path.exists():
        raise InvalidSchemaError(path, "File does not exist")

    try:
        with open(path) as f:
            first_line = f.readline().strip()
            if not first_line:
                # Empty file - treat as legacy
                return SchemaHeader(schema_type=expected_type, schema_version="0.0")

            data = json.loads(first_line)
    except json.JSONDecodeError as e:
        raise InvalidSchemaError(path, f"Invalid JSON in header: {e}") from e

    # Check for schema fields
    schema_type = data.get("_schema")
    schema_version = data.get("_version")

    if schema_type is None or schema_version is None:
        # Legacy file without schema fields
        logger.debug("Legacy file detected (no schema fields): %s", path)
        return SchemaHeader(schema_type=expected_type, schema_version="0.0")

    # Validate schema type matches expected
    if schema_type != expected_type:
        raise InvalidSchemaError(
            path, f"Expected schema '{expected_type}', got '{schema_type}'"
        )

    return SchemaHeader(schema_type=schema_type, schema_version=schema_version)


def write_schema_fields(schema_type: str) -> dict[str, str]:
    """Get schema fields to include in header.

    Args:
        schema_type: The schema type (e.g., "flight_plan").

    Returns:
        Dict with _schema and _version fields.

    Raises:
        ValueError: If schema_type is unknown.
    """
    if schema_type not in CURRENT_VERSIONS:
        raise ValueError(f"Unknown schema type: {schema_type}")

    return {
        "_schema": schema_type,
        "_version": CURRENT_VERSIONS[schema_type],
    }


# Migration registry
Migrator = Callable[[Path], None]
MIGRATORS: dict[tuple[str, str, str], Migrator] = {}


def register_migrator(
    schema_type: str, from_version: str, to_version: str
) -> Callable[[Migrator], Migrator]:
    """Decorator to register a migration function.

    Args:
        schema_type: The schema type this migration applies to.
        from_version: Source version.
        to_version: Target version.

    Example:
        @register_migrator("flight_plan", "1.0", "2.0")
        def migrate_flight_plan_1_to_2(path: Path) -> None:
            ...
    """

    def decorator(fn: Migrator) -> Migrator:
        key = (schema_type, from_version, to_version)
        MIGRATORS[key] = fn
        logger.debug(
            "Registered migrator: %s %s -> %s", schema_type, from_version, to_version
        )
        return fn

    return decorator


def migrate_if_needed(path: Path, schema_type: str) -> bool:
    """Migrate file to current version if needed.

    Uses atomic write (write to temp file, then rename) to prevent corruption.

    Args:
        path: Path to the JSONL file.
        schema_type: The expected schema type.

    Returns:
        True if migration was performed, False if already current.

    Raises:
        MigrationNotFoundError: If no migration path exists.
        InvalidSchemaError: If the file is corrupt.
    """
    if not path.exists():
        return False

    header = read_schema_header(path, schema_type)

    if header.is_current:
        return False

    current_version = CURRENT_VERSIONS[schema_type]
    migrator_key = (schema_type, header.schema_version, current_version)

    migrator = MIGRATORS.get(migrator_key)
    if migrator is None:
        raise MigrationNotFoundError(
            schema_type, header.schema_version, current_version
        )

    logger.info(
        "Migrating %s from %s to %s: %s",
        schema_type,
        header.schema_version,
        current_version,
        path,
    )
    migrator(path)
    return True


def _atomic_rewrite(path: Path, transform: Callable[[list[str]], list[str]]) -> None:
    """Atomically rewrite a file by transforming its lines.

    Args:
        path: Path to the file.
        transform: Function that takes list of lines and returns transformed lines.
    """
    # Read all lines
    with open(path) as f:
        lines = f.readlines()

    # Transform
    new_lines = transform(lines)

    # Write to temp file, then rename (atomic on most filesystems)
    temp_path = Path(tempfile.mktemp(dir=path.parent, suffix=".tmp"))
    try:
        with open(temp_path, "w") as f:
            f.writelines(new_lines)
        temp_path.replace(path)
    except Exception:
        # Clean up temp file on error
        if temp_path.exists():
            temp_path.unlink()
        raise


# =============================================================================
# Legacy Migrations (0.0 -> 1.0)
# =============================================================================


@register_migrator("flight_plan", "0.0", "1.0")
def _migrate_flight_plan_legacy(path: Path) -> None:
    """Add schema fields to legacy flight plan.

    Legacy format has header: {"created_at": ..., "updated_at": ...}
    New format adds: {"_schema": "flight_plan", "_version": "1.0", ...}
    """

    def transform(lines: list[str]) -> list[str]:
        if not lines:
            return lines

        # Parse and update header
        header_data = json.loads(lines[0])
        header_data = {
            **write_schema_fields("flight_plan"),
            **header_data,
        }
        lines[0] = json.dumps(header_data) + "\n"
        return lines

    _atomic_rewrite(path, transform)


@register_migrator("session", "0.0", "1.0")
def _migrate_session_legacy(path: Path) -> None:
    """Add schema fields to legacy session.

    Legacy format has header: {"session_id": ..., "phase": ..., ...}
    New format adds: {"_schema": "session", "_version": "1.0", ...}
    """

    def transform(lines: list[str]) -> list[str]:
        if not lines:
            return lines

        # Parse and update header
        header_data = json.loads(lines[0])
        header_data = {
            **write_schema_fields("session"),
            **header_data,
        }
        lines[0] = json.dumps(header_data) + "\n"
        return lines

    _atomic_rewrite(path, transform)


@register_migrator("execution_log", "0.0", "1.0")
def _migrate_execution_log_legacy(path: Path) -> None:
    """Add schema fields to legacy execution log.

    Legacy format has header: {"type": "header", "execution_id": ..., ...}
    New format adds: {"type": "header", "_schema": "execution_log",
    "_version": "1.0", ...}
    """

    def transform(lines: list[str]) -> list[str]:
        if not lines:
            return lines

        # Parse and update header
        header_data = json.loads(lines[0])
        # Keep "type" first for readability
        new_header = {"type": header_data.pop("type", "header")}
        new_header.update(write_schema_fields("execution_log"))
        new_header.update(header_data)
        lines[0] = json.dumps(new_header) + "\n"
        return lines

    _atomic_rewrite(path, transform)


@register_migrator("metrics", "0.0", "1.0")
def _migrate_metrics_legacy(path: Path) -> None:
    """Add header to metrics file.

    Legacy format has no header, starts directly with LLMCall entries.
    New format adds header: {"_schema": "metrics", "_version": "1.0", "created_at": ...}
    """

    def transform(lines: list[str]) -> list[str]:
        # Create header with current timestamp
        header = {
            **write_schema_fields("metrics"),
            "created_at": datetime.now(UTC).isoformat(),
        }
        # Prepend header
        return [json.dumps(header) + "\n"] + lines

    _atomic_rewrite(path, transform)
