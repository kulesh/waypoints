"""Tests for product-spec context helpers."""

from waypoints.spec import (
    compute_spec_hash,
    extract_spec_section_headings,
    section_ref_exists,
)


def test_extract_spec_section_headings_preserves_order() -> None:
    spec = """# Product Specification

## Overview
### Scope
## Problem Statement
## Overview
"""
    headings = extract_spec_section_headings(spec)
    assert headings == (
        "Product Specification",
        "Overview",
        "Scope",
        "Problem Statement",
    )


def test_section_ref_exists_allows_loose_matching() -> None:
    headings = ("2. Problem Statement", "5. Runtime Architecture")
    assert section_ref_exists("Problem Statement", headings)
    assert section_ref_exists("5. Runtime Architecture", headings)
    assert section_ref_exists("Runtime Architecture", headings)
    assert not section_ref_exists("Nonexistent Section", headings)


def test_compute_spec_hash_is_stable() -> None:
    spec = "## A\\nBody"
    assert compute_spec_hash(spec) == compute_spec_hash(spec)
    assert compute_spec_hash(spec) != compute_spec_hash(spec + " changed")
