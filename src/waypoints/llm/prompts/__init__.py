"""Centralized prompt definitions for all Waypoints phases.

This module consolidates all LLM prompts used throughout Waypoints,
enabling:
- Headless operation via JourneyCoordinator
- Consistent prompt management
- Easy testing and verification
"""
from __future__ import annotations

# SPARK phase prompts
# CHART phase prompts
from waypoints.llm.prompts.chart import (
    CHART_SYSTEM_PROMPT,
    REPRIORITIZE_PROMPT,
    WAYPOINT_ADD_PROMPT,
    WAYPOINT_BREAKDOWN_PROMPT,
    WAYPOINT_GENERATION_PROMPT,
)

# FLY phase prompts
from waypoints.llm.prompts.fly import (
    build_execution_prompt,
    build_verification_prompt,
)

# SHAPE phase prompts
from waypoints.llm.prompts.shape import (
    BRIEF_GENERATION_PROMPT,
    BRIEF_SUMMARY_PROMPT,
    BRIEF_SYSTEM_PROMPT,
    SPEC_GENERATION_PROMPT,
    SPEC_SUMMARY_PROMPT,
    SPEC_SYSTEM_PROMPT,
    SUMMARY_SYSTEM_PROMPT,
)
from waypoints.llm.prompts.spark import QA_SYSTEM_PROMPT

__all__ = [
    # SPARK
    "QA_SYSTEM_PROMPT",
    # SHAPE
    "BRIEF_GENERATION_PROMPT",
    "BRIEF_SUMMARY_PROMPT",
    "BRIEF_SYSTEM_PROMPT",
    "SPEC_GENERATION_PROMPT",
    "SPEC_SUMMARY_PROMPT",
    "SPEC_SYSTEM_PROMPT",
    "SUMMARY_SYSTEM_PROMPT",
    # CHART
    "CHART_SYSTEM_PROMPT",
    "WAYPOINT_GENERATION_PROMPT",
    "WAYPOINT_BREAKDOWN_PROMPT",
    "WAYPOINT_ADD_PROMPT",
    "REPRIORITIZE_PROMPT",
    # FLY
    "build_execution_prompt",
    "build_verification_prompt",
]
