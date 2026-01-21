"""Session persistence for dialogue history in JSONL format."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from waypoints.models.dialogue import DialogueHistory, Message
from waypoints.models.schema import migrate_if_needed, write_schema_fields

if TYPE_CHECKING:
    from waypoints.models.project import Project


class SessionWriter:
    """Appends messages to a JSONL file as they arrive.

    JSONL format allows streaming writes and easy parsing:
    - First line: session header with metadata
    - Subsequent lines: individual messages
    """

    def __init__(self, project: "Project", phase: str, session_id: str) -> None:
        """Initialize session writer.

        Args:
            project: The project this session belongs to
            phase: The phase name (e.g., "ideation")
            session_id: Unique session identifier
        """
        self.project = project
        self.phase = phase
        self.session_id = session_id
        self.file_path = self._generate_path()
        self._write_header()

    def _generate_path(self) -> Path:
        """Generate the JSONL file path for this session."""
        timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        return self.project.get_sessions_path() / f"{self.phase}-{timestamp}.jsonl"

    def _write_header(self) -> None:
        """Write the session header as the first line."""
        header = {
            **write_schema_fields("session"),
            "session_id": self.session_id,
            "phase": self.phase,
            "created_at": datetime.now(UTC).isoformat(),
            "project_slug": self.project.slug,
        }
        self.project.get_sessions_path().mkdir(parents=True, exist_ok=True)
        with open(self.file_path, "w", encoding="utf-8") as f:
            f.write(json.dumps(header) + "\n")

    def append_message(self, message: Message) -> None:
        """Append a single message to the JSONL file."""
        with open(self.file_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(message.to_dict()) + "\n")


class SessionReader:
    """Reads and reconstructs DialogueHistory from JSONL files."""

    @classmethod
    def load(cls, file_path: Path) -> DialogueHistory:
        """Load a session from a JSONL file.

        Automatically migrates legacy files to current schema version.

        Args:
            file_path: Path to the JSONL file

        Returns:
            Reconstructed DialogueHistory with all messages
        """
        # Migrate legacy files if needed
        migrate_if_needed(file_path, "session")

        history = DialogueHistory()

        with open(file_path, encoding="utf-8") as f:
            for line_num, line in enumerate(f):
                line = line.strip()
                if not line:
                    continue

                data = json.loads(line)

                if line_num == 0 and "session_id" in data:
                    # First line is the header (may include _schema, _version)
                    history.session_id = data["session_id"]
                    history.phase = data.get("phase", "")
                else:
                    # Message line
                    history.messages.append(Message.from_dict(data))

        return history

    @classmethod
    def list_sessions(cls, project: "Project", phase: str | None = None) -> list[Path]:
        """List all session files for a project.

        Args:
            project: The project to list sessions for
            phase: Optional phase filter (e.g., "ideation")

        Returns:
            List of session file paths, sorted by modification time (newest first)
        """
        sessions_dir = project.get_sessions_path()
        if not sessions_dir.exists():
            return []

        pattern = f"{phase}-*.jsonl" if phase else "*.jsonl"
        files = list(sessions_dir.glob(pattern))
        return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)

    @classmethod
    def load_latest(
        cls, project: "Project", phase: str | None = None
    ) -> DialogueHistory | None:
        """Load the most recent session for a project.

        Args:
            project: The project to load from
            phase: Optional phase filter

        Returns:
            DialogueHistory if a session exists, None otherwise
        """
        sessions = cls.list_sessions(project, phase)
        if not sessions:
            return None
        return cls.load(sessions[0])
