"""Tests for workspace provenance tracking."""

from pathlib import Path

from waypoints.fly.provenance import (
    capture_workspace_snapshot,
    summarize_workspace_diff,
)


def test_workspace_diff_tracks_text_and_binary_changes(tmp_path: Path) -> None:
    """Before/after snapshots capture file provenance and rough token estimate."""
    (tmp_path / "a.py").write_text("print('hello')\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("one\ntwo\n", encoding="utf-8")
    (tmp_path / "bin.dat").write_bytes(b"\x00\x01\x02")

    # Internal artifacts should not be counted
    (tmp_path / "sessions").mkdir()
    (tmp_path / "sessions" / "internal.log").write_text("ignored", encoding="utf-8")
    (tmp_path / "metrics.jsonl").write_text("ignored", encoding="utf-8")

    before = capture_workspace_snapshot(tmp_path)

    (tmp_path / "a.py").write_text("print('hello')\nprint('world')\n", encoding="utf-8")
    (tmp_path / "c.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "bin.dat").write_bytes(b"\x00\xff\x02\x03")
    (tmp_path / "sessions" / "internal.log").write_text(
        "still ignored", encoding="utf-8"
    )
    (tmp_path / "metrics.jsonl").write_text("still ignored", encoding="utf-8")
    (tmp_path / "b.txt").unlink()

    after = capture_workspace_snapshot(tmp_path)
    summary = summarize_workspace_diff(before, after)

    assert summary.files_added == 1
    assert summary.files_modified == 2
    assert summary.files_deleted == 1
    assert summary.total_files_changed == 4
    assert summary.text_files_changed == 3
    assert summary.binary_files_changed == 1
    assert summary.approx_tokens_changed > 0

    changed_paths = {entry.path for entry in summary.changed_files}
    assert "a.py" in changed_paths
    assert "b.txt" in changed_paths
    assert "c.py" in changed_paths
    assert "bin.dat" in changed_paths
    assert "sessions/internal.log" not in changed_paths
    assert "metrics.jsonl" not in changed_paths


def test_workspace_diff_marks_indeterminate_large_text_changes(tmp_path: Path) -> None:
    """Large text files changed in-place are marked as size-only estimates."""
    large_before = "a" * 300_000
    large_after = "b" * 300_000
    file_path = tmp_path / "large.txt"
    file_path.write_text(large_before, encoding="utf-8")
    before = capture_workspace_snapshot(tmp_path)

    file_path.write_text(large_after, encoding="utf-8")
    after = capture_workspace_snapshot(tmp_path)
    summary = summarize_workspace_diff(before, after)

    assert summary.files_modified == 1
    assert summary.indeterminate_text_files == 1
