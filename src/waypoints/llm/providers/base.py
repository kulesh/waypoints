"""Base class for LLM providers."""

import re
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from enum import Enum
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

if TYPE_CHECKING:
    from waypoints.llm.metrics import MetricsCollector


@dataclass
class StreamChunk:
    """A chunk of streamed text from the LLM."""

    text: str


@dataclass
class StreamToolUse:
    """LLM is requesting to use a tool."""

    tool_name: str
    tool_input: dict[str, Any]
    tool_output: str | None = None


@dataclass
class StreamComplete:
    """Stream completion with metadata."""

    full_text: str
    cost_usd: float | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    cached_tokens_in: int | None = None
    session_id: str | None = None


class APIErrorType(Enum):
    """Types of API errors for classification."""

    RATE_LIMITED = "rate_limited"
    API_UNAVAILABLE = "api_unavailable"
    BUDGET_EXCEEDED = "budget_exceeded"
    UNKNOWN = "unknown"


# Patterns to match in error messages (case-insensitive)
RATE_LIMIT_PATTERNS = [
    "rate limit",
    "rate_limit",
    "ratelimit",
    "429",
    "too many requests",
    "throttl",
]

UNAVAILABLE_PATTERNS = [
    "overloaded",
    "503",
    "502",
    "504",
    "unavailable",
    "service error",
    "temporarily",
    "try again later",
    "capacity",
]

BUDGET_PATTERNS = [
    "budget",
    "spending limit",
    "billing",
    "credit",
    "quota exceeded",
    "usage limit",
    "daily limit",
    "out of extra usage",  # "You're out of extra usage"
    "out of usage",
    "limit reached",
    "resets",  # Messages mentioning when usage resets
]


def classify_api_error(error: Exception) -> APIErrorType:
    """Classify an API error by parsing the error message."""
    error_str = str(error).lower()

    for pattern in BUDGET_PATTERNS:
        if pattern in error_str:
            return APIErrorType.BUDGET_EXCEEDED

    for pattern in RATE_LIMIT_PATTERNS:
        if pattern in error_str:
            return APIErrorType.RATE_LIMITED

    for pattern in UNAVAILABLE_PATTERNS:
        if pattern in error_str:
            return APIErrorType.API_UNAVAILABLE

    return APIErrorType.UNKNOWN


def extract_reset_time(error_msg: str) -> str | None:
    """Extract reset time from API error message if present.

    Parses messages like:
    - "resets 7pm (America/New_York)"
    - "resets in 2 hours"
    - "resets at 3:00pm"

    Args:
        error_msg: The error message to parse

    Returns:
        The reset time string if found, None otherwise
    """
    patterns = [
        # "resets 7pm (America/New_York)" - time with timezone
        r"resets?\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm)\s*\([^)]+\))",
        # "resets in 2 hours" - relative time
        r"resets?\s+(in\s+\d+\s+(?:hour|minute|second)s?)",
        # "resets at 3:00pm" or "resets at 15:00"
        r"resets?\s+at\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)",
        # "resets 7pm" - simple time without timezone
        r"resets?\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm))",
    ]
    for pattern in patterns:
        match = re.search(pattern, error_msg, re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return None


def _parse_clock_time(raw_time: str) -> time | None:
    """Parse a clock time like '7pm', '3:00pm', or '15:30'."""
    text = raw_time.strip().lower().replace(" ", "")
    formats = ("%I%p", "%I:%M%p", "%H:%M", "%H")
    for fmt in formats:
        try:
            return datetime.strptime(text, fmt).time()
        except ValueError:
            continue
    return None


def _next_occurrence(clock: time, now_local: datetime) -> datetime:
    """Build next local datetime occurrence for clock time."""
    candidate = now_local.replace(
        hour=clock.hour,
        minute=clock.minute,
        second=0,
        microsecond=0,
    )
    if candidate <= now_local:
        candidate += timedelta(days=1)
    return candidate


def extract_reset_datetime(
    error_msg: str,
    now: datetime | None = None,
) -> datetime | None:
    """Extract reset timestamp as UTC datetime when possible.

    Supports:
    - "resets in 2 hours"
    - "resets 7pm (America/New_York)"
    - "resets at 3:00pm"
    - "resets 15:00"
    """
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)

    relative_match = re.search(
        r"resets?\s+in\s+(\d+)\s+(hour|minute|second)s?",
        error_msg,
        re.IGNORECASE,
    )
    if relative_match:
        quantity = int(relative_match.group(1))
        unit = relative_match.group(2).lower()
        if unit == "hour":
            return current + timedelta(hours=quantity)
        if unit == "minute":
            return current + timedelta(minutes=quantity)
        return current + timedelta(seconds=quantity)

    zoned_match = re.search(
        r"resets?\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm))\s*\(([^)]+)\)",
        error_msg,
        re.IGNORECASE,
    )
    if zoned_match:
        clock = _parse_clock_time(zoned_match.group(1))
        if clock is None:
            return None
        tz_name = zoned_match.group(2).strip()
        try:
            tz = ZoneInfo(tz_name)
        except ZoneInfoNotFoundError:
            return None
        now_local = current.astimezone(tz)
        return _next_occurrence(clock, now_local).astimezone(UTC)

    local_match = re.search(
        r"resets?\s+(?:at\s+)?(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)",
        error_msg,
        re.IGNORECASE,
    )
    if local_match:
        clock = _parse_clock_time(local_match.group(1))
        if clock is None:
            return None
        local_tz = datetime.now().astimezone().tzinfo or UTC
        now_local = current.astimezone(local_tz)
        return _next_occurrence(clock, now_local).astimezone(UTC)

    return None


