"""Mixin for @waypoints mention processing in document screens."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, cast, runtime_checkable

from textual import work
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import TextArea

from waypoints.llm.client import ChatClient, StreamChunk
from waypoints.mentions import (
    CommentLogEntry,
    Mention,
    ProcessingResult,
    find_mentions,
    log_comment,
    mark_mention_resolved,
    process_mention,
    replace_section,
    save_document_version,
)

if TYPE_CHECKING:
    from waypoints.tui.app import WaypointsApp

logger = logging.getLogger(__name__)


@runtime_checkable
class MentionCapableScreen(Protocol):
    """Protocol for screens that support @waypoints mentions.

    Screens using MentionProcessingMixin must implement these properties/methods.
    """

    @property
    def document_content(self) -> str:
        """Get current document content."""
        ...

    @document_content.setter
    def document_content(self, value: str) -> None:
        """Set document content."""
        ...

    @property
    def document_type(self) -> str:
        """Get document type: 'idea-brief' or 'product-spec'."""
        ...

    def _get_docs_path(self) -> Path:
        """Get path to the docs directory."""
        ...

    def _update_mention_display(self, content: str) -> None:
        """Update the document display widget with new content."""
        ...

    def notify(
        self,
        message: str,
        *,
        title: str = "",
        severity: str = "information",
        timeout: float = 5.0,
    ) -> None:
        """Show notification to user."""
        ...


class MentionProcessingMixin:
    """Mixin that adds @waypoints mention processing to document screens.

    Usage:
        class MyScreen(Screen, MentionProcessingMixin):
            BINDINGS = [
                *MentionProcessingMixin.MENTION_BINDINGS,
                # ... other bindings
            ]

            @property
            def document_content(self) -> str:
                return self._content

            @document_content.setter
            def document_content(self, value: str) -> None:
                self._content = value

            @property
            def document_type(self) -> str:
                return "idea-brief"

            def _get_docs_path(self) -> Path:
                return self.project.get_docs_path()

            def _update_mention_display(self, content: str) -> None:
                from textual.widgets import Markdown
                self.query_one("#display", Markdown).update(content)
    """

    MENTION_BINDINGS = [
        Binding("ctrl+r", "process_mentions", "Process @waypoints", show=True),
    ]

    # Instance state for mention processing
    _mentions_processing: bool = False
    _pending_mentions: list[Mention]
    _current_mention_index: int = 0
    _llm_client: ChatClient | None = None

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """Ensure subclasses initialize mixin state."""
        super().__init_subclass__(**kwargs)

    def _init_mention_state(self) -> None:
        """Initialize mention processing state. Call in __init__ or on_mount."""
        self._mentions_processing = False
        self._pending_mentions = []
        self._current_mention_index = 0
        self._llm_client = None

    def _get_llm_client(self) -> ChatClient:
        """Get or create the LLM client for mention processing."""
        if self._llm_client is None:
            # Get app and metrics collector
            screen = cast(Screen[Any], self)
            app = cast("WaypointsApp", screen.app)

            self._llm_client = ChatClient(
                metrics_collector=app.metrics_collector,
                phase="mentions",
            )
        return self._llm_client

    def action_process_mentions(self) -> None:
        """Start processing @waypoints mentions in the document.

        Assumes self also implements MentionCapableScreen protocol.
        """
        # Cast to access protocol methods (mixin assumes protocol is implemented)
        screen = cast(Any, self)

        if self._mentions_processing:
            screen.notify("Already processing mentions", severity="warning")
            return

        # Find all unresolved mentions
        mentions = find_mentions(screen.document_content)
        if not mentions:
            screen.notify("No @waypoints mentions found")
            return

        self._pending_mentions = mentions
        self._current_mention_index = 0
        self._mentions_processing = True

        screen.notify(f"Processing {len(mentions)} mention(s)...")
        logger.info(
            "Starting mention processing: %d mentions in %s",
            len(mentions),
            screen.document_type,
        )

        self._process_next_mention()

    @work(thread=True)
    def _process_next_mention(self) -> None:
        """Process the next pending mention in a background thread."""
        screen = cast(Any, self)

        if self._current_mention_index >= len(self._pending_mentions):
            # All done
            cast(Screen[Any], self).app.call_from_thread(
                self._finalize_mention_processing
            )
            return

        mention = self._pending_mentions[self._current_mention_index]
        logger.info(
            "Processing mention %d/%d: %s",
            self._current_mention_index + 1,
            len(self._pending_mentions),
            mention.instruction[:50],
        )

        # Get current document state (may have been updated by previous mentions)
        current_doc = screen.document_content

        # Process with LLM
        client = self._get_llm_client()
        updated_section: str = ""
        success = False
        error_msg: str | None = None

        try:
            for result in process_mention(mention, current_doc, client):
                if isinstance(result, StreamChunk):
                    # Accumulate response
                    updated_section += result.text
                elif isinstance(result, ProcessingResult):
                    success = result.success
                    if success:
                        updated_section = result.updated_section or ""
                    else:
                        error_msg = result.error
        except Exception as e:
            logger.exception("Error processing mention: %s", e)
            success = False
            error_msg = str(e)

        # Apply results on main thread
        if success and updated_section:
            cast(Screen[Any], self).app.call_from_thread(
                self._apply_mention_result,
                mention,
                updated_section,
            )
        else:
            cast(Screen[Any], self).app.call_from_thread(
                self._handle_mention_error,
                mention,
                error_msg or "Unknown error",
            )

        # Process next mention
        self._current_mention_index += 1
        self._process_next_mention()

    def _apply_mention_result(
        self,
        mention: Mention,
        updated_section: str,
    ) -> None:
        """Apply the LLM result to the document (runs on main thread)."""
        screen = cast(Any, self)
        current_doc = screen.document_content
        lines_before = mention.section_end - mention.section_start

        # 1. Replace section content
        new_doc = replace_section(current_doc, mention, updated_section)

        # 2. Mark mention as resolved
        # Note: Line numbers may have shifted, so we need to find the mention again
        # For now, we mark it resolved in the updated section
        new_doc = mark_mention_resolved(new_doc, mention)

        # 3. Update document content and display
        screen.document_content = new_doc
        screen._update_mention_display(new_doc)

        # Also update TextArea if present
        try:
            # Try common TextArea IDs
            for editor_id in ["brief-editor", "spec-editor"]:
                try:
                    editor = screen.query_one(f"#{editor_id}", TextArea)
                    editor.text = new_doc
                    break
                except Exception:
                    pass
        except Exception:
            pass

        # 4. Count lines in new section
        new_section_lines = updated_section.count("\n") + 1
        lines_after = new_section_lines

        # 5. Log the comment
        entry = CommentLogEntry(
            timestamp=_get_timestamp(),
            section=mention.section_heading or "(preamble)",
            instruction=mention.instruction,
            lines_before=lines_before,
            lines_after=lines_after,
        )
        log_comment(screen._get_docs_path(), screen.document_type, entry)

        logger.info(
            "Applied mention result: %d -> %d lines in section '%s'",
            lines_before,
            lines_after,
            mention.section_heading or "(preamble)",
        )

    def _handle_mention_error(
        self,
        mention: Mention,
        error: str,
    ) -> None:
        """Handle error processing a mention (runs on main thread)."""
        screen = cast(Any, self)
        logger.error(
            "Failed to process mention in section '%s': %s",
            mention.section_heading or "(preamble)",
            error,
        )
        screen.notify(
            f"Error processing mention: {error[:50]}",
            severity="error",
        )

    def _finalize_mention_processing(self) -> None:
        """Called when all mentions have been processed (runs on main thread)."""
        screen = cast(Any, self)
        count = len(self._pending_mentions)
        self._mentions_processing = False
        self._pending_mentions = []
        self._current_mention_index = 0

        # Save new document version
        docs_path = screen._get_docs_path()
        save_document_version(docs_path, screen.document_type, screen.document_content)

        screen.notify(f"Processed {count} mention(s) and saved new version")
        logger.info(
            "Completed mention processing: %d mentions in %s",
            count,
            screen.document_type,
        )


def _get_timestamp() -> str:
    """Get current timestamp in ISO format."""
    from datetime import UTC, datetime

    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")
