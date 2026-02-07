"""Helpers for LAND iteration request intake and evidence handling."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import shlex
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from waypoints.llm import ChatClient, StreamChunk
from waypoints.models.iteration_request import (
    IterationAttachmentRecord,
    IterationIntent,
    IterationTriage,
)

_TRAILING_PUNCTUATION = ".,;:!?)]}\"'"
_WINDOWS_DRIVE_PREFIX = re.compile(r"^[A-Za-z]:[\\/]")
_BUG_KEYWORDS = (
    "bug",
    "broken",
    "breaks",
    "error",
    "exception",
    "traceback",
    "fail",
    "failing",
    "fails",
    "crash",
    "incorrect",
    "wrong",
    "fix",
    "regression",
    "not working",
)
_IMPROVEMENT_KEYWORDS = (
    "feature",
    "improve",
    "improvement",
    "enhance",
    "refactor",
    "optimiz",
    "cleanup",
    "polish",
    "add",
    "support",
    "iterate",
)
_TRIAGE_SYSTEM_PROMPT = (
    "You classify software iteration requests into bug-fix or improvement intent. "
    "Respond with valid JSON only."
)
_TRIAGE_PROMPT = """\
Classify this software iteration request.

Return a JSON object with:
- "intent": one of "bug_fix", "improvement", "bug_fix_and_improvement", "unknown"
- "confidence": number from 0.0 to 1.0
- "summary": one concise sentence
- "rationale": one concise sentence describing the signal

Request:
{request}

