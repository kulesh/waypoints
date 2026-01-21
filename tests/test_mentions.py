"""Tests for @waypoints mention parsing and processing."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from waypoints.mentions import (
    CommentLogEntry,
    Mention,
    find_mentions,
    is_mention_resolved,
    log_comment,
    mark_mention_resolved,
    parse_sections,
    replace_section,
    save_document_version,
)


class TestParseSections:
    """Tests for markdown section parsing."""

    def test_single_section_no_heading(self) -> None:
        """Document with no headings is one section."""
        doc = "Just some text\nwith multiple lines."
        sections = parse_sections(doc)
        assert len(sections) == 1
        assert sections[0].heading is None
        assert sections[0].content == doc

    def test_single_heading(self) -> None:
        """Document with one heading."""
        doc = """# Title

Content here."""
        sections = parse_sections(doc)
        assert len(sections) == 1
        assert sections[0].heading == "# Title"
        assert "Content here." in sections[0].content

    def test_multiple_headings(self) -> None:
        """Document with multiple headings."""
        doc = """# Title

Intro.

## Section One

Content one.

## Section Two

Content two."""
        sections = parse_sections(doc)
        assert len(sections) == 3
        assert sections[0].heading == "# Title"
        assert sections[1].heading == "## Section One"
        assert sections[2].heading == "## Section Two"

    def test_preamble_before_first_heading(self) -> None:
        """Content before first heading is preamble."""
        doc = """This is preamble.

# First Heading

Content."""
        sections = parse_sections(doc)
        assert len(sections) == 2
        assert sections[0].heading is None
        assert "preamble" in sections[0].content
        assert sections[1].heading == "# First Heading"

    def test_preserves_line_numbers(self) -> None:
        """Section start/end lines are correct."""
        doc = """# Title

Line 2.

## Section

Line 6."""
        sections = parse_sections(doc)
        # Title section starts at line 0
        assert sections[0].start_line == 0
        # Section starts at line 4 (after blank line)
        assert sections[1].start_line == 4
        assert sections[1].end_line == 7  # Total lines


class TestFindMentions:
    """Tests for finding @waypoints mentions."""

    def test_find_single_mention(self) -> None:
        """Find a single mention."""
        doc = """## Section

Some text.
@waypoints: expand this section

More text."""
        mentions = find_mentions(doc)
        assert len(mentions) == 1
        assert mentions[0].instruction == "expand this section"

    def test_find_multiple_mentions(self) -> None:
        """Find multiple mentions across sections."""
        doc = """## Section One

@waypoints: add examples

## Section Two

@waypoints: simplify this"""
        mentions = find_mentions(doc)
        assert len(mentions) == 2
        assert mentions[0].instruction == "add examples"
        assert mentions[1].instruction == "simplify this"

    def test_skip_resolved_mentions(self) -> None:
        """Resolved mentions are skipped."""
        doc = """## Section

[resolved]: # (@waypoints: already done - 2026-01-11T14:30:22)
@waypoints: still pending"""
        mentions = find_mentions(doc)
        assert len(mentions) == 1
        assert mentions[0].instruction == "still pending"

    def test_no_mentions(self) -> None:
        """No mentions returns empty list."""
        doc = """## Section

Just regular content."""
        mentions = find_mentions(doc)
        assert len(mentions) == 0

    def test_mention_captures_section(self) -> None:
        """Mention includes its containing section."""
        doc = """## Problem Statement

This is the problem.
@waypoints: expand this

## Solution

This is the solution."""
        mentions = find_mentions(doc)
        assert len(mentions) == 1
        assert mentions[0].section_heading == "## Problem Statement"
        assert "This is the problem." in mentions[0].original_section

    def test_case_insensitive(self) -> None:
        """@WAYPOINTS works too."""
        doc = """## Section

@WAYPOINTS: uppercase mention"""
        mentions = find_mentions(doc)
        assert len(mentions) == 1
        assert mentions[0].instruction == "uppercase mention"

    def test_with_leading_whitespace(self) -> None:
        """Indented mentions are found."""
        doc = """## Section

    @waypoints: indented mention"""
        mentions = find_mentions(doc)
        assert len(mentions) == 1
        assert mentions[0].instruction == "indented mention"


class TestIsMentionResolved:
    """Tests for checking if a mention is resolved."""

    def test_resolved_mention(self) -> None:
        """Resolved format is detected."""
        line = "[resolved]: # (@waypoints: expand this - 2026-01-11T14:30:22)"
        assert is_mention_resolved(line) is True

    def test_unresolved_mention(self) -> None:
        """Unresolved mention is not detected as resolved."""
        line = "@waypoints: expand this"
        assert is_mention_resolved(line) is False

    def test_regular_line(self) -> None:
        """Regular text is not resolved."""
        line = "Just some text."
        assert is_mention_resolved(line) is False


class TestMarkMentionResolved:
    """Tests for marking mentions as resolved."""

    def test_marks_resolved(self) -> None:
        """Mention is converted to resolved format."""
        doc = """## Section

