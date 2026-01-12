"""@waypoints mention parsing and processing.

Allows users to leave @waypoints: <instruction> comments in documents
that are processed by the LLM on demand.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from waypoints.llm.client import ChatClient, StreamChunk

logger = logging.getLogger(__name__)

# --- Patterns ---

# Matches @waypoints: followed by instruction text
MENTION_PATTERN = re.compile(
    r"^(\s*)@waypoints:\s*(.+)$",
    re.MULTILINE | re.IGNORECASE,
)

# Matches resolved mentions: [resolved]: # (@waypoints: ... - timestamp)
RESOLVED_PATTERN = re.compile(
    r"^\[resolved\]:\s*#\s*\(@waypoints:",
    re.MULTILINE | re.IGNORECASE,
)

# Matches markdown headings (# through ######)
HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)


# --- Data Structures ---


@dataclass
class DocumentSection:
    """A section of a markdown document."""

    heading: str | None  # The heading line (e.g., "## Problem Statement")
    content: str  # Full section content including heading
    start_line: int  # 0-indexed line where section starts
    end_line: int  # 0-indexed, exclusive


@dataclass
class Mention:
    """A single @waypoints mention in a document."""

    instruction: str  # Text after @waypoints:
    section_start: int  # Line where section starts (0-indexed)
    section_end: int  # Line where section ends (exclusive)
    mention_line: int  # Line of the @waypoints mention (0-indexed)
    original_section: str  # Section content before processing
    section_heading: str | None = None  # Heading text if present


@dataclass
class ProcessingResult:
    """Result of processing a single mention."""

    success: bool
    updated_section: str | None = None
    error: str | None = None


@dataclass
class CommentLogEntry:
    """Entry for comment history log."""

    timestamp: str
    section: str  # Heading text or "(preamble)"
    instruction: str
    lines_before: int
    lines_after: int


# --- Section Parsing ---


def parse_sections(document: str) -> list[DocumentSection]:
    """Parse a markdown document into sections based on headings.

    A section is a heading plus all content until the next heading (or EOF).
    Content before the first heading is the "preamble" section with heading=None.

    Args:
        document: The markdown document text.

    Returns:
        List of DocumentSection objects in document order.
    """
    lines = document.split("\n")
    sections: list[DocumentSection] = []

    # Find all heading positions
    heading_positions: list[tuple[int, str]] = []  # (line_index, heading_text)

    for i, line in enumerate(lines):
        match = HEADING_PATTERN.match(line)
        if match:
            heading_positions.append((i, line))

    # Build sections
    if not heading_positions:
        # No headings - entire document is one section
        return [
            DocumentSection(
                heading=None,
                content=document,
                start_line=0,
                end_line=len(lines),
            )
        ]

    # Handle preamble (content before first heading)
    first_heading_line = heading_positions[0][0]
    if first_heading_line > 0:
        preamble_content = "\n".join(lines[:first_heading_line])
        sections.append(
            DocumentSection(
                heading=None,
                content=preamble_content,
                start_line=0,
                end_line=first_heading_line,
            )
        )

    # Build sections from headings
    for i, (line_idx, heading) in enumerate(heading_positions):
        # Determine end of this section
        if i + 1 < len(heading_positions):
            end_line = heading_positions[i + 1][0]
        else:
            end_line = len(lines)

        section_content = "\n".join(lines[line_idx:end_line])
        sections.append(
            DocumentSection(
                heading=heading,
                content=section_content,
                start_line=line_idx,
                end_line=end_line,
            )
        )

    return sections


# --- Mention Finding ---


def find_mentions(document: str) -> list[Mention]:
    """Find all unresolved @waypoints mentions in a document.

    Skips already-resolved mentions (those in [resolved]: # format).
    Returns mentions in document order (top to bottom).

    Args:
        document: The markdown document text.

    Returns:
        List of Mention objects for unresolved mentions.
    """
    lines = document.split("\n")
    sections = parse_sections(document)
    mentions: list[Mention] = []

    for match in MENTION_PATTERN.finditer(document):
        # Get line number of this mention
        mention_start = match.start()
        mention_line = document[:mention_start].count("\n")

        # Check if this line is actually a resolved mention
        line_content = lines[mention_line]
        if RESOLVED_PATTERN.match(line_content):
            continue

        # Find which section contains this mention
        containing_section: DocumentSection | None = None
        for section in sections:
            if section.start_line <= mention_line < section.end_line:
                containing_section = section
                break

        if containing_section is None:
            # Shouldn't happen, but skip if it does
            logger.warning(
                "Could not find section for mention at line %d", mention_line
            )
            continue

        instruction = match.group(2).strip()

        mentions.append(
            Mention(
                instruction=instruction,
                section_start=containing_section.start_line,
                section_end=containing_section.end_line,
                mention_line=mention_line,
                original_section=containing_section.content,
                section_heading=containing_section.heading,
            )
        )

    return mentions


def is_mention_resolved(line: str) -> bool:
    """Check if a line contains a resolved mention."""
    return bool(RESOLVED_PATTERN.match(line))


# --- Mention Resolution ---


def mark_mention_resolved(document: str, mention: Mention) -> str:
    """Mark a mention as resolved in the document.

    Transforms:
        @waypoints: expand this section
    To:
        [resolved]: # (@waypoints: expand this section - 2026-01-11T14:30:22)

    Args:
        document: The document text.
        mention: The mention to mark resolved.

    Returns:
        Updated document with mention marked resolved.
    """
    lines = document.split("\n")
    original_line = lines[mention.mention_line]

    # Extract the @waypoints: ... part
    match = MENTION_PATTERN.match(original_line)
    if not match:
        # Line doesn't match pattern anymore (maybe already resolved)
        return document

    indent = match.group(1)
    instruction = match.group(2)
    timestamp = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    # Create resolved format
    resolved_line = f"{indent}[resolved]: # (@waypoints: {instruction} - {timestamp})"
    lines[mention.mention_line] = resolved_line

    return "\n".join(lines)


def replace_section(
    document: str,
    mention: Mention,
    new_content: str,
) -> str:
    """Replace the section containing a mention with new content.

    The new content replaces the entire section (from heading to next heading).
    The mention line within the original section is NOT included in new_content
    (it's handled separately by mark_mention_resolved).

    Args:
        document: The document text.
        mention: The mention whose section to replace.
        new_content: The new section content (should include heading if appropriate).

    Returns:
        Updated document with section replaced.
    """
    lines = document.split("\n")

    # Build new document:
    # - Lines before section
    # - New content
    # - Lines after section
    before = lines[: mention.section_start]
    after = lines[mention.section_end :]

    # Ensure new content ends properly
    new_content = new_content.rstrip("\n")

    # Reconstruct
    new_lines = before + new_content.split("\n") + after
    return "\n".join(new_lines)


# --- LLM Processing ---

SECTION_EDIT_PROMPT = """\
You are editing a section of a product document. The user has left an instruction.

## Full Document (for context)
---
{full_document}
---

## Target Section (lines {start_line}-{end_line})
---
{section_content}
---

## Instruction
{instruction}

Apply the instruction to the target section. Output ONLY the updated section content \
(including heading if present). Do not include the @waypoints instruction line in \
your output - that will be handled separately."""

SECTION_EDIT_SYSTEM = """\
You are a technical writer editing product documentation. Make clean, focused edits \
that address the user's instruction. Preserve markdown formatting and document \
structure. Output only the updated section, nothing else."""


def process_mention(
    mention: Mention,
    full_document: str,
    client: "ChatClient",
) -> Iterator["StreamChunk | ProcessingResult"]:
    """Process a single mention by sending to LLM.

    Sends the full document for context, but requests only the target section
    to be updated.

    Args:
        mention: The mention to process.
        full_document: The complete document for context.
        client: The LLM client.

    Yields:
        StreamChunk during streaming response.
        ProcessingResult when complete (success or error).
    """
    from waypoints.llm.client import StreamChunk, StreamComplete

    prompt = SECTION_EDIT_PROMPT.format(
        full_document=full_document,
        start_line=mention.section_start + 1,  # 1-indexed for display
        end_line=mention.section_end,
        section_content=mention.original_section,
        instruction=mention.instruction,
    )

    full_response = ""
    try:
        for result in client.stream_message(
            messages=[{"role": "user", "content": prompt}],
            system=SECTION_EDIT_SYSTEM,
        ):
            if isinstance(result, StreamChunk):
                full_response += result.text
                yield result
            elif isinstance(result, StreamComplete):
                # Stream complete - we have the full response
                pass

        # Success
        yield ProcessingResult(
            success=True,
            updated_section=full_response.strip(),
        )
    except Exception as e:
        logger.exception("Error processing mention: %s", e)
        yield ProcessingResult(
            success=False,
            error=str(e),
        )


# --- Comment Logging ---


def log_comment(
    docs_path: Path,
    document_type: str,
    entry: CommentLogEntry,
) -> None:
    """Append a comment log entry to the document's comment history.

    Creates the log file if it doesn't exist.

    Args:
        docs_path: Path to the docs directory.
        document_type: "idea-brief" or "product-spec".
        entry: The log entry to append.
    """
    log_file = docs_path / f"{document_type}-comments.jsonl"

    # Ensure docs directory exists
    docs_path.mkdir(parents=True, exist_ok=True)

    # Append entry as JSON line
    with log_file.open("a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(entry)) + "\n")

    logger.info(
        "Logged comment for %s: %s",
        document_type,
        entry.instruction[:50],
    )


def save_document_version(
    docs_path: Path,
    document_type: str,
    content: str,
) -> Path:
    """Save a new timestamped version of a document.

    Args:
        docs_path: Path to the docs directory.
        document_type: "idea-brief" or "product-spec".
        content: The document content to save.

    Returns:
        Path to the saved file.
    """
    docs_path.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    filename = f"{document_type}-{timestamp}.md"
    file_path = docs_path / filename

    file_path.write_text(content, encoding="utf-8")
    logger.info("Saved document version: %s", file_path)

    return file_path
