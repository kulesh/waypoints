"""Tests for centralized command runner timeout behavior."""

from __future__ import annotations

from sys import executable

from waypoints.runtime.command_runner import CommandEvent, CommandRunner
from waypoints.runtime.timeout_history import TimeoutHistory
from waypoints.runtime.timeout_policy import (
    BackoffPolicy,
    SignalPolicy,
    TimeoutContext,
    TimeoutDomain,
    TimeoutPolicy,
    TimeoutPolicyRegistry,
)


def _make_registry(policy: TimeoutPolicy) -> TimeoutPolicyRegistry:
    return TimeoutPolicyRegistry(policies={policy.domain: policy})


def test_command_runner_retries_with_backoff_on_timeout() -> None:
    policy = TimeoutPolicy(
        domain=TimeoutDomain.HOST_VALIDATION,
        default_timeout_seconds=0.05,
        min_timeout_seconds=0.01,
        retry_on_timeout=True,
        use_process_group=True,
        backoff=BackoffPolicy(max_attempts=2, multiplier=2.0, max_timeout_seconds=0.2),
        signal=SignalPolicy(warning_fraction=0.5, terminate_grace_seconds=0.05),
    )
    runner = CommandRunner(
        policy_registry=_make_registry(policy),
        timeout_history=TimeoutHistory(),
    )

    result = runner.run(
        command=[executable, "-c", "import time; time.sleep(0.3)"],
        domain=TimeoutDomain.HOST_VALIDATION,
    )

    assert result.timed_out
    assert len(result.attempts) == 2
    assert result.attempts[1].timeout_seconds > result.attempts[0].timeout_seconds
    assert result.effective_exit_code != 0


def test_command_runner_emits_warning_before_timeout() -> None:
    policy = TimeoutPolicy(
        domain=TimeoutDomain.LLM_TOOL_BASH,
        default_timeout_seconds=0.2,
        min_timeout_seconds=0.01,
        retry_on_timeout=False,
        use_process_group=True,
        backoff=BackoffPolicy(max_attempts=1, multiplier=1.0, max_timeout_seconds=1.0),
        signal=SignalPolicy(warning_fraction=0.5, terminate_grace_seconds=0.05),
    )
    runner = CommandRunner(
        policy_registry=_make_registry(policy),
        timeout_history=TimeoutHistory(),
    )

    events: list[CommandEvent] = []
    result = runner.run(
        command=[executable, "-c", "import time; time.sleep(0.12)"],
        domain=TimeoutDomain.LLM_TOOL_BASH,
        on_event=events.append,
    )

    assert not result.timed_out
    assert result.final_attempt.warning_emitted
    assert any(event.event_type == "warning" for event in events)


def test_command_runner_respects_explicit_timeout_override() -> None:
    policy = TimeoutPolicy(
        domain=TimeoutDomain.FLIGHT_TEST,
        default_timeout_seconds=1.0,
        min_timeout_seconds=0.1,
        retry_on_timeout=False,
        use_process_group=True,
        backoff=BackoffPolicy(max_attempts=1, multiplier=1.0, max_timeout_seconds=5.0),
        signal=SignalPolicy(warning_fraction=0.8, terminate_grace_seconds=0.05),
    )
    runner = CommandRunner(
        policy_registry=_make_registry(policy),
        timeout_history=TimeoutHistory(),
    )

    result = runner.run(
        command=[executable, "-c", "print('ok')"],
        domain=TimeoutDomain.FLIGHT_TEST,
        requested_timeout_seconds=0.001,
    )

    assert result.effective_exit_code == 0
    assert result.final_attempt.timeout_seconds == 0.1
    assert "ok" in result.stdout


def test_policy_timeout_resolution_accepts_history_hint() -> None:
    policy = TimeoutPolicy(
        domain=TimeoutDomain.GIT_OPERATION,
        default_timeout_seconds=5.0,
        min_timeout_seconds=1.0,
        retry_on_timeout=False,
        use_process_group=False,
        backoff=BackoffPolicy(max_attempts=1, multiplier=1.0, max_timeout_seconds=60.0),
        signal=SignalPolicy(warning_fraction=0.9, terminate_grace_seconds=0.1),
    )
    registry = _make_registry(policy)

    resolved = registry.timeout_for_attempt(
        TimeoutContext(domain=TimeoutDomain.GIT_OPERATION, command="git status"),
        attempt=1,
        history_hint_seconds=12.0,
    )

    assert resolved == 12.0
