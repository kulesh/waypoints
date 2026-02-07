"""Iteration request persistence for LAND iterate flow."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

if TYPE_CHECKING:
    from waypoints.models.project import Project


class IterationIntent(str, Enum):
    """Classified intent for an iterate request."""

    BUG_FIX = "bug_fix"
    IMPROVEMENT = "improvement"
    BUG_FIX_AND_IMPROVEMENT = "bug_fix_and_improvement"
    UNKNOWN = "unknown"


class IterationRequestStatus(str, Enum):
    """Lifecycle status for an iterate request."""

    SUBMITTED = "submitted"
    WAYPOINT_DRAFTED = "waypoint_drafted"
    WAYPOINT_ADDED = "waypoint_added"
    CANCELLED = "cancelled"
    GENERATION_FAILED = "generation_failed"


@dataclass(frozen=True, slots=True)
class IterationTriage:
    """Triage output used for waypoint shaping and lineage."""

    intent: IterationIntent
    confidence: float
    summary: str
    rationale: str
    source: str = "heuristic"

    def to_dict(self) -> dict[str, Any]:
        """Convert triage data to a JSON-serializable dictionary."""
        return {
            "intent": self.intent.value,
            "confidence": self.confidence,
            "summary": self.summary,
            "rationale": self.rationale,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "IterationTriage":
        """Build triage data from serialized content."""
        try:
            intent = IterationIntent(str(data.get("intent", IterationIntent.UNKNOWN)))
        except ValueError:
            intent = IterationIntent.UNKNOWN

        raw_confidence = data.get("confidence", 0.0)
        try:
            confidence = float(raw_confidence)
        except (TypeError, ValueError):
            confidence = 0.0

        return cls(
            intent=intent,
            confidence=max(0.0, min(1.0, confidence)),
            summary=str(data.get("summary", "")).strip(),
            rationale=str(data.get("rationale", "")).strip(),
            source=str(data.get("source", "heuristic")),
        )


@dataclass(frozen=True, slots=True)
class IterationAttachmentRecord:
    """Attachment metadata captured for iteration lineage."""

    relative_path: str
    sha256: str
    size_bytes: int
    source_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        data: dict[str, Any] = {
            "relative_path": self.relative_path,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
        }
        if self.source_path:
            data["source_path"] = self.source_path
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "IterationAttachmentRecord":
        """Create from serialized attachment metadata."""
        return cls(
            relative_path=str(data.get("relative_path", "")),
            sha256=str(data.get("sha256", "")),
            size_bytes=int(data.get("size_bytes", 0)),
            source_path=(
                str(data["source_path"])
                if data.get("source_path") is not None
                else None
            ),
        )


@dataclass(frozen=True, slots=True)
class IterationRequestRecord:
    """Materialized lifecycle record for a single iterate request."""

    request_id: str
    prompt: str
    triage: IterationTriage
    created_at: datetime
    updated_at: datetime
    attachments: tuple[IterationAttachmentRecord, ...] = ()
    status: IterationRequestStatus = IterationRequestStatus.SUBMITTED
    draft_waypoint_id: str | None = None
    linked_waypoint_id: str | None = None
    insert_after: str | None = None
    error_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert request record to dictionary for serialization."""
        data: dict[str, Any] = {
            "request_id": self.request_id,
            "prompt": self.prompt,
            "triage": self.triage.to_dict(),
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "attachments": [item.to_dict() for item in self.attachments],
            "status": self.status.value,
        }
        if self.draft_waypoint_id:
            data["draft_waypoint_id"] = self.draft_waypoint_id
        if self.linked_waypoint_id:
            data["linked_waypoint_id"] = self.linked_waypoint_id
        if self.insert_after is not None:
            data["insert_after"] = self.insert_after
        if self.error_message:
            data["error_message"] = self.error_message
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "IterationRequestRecord":
        """Create request record from serialized content."""
        try:
            status = IterationRequestStatus(
                str(data.get("status", IterationRequestStatus.SUBMITTED))
            )
        except ValueError:
            status = IterationRequestStatus.SUBMITTED

        created_raw = data.get("created_at", datetime.now(UTC).isoformat())
        updated_raw = data.get("updated_at", created_raw)
        return cls(
            request_id=str(data["request_id"]),
            prompt=str(data.get("prompt", "")),
            triage=IterationTriage.from_dict(
                data.get("triage", {"intent": IterationIntent.UNKNOWN.value})
            ),
            created_at=datetime.fromisoformat(str(created_raw)),
            updated_at=datetime.fromisoformat(str(updated_raw)),
            attachments=tuple(
                IterationAttachmentRecord.from_dict(item)
                for item in data.get("attachments", [])
                if isinstance(item, dict)
            ),
            status=status,
            draft_waypoint_id=(
                str(data["draft_waypoint_id"])
                if data.get("draft_waypoint_id") is not None
                else None
            ),
            linked_waypoint_id=(
                str(data["linked_waypoint_id"])
                if data.get("linked_waypoint_id") is not None
                else None
            ),
            insert_after=(
                str(data["insert_after"])
                if data.get("insert_after") is not None
                else None
            ),
            error_message=(
                str(data["error_message"])
                if data.get("error_message") is not None
                else None
            ),
        )