Attachments:
{attachments}
"""

logger = logging.getLogger(__name__)


def _looks_like_path(token: str) -> bool:
    """Heuristic for whether a token likely represents a filesystem path."""
    if "://" in token:
        return False
    if token.startswith(("~", "./", "../", "/")):
        return True
    if _WINDOWS_DRIVE_PREFIX.match(token) is not None:
        return True
    return "/" in token or "\\" in token


def _sha256(path: Path) -> str:
    """Compute SHA256 for a file."""
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


@dataclass(frozen=True, slots=True)
class IterationAttachment:
    """Materialized evidence attachment stored in project-local snapshot."""

    source_path: Path
    stored_path: Path
    relative_path: str
    sha256: str
    size_bytes: int

    def to_record(self) -> IterationAttachmentRecord:
        """Convert attachment metadata into persistence record format."""
        return IterationAttachmentRecord(
            relative_path=self.relative_path,
            sha256=self.sha256,
            size_bytes=self.size_bytes,
            source_path=str(self.source_path),
        )


class IterationRequestService:
    """Service for parsing iteration requests and managing evidence files."""

    def __init__(self, project_path: Path) -> None:
        self.project_path = project_path.resolve()
        self.evidence_dir = self.project_path / "evidence" / "iterations"

    @staticmethod
    def _extract_json_object(text: str) -> dict[str, Any]:
        """Extract first JSON object from text, handling markdown fences."""
        cleaned = re.sub(r"```json\s*", "", text, flags=re.IGNORECASE)
        cleaned = re.sub(r"```\s*", "", cleaned)
        decoder = json.JSONDecoder()
        for match in re.finditer(r"\{", cleaned):
            start = match.start()
            try:
                parsed, _ = decoder.raw_decode(cleaned[start:])
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed
        raise ValueError("No JSON object found in triage response")

    @staticmethod
    def _intent_from_llm(raw_intent: object) -> IterationIntent | None:
        if not isinstance(raw_intent, str):
            return None
        normalized = raw_intent.strip().lower()
        mapping = {
            IterationIntent.BUG_FIX.value: IterationIntent.BUG_FIX,
            IterationIntent.IMPROVEMENT.value: IterationIntent.IMPROVEMENT,
            IterationIntent.BUG_FIX_AND_IMPROVEMENT.value: (
                IterationIntent.BUG_FIX_AND_IMPROVEMENT
            ),
            IterationIntent.UNKNOWN.value: IterationIntent.UNKNOWN,
        }
        return mapping.get(normalized)

    @staticmethod
    def _short_summary(text: str) -> str:
        stripped = text.strip()
        if not stripped:
            return "Iteration request submitted"
        first_line = stripped.splitlines()[0].strip()
        return first_line[:160]

    def _classify_with_llm(
        self,
        request_text: str,
        attachments: list[IterationAttachment],
    ) -> IterationTriage | None:
        attachment_lines = (
            "\n".join(f"- {item.relative_path}" for item in attachments)
            if attachments
            else "- none"
        )
        prompt = _TRIAGE_PROMPT.format(
            request=request_text.strip(),
            attachments=attachment_lines,
        )

        response = ""
        client = ChatClient(phase="land-iterate-triage")
        for result in client.stream_message(
            messages=[{"role": "user", "content": prompt}],
            system=_TRIAGE_SYSTEM_PROMPT,
            max_tokens=220,
        ):
            if isinstance(result, StreamChunk):
                response += result.text

        parsed = self._extract_json_object(response)
        intent = self._intent_from_llm(parsed.get("intent"))
        if intent is None:
            return None

        try:
            raw_confidence = float(parsed.get("confidence", 0.0))
        except (TypeError, ValueError):
            raw_confidence = 0.0
        confidence = max(0.0, min(1.0, raw_confidence))

        summary = str(parsed.get("summary", "")).strip() or self._short_summary(
            request_text
        )
        rationale = str(parsed.get("rationale", "")).strip() or "LLM triage"
        return IterationTriage(
            intent=intent,
            confidence=confidence,
            summary=summary,
            rationale=rationale,
            source="llm",
        )

    @staticmethod
    def _count_keyword_hits(text: str, keywords: tuple[str, ...]) -> list[str]:
        lowered = text.lower()
        return [keyword for keyword in keywords if keyword in lowered]

    def _classify_with_heuristics(self, request_text: str) -> IterationTriage:
        bug_hits = self._count_keyword_hits(request_text, _BUG_KEYWORDS)
        improvement_hits = self._count_keyword_hits(request_text, _IMPROVEMENT_KEYWORDS)

        if bug_hits and improvement_hits:
            intent = IterationIntent.BUG_FIX_AND_IMPROVEMENT
            confidence = 0.72
        elif bug_hits:
            intent = IterationIntent.BUG_FIX
            confidence = 0.78
        elif improvement_hits:
            intent = IterationIntent.IMPROVEMENT
            confidence = 0.74
        else:
            intent = IterationIntent.UNKNOWN
            confidence = 0.42

        if bug_hits or improvement_hits:
            rationale = (
                f"keyword hits: bug={len(bug_hits)}, "
                f"improvement={len(improvement_hits)}"
            )
        else:
            rationale = "no strong bug-fix or improvement keywords detected"

        return IterationTriage(
            intent=intent,
            confidence=confidence,
            summary=self._short_summary(request_text),
            rationale=rationale,
            source="heuristic",
        )

    def classify_request(
        self,
        request_text: str,
        attachments: list[IterationAttachment],
    ) -> IterationTriage:
        """Classify request intent for bug-fix vs improvement routing."""
        try:
            triage = self._classify_with_llm(request_text, attachments)
            if triage is not None:
                return triage
        except Exception as exc:  # pragma: no cover - exercised via fallback behavior
            logger.info("LLM triage failed; falling back to heuristics: %s", exc)
        return self._classify_with_heuristics(request_text)

    def extract_existing_files(self, request_text: str) -> list[Path]:
        """Extract existing file paths mentioned in freeform request text."""
        try:
            raw_tokens = shlex.split(request_text, posix=True)
        except ValueError:
            raw_tokens = request_text.split()

        found: list[Path] = []
        seen: set[Path] = set()

        for raw in raw_tokens:
            token = raw.strip().strip("\"'").rstrip(_TRAILING_PUNCTUATION)
            if not token or not _looks_like_path(token):
                continue

            candidate = Path(token).expanduser()
            if not candidate.is_absolute():
                candidate = self.project_path / candidate
            resolved = candidate.resolve()
            if not resolved.exists() or not resolved.is_file() or resolved in seen:
                continue

            seen.add(resolved)
            found.append(resolved)

        return found

    def ingest_attachments(self, request_text: str) -> list[IterationAttachment]:
        """Copy mentioned files into project-local evidence storage."""
        attachments: list[IterationAttachment] = []
        self.evidence_dir.mkdir(parents=True, exist_ok=True)

        for source in self.extract_existing_files(request_text):
            digest = _sha256(source)
            stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
            filename = f"{stamp}-{digest[:10]}-{source.name}"
            stored = self.evidence_dir / filename
            shutil.copy2(source, stored)

            attachments.append(
                IterationAttachment(
                    source_path=source,
                    stored_path=stored,
                    relative_path=str(stored.relative_to(self.project_path)),
                    sha256=digest,
                    size_bytes=stored.stat().st_size,
                )
            )

        return attachments

    @staticmethod
    def build_waypoint_description(
        request_text: str,
        attachments: list[IterationAttachment],
        triage: IterationTriage | None = None,
    ) -> str:
        """Build a structured waypoint request from freeform text + attachments."""
        request = request_text.strip()
        lines: list[str] = [request]

        if triage is not None:
            lines.extend(
                [
                    "",
                    "Request triage:",
                    f"- intent: {triage.intent.value}",
                    f"- confidence: {triage.confidence:.2f}",
                    f"- summary: {triage.summary}",
                    f"- rationale: {triage.rationale}",
                ]
            )

        if not attachments:
            return "\n".join(lines).strip()

        lines.extend(["", "Evidence files (project-relative paths):"])
        for attachment in attachments:
            lines.append(
                "- "
                f"{attachment.relative_path} "
                f"(sha256={attachment.sha256[:12]}, bytes={attachment.size_bytes})"
            )

        lines.extend(
            [
                "",
                "Before proposing changes, inspect these evidence files with the Read "
                "tool and connect findings to acceptance criteria.",
            ]
        )
        return "\n".join(lines).strip()
