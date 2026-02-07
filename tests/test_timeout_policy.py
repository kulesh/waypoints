"""Tests for centralized timeout policy resolution."""

from waypoints.runtime.timeout_policy import (
    TimeoutContext,
    TimeoutDomain,
    get_timeout_policy_registry,
)


def test_host_validation_cargo_clippy_gets_extended_base_timeout() -> None:
    registry = get_timeout_policy_registry()

    context = TimeoutContext(
        domain=TimeoutDomain.HOST_VALIDATION,
        command="cargo clippy -- -D warnings",
        category="lint",
    )

    timeout_seconds = registry.timeout_for_attempt(context, attempt=1)

    assert timeout_seconds >= 900.0


def test_host_validation_timeout_uses_exponential_backoff() -> None:
    registry = get_timeout_policy_registry()

    context = TimeoutContext(
        domain=TimeoutDomain.HOST_VALIDATION,
        command="cargo test",
        category="test",
    )

    first = registry.timeout_for_attempt(context, attempt=1)
    second = registry.timeout_for_attempt(context, attempt=2)

    assert second > first
    assert (
        second
        <= registry.policy_for(
            TimeoutDomain.HOST_VALIDATION
        ).backoff.max_timeout_seconds
    )


def test_requested_timeout_is_clamped_to_domain_bounds() -> None:
    registry = get_timeout_policy_registry()

    context = TimeoutContext(
        domain=TimeoutDomain.LLM_TOOL_BASH,
        command="echo hello",
        requested_timeout_seconds=0.001,
    )

    timeout_seconds = registry.timeout_for_attempt(context, attempt=1)

    assert (
        timeout_seconds
        == registry.policy_for(TimeoutDomain.LLM_TOOL_BASH).min_timeout_seconds
    )