class IterationRequestWriter:
    """Append-only writer for iterate request lifecycle snapshots."""

    def __init__(self, project: "Project") -> None:
        self.project = project
        self.file_path = self._get_path()

    def _get_path(self) -> Path:
        chart_dir = self.project.get_sessions_path() / "chart"
        chart_dir.mkdir(parents=True, exist_ok=True)
        return chart_dir / "iteration_requests.jsonl"

    def _append(self, record: IterationRequestRecord) -> None:
        with open(self.file_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record.to_dict()) + "\n")

    def log_submitted(
        self,
        prompt: str,
        triage: IterationTriage,
        attachments: Sequence[IterationAttachmentRecord] = (),
    ) -> IterationRequestRecord:
        """Create a new iterate request record."""
        now = datetime.now(UTC)
        request_id = f"ir-{uuid4().hex[:12]}"
        record = IterationRequestRecord(
            request_id=request_id,
            prompt=prompt,
            triage=triage,
            created_at=now,
            updated_at=now,
            attachments=tuple(attachments),
            status=IterationRequestStatus.SUBMITTED,
        )
        self._append(record)
        return record

    def log_waypoint_drafted(
        self,
        request_id: str,
        draft_waypoint_id: str,
        insert_after: str | None,
    ) -> IterationRequestRecord:
        """Mark request as drafted into a waypoint preview."""
        return self._update(
            request_id,
            lambda record, now: replace(
                record,
                status=IterationRequestStatus.WAYPOINT_DRAFTED,
                updated_at=now,
                draft_waypoint_id=draft_waypoint_id,
                insert_after=insert_after,
                error_message=None,
            ),
        )

    def log_waypoint_added(
        self,
        request_id: str,
        waypoint_id: str,
        insert_after: str | None,
    ) -> IterationRequestRecord:
        """Mark request as confirmed and linked to a waypoint in the plan."""
        return self._update(
            request_id,
            lambda record, now: replace(
                record,
                status=IterationRequestStatus.WAYPOINT_ADDED,
                updated_at=now,
                linked_waypoint_id=waypoint_id,
                draft_waypoint_id=record.draft_waypoint_id or waypoint_id,
                insert_after=insert_after,
                error_message=None,
            ),
        )

    def log_cancelled(self, request_id: str) -> IterationRequestRecord:
        """Mark request as cancelled by the user."""
        return self._update(
            request_id,
            lambda record, now: replace(
                record,
                status=IterationRequestStatus.CANCELLED,
                updated_at=now,
            ),
        )

    def log_generation_failed(
        self,
        request_id: str,
        error_message: str,
    ) -> IterationRequestRecord:
        """Mark request as failed before waypoint generation completed."""
        return self._update(
            request_id,
            lambda record, now: replace(
                record,
                status=IterationRequestStatus.GENERATION_FAILED,
                updated_at=now,
                error_message=error_message,
            ),
        )

    def _update(
        self,
        request_id: str,
        updater: Callable[
            [IterationRequestRecord, datetime],
            IterationRequestRecord,
        ],
    ) -> IterationRequestRecord:
        current = IterationRequestReader.load_map(self.project).get(request_id)
        if current is None:
            raise KeyError(f"Unknown iteration request: {request_id}")
        updated = updater(current, datetime.now(UTC))
        self._append(updated)
        return updated


class IterationRequestReader:
    """Reader for iterate request lifecycle snapshots."""

    @classmethod
    def _get_path(cls, project: "Project") -> Path:
        return project.get_sessions_path() / "chart" / "iteration_requests.jsonl"

    @classmethod
    def load_history(cls, project: "Project") -> list[IterationRequestRecord]:
        """Load append-only request snapshots in chronological order."""
        file_path = cls._get_path(project)
        if not file_path.exists():
            return []

        records: list[IterationRequestRecord] = []
        with open(file_path, encoding="utf-8") as handle:
            for line in handle:
                payload = line.strip()
                if not payload:
                    continue
                data = json.loads(payload)
                if not isinstance(data, dict):
                    continue
                records.append(IterationRequestRecord.from_dict(data))
        return records

    @classmethod
    def load_map(cls, project: "Project") -> dict[str, IterationRequestRecord]:
        """Load latest snapshot per request ID."""
        latest: dict[str, IterationRequestRecord] = {}
        for record in cls.load_history(project):
            latest[record.request_id] = record
        return latest

    @classmethod
    def load(cls, project: "Project") -> list[IterationRequestRecord]:
        """Load latest request snapshots preserving first-seen request order."""
        order: list[str] = []
        latest: dict[str, IterationRequestRecord] = {}

        for record in cls.load_history(project):
            if record.request_id not in latest:
                order.append(record.request_id)
            latest[record.request_id] = record

        return [latest[request_id] for request_id in order]