@waypoints: expand this section"""
        mention = Mention(
            instruction="expand this section",
            section_start=0,
            section_end=3,
            mention_line=2,
            original_section=doc,
        )
        result = mark_mention_resolved(doc, mention)
        assert "[resolved]: # (@waypoints: expand this section -" in result
        assert "@waypoints: expand this section" not in result.split("[resolved]")[0]

    def test_preserves_indentation(self) -> None:
        """Indentation is preserved in resolved format."""
        doc = """## Section

    @waypoints: indented"""
        mention = Mention(
            instruction="indented",
            section_start=0,
            section_end=3,
            mention_line=2,
            original_section=doc,
        )
        result = mark_mention_resolved(doc, mention)
        # Check that the resolved line has same indentation
        for line in result.split("\n"):
            if "[resolved]" in line:
                assert line.startswith("    ")
                break


class TestReplaceSection:
    """Tests for replacing section content."""

    def test_replace_middle_section(self) -> None:
        """Replace a section in the middle of document."""
        doc = """## First

First content.

## Second

Old content.

## Third

Third content."""
        mention = Mention(
            instruction="expand",
            section_start=4,  # "## Second" line
            section_end=8,  # Up to "## Third"
            mention_line=6,
            original_section="## Second\n\nOld content.\n",
        )
        new_section = "## Second\n\nNew expanded content."
        result = replace_section(doc, mention, new_section)

        assert "New expanded content." in result
        assert "Old content." not in result
        assert "First content." in result
        assert "Third content." in result

    def test_replace_last_section(self) -> None:
        """Replace the last section."""
        doc = """## First

Content.

## Last

Old last."""
        mention = Mention(
            instruction="expand",
            section_start=4,
            section_end=7,
            mention_line=6,
            original_section="## Last\n\nOld last.",
        )
        new_section = "## Last\n\nNew last content."
        result = replace_section(doc, mention, new_section)

        assert "New last content." in result
        assert "Old last." not in result


class TestLogComment:
    """Tests for comment logging."""

    def test_log_comment_creates_file(self) -> None:
        """Log creates file if it doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            docs_path = Path(tmpdir)
            entry = CommentLogEntry(
                timestamp="2026-01-11T14:30:22",
                section="## Problem Statement",
                instruction="expand with examples",
                lines_before=5,
                lines_after=12,
            )
            log_comment(docs_path, "idea-brief", entry)

            log_file = docs_path / "idea-brief-comments.jsonl"
            assert log_file.exists()

            content = log_file.read_text()
            data = json.loads(content.strip())
            assert data["section"] == "## Problem Statement"
            assert data["instruction"] == "expand with examples"

    def test_log_comment_appends(self) -> None:
        """Multiple logs are appended."""
        with tempfile.TemporaryDirectory() as tmpdir:
            docs_path = Path(tmpdir)

            for i in range(3):
                entry = CommentLogEntry(
                    timestamp=f"2026-01-11T14:30:{i:02d}",
                    section=f"## Section {i}",
                    instruction=f"instruction {i}",
                    lines_before=5,
                    lines_after=10,
                )
                log_comment(docs_path, "product-spec", entry)

            log_file = docs_path / "product-spec-comments.jsonl"
            lines = log_file.read_text().strip().split("\n")
            assert len(lines) == 3


class TestSaveDocumentVersion:
    """Tests for document versioning."""

    def test_save_creates_timestamped_file(self) -> None:
        """Save creates a new timestamped file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            docs_path = Path(tmpdir)
            content = "# Test Document\n\nContent here."

            result_path = save_document_version(docs_path, "idea-brief", content)

            assert result_path.exists()
            assert result_path.name.startswith("idea-brief-")
            assert result_path.name.endswith(".md")
            assert result_path.read_text() == content

    def test_save_creates_docs_dir(self) -> None:
        """Save creates docs directory if needed."""
        with tempfile.TemporaryDirectory() as tmpdir:
            docs_path = Path(tmpdir) / "docs" / "nested"
            content = "# Test"

            result_path = save_document_version(docs_path, "product-spec", content)

            assert docs_path.exists()
            assert result_path.exists()

    def test_multiple_versions_have_different_names(self) -> None:
        """Multiple saves create different files."""
        import time

        with tempfile.TemporaryDirectory() as tmpdir:
            docs_path = Path(tmpdir)

            path1 = save_document_version(docs_path, "idea-brief", "Version 1")
            time.sleep(1.1)  # Ensure different timestamp
            path2 = save_document_version(docs_path, "idea-brief", "Version 2")

            assert path1 != path2
            assert path1.read_text() == "Version 1"
            assert path2.read_text() == "Version 2"
