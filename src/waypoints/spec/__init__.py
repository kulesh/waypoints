"""Utilities for product-spec context extraction and validation."""

from waypoints.spec.context import (
    compute_spec_hash,
    extract_spec_section_headings,
    normalize_section_ref,
    section_ref_exists,
)

__all__ = [
    "compute_spec_hash",
    "extract_spec_section_headings",
    "normalize_section_ref",
    "section_ref_exists",
]