def is_retryable_error(error_type: APIErrorType) -> bool:
    """Check if an error type is retryable."""
    return error_type in (APIErrorType.RATE_LIMITED, APIErrorType.API_UNAVAILABLE)


# Retry configuration
MAX_RETRIES = 3
RETRY_DELAYS = [5, 15, 45]  # Exponential backoff


class LLMProvider(ABC):
    """Abstract base class for LLM providers."""

    provider_name: str  # "anthropic" or "openai"

    def __init__(
        self,
        model: str,
        api_key: str | None = None,
    ) -> None:
        """Initialize provider.

        Args:
            model: Model identifier string.
            api_key: Optional API key (uses env var or web auth if None).
        """
        self.model = model
        self.api_key = api_key

    @abstractmethod
    def stream_message(
        self,
        messages: list[dict[str, str]],
        system: str = "",
        max_tokens: int = 4096,
        metrics_collector: "MetricsCollector | None" = None,
        phase: str = "unknown",
    ) -> Iterator[StreamChunk | StreamComplete]:
        """Stream a chat completion.

        Args:
            messages: Conversation history as list of {role, content} dicts.
            system: System prompt.
            max_tokens: Maximum tokens in response.
            metrics_collector: Optional metrics collector.
            phase: Phase name for metrics.

        Yields:
            StreamChunk for each text piece, then StreamComplete at end.
        """

    @abstractmethod
    async def agent_query(
        self,
        prompt: str,
        system_prompt: str | None = None,
        allowed_tools: list[str] | None = None,
        cwd: str | None = None,
        tool_role: str | None = None,
        resume_session_id: str | None = None,
        metrics_collector: "MetricsCollector | None" = None,
        phase: str = "fly",
        waypoint_id: str | None = None,
    ) -> AsyncIterator[StreamChunk | StreamToolUse | StreamComplete]:
        """Run an agentic query with tool use.

        Args:
            prompt: The prompt to send.
            system_prompt: Optional system prompt.
            allowed_tools: List of tool names to allow.
            cwd: Working directory for tools.
            tool_role: Optional role identifier for tool permissions.
            resume_session_id: Optional provider session ID to resume.
            metrics_collector: Optional metrics collector.
            phase: Phase name for metrics.
            waypoint_id: Optional waypoint ID for per-waypoint metrics.

        Yields:
            StreamChunk for text, StreamToolUse for tool calls, StreamComplete at end.
        """
        # Abstract method - subclasses must implement
        raise NotImplementedError  # pragma: no cover
        # Make this an async generator for type checking
        if False:  # pragma: no cover
            yield StreamChunk(text="")
