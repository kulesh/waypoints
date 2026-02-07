"""Central timeout policy definitions and resolution helpers."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class TimeoutDomain(str, Enum):
    """Logical command domains with distinct timeout behavior."""

    HOST_VALIDATION = "host_validation"
    LLM_TOOL_BASH = "llm_tool_bash"
    FLIGHT_TEST = "flight_test"
    UI_GIT_PROBE = "ui_git_probe"
    GIT_OPERATION = "git_operation"


@dataclass(frozen=True, slots=True)
class BackoffPolicy:
    """Timeout backoff configuration."""

    max_attempts: int
    multiplier: float
    max_timeout_seconds: float


@dataclass(frozen=True, slots=True)
class SignalPolicy:
    """Process signaling behavior when timeout thresholds are crossed."""

    warning_fraction: float
    terminate_grace_seconds: float


@dataclass(frozen=True, slots=True)
class TimeoutPolicy:
    """Resolved policy for a timeout domain."""

    domain: TimeoutDomain
    default_timeout_seconds: float
    min_timeout_seconds: float
    retry_on_timeout: bool
    use_process_group: bool
    backoff: BackoffPolicy
    signal: SignalPolicy


@dataclass(frozen=True, slots=True)
class TimeoutContext:
    """Context used to resolve timeout values for a command run."""

    domain: TimeoutDomain
    command: str
    category: str | None = None
    requested_timeout_seconds: float | None = None


class TimeoutPolicyRegistry:
    """Registry that resolves timeout policies and per-attempt limits."""

    def __init__(
        self,
        policies: dict[TimeoutDomain, TimeoutPolicy] | None = None,
    ) -> None:
        self._policies = policies or {
            TimeoutDomain.HOST_VALIDATION: TimeoutPolicy(
                domain=TimeoutDomain.HOST_VALIDATION,
                default_timeout_seconds=300.0,
                min_timeout_seconds=30.0,
                retry_on_timeout=True,
                use_process_group=True,
                backoff=BackoffPolicy(
                    max_attempts=3,
                    multiplier=2.0,
                    max_timeout_seconds=1800.0,
                ),
                signal=SignalPolicy(
                    warning_fraction=0.75,
                    terminate_grace_seconds=15.0,
                ),
            ),
            TimeoutDomain.LLM_TOOL_BASH: TimeoutPolicy(
                domain=TimeoutDomain.LLM_TOOL_BASH,
                default_timeout_seconds=120.0,
                min_timeout_seconds=1.0,
                retry_on_timeout=False,
                use_process_group=True,
                backoff=BackoffPolicy(
                    max_attempts=1,
                    multiplier=1.0,
                    max_timeout_seconds=900.0,
                ),
                signal=SignalPolicy(
                    warning_fraction=0.8,
                    terminate_grace_seconds=2.0,
                ),
            ),
            TimeoutDomain.FLIGHT_TEST: TimeoutPolicy(
                domain=TimeoutDomain.FLIGHT_TEST,
                default_timeout_seconds=180.0,
                min_timeout_seconds=5.0,
                retry_on_timeout=False,
                use_process_group=True,
                backoff=BackoffPolicy(
                    max_attempts=1,
                    multiplier=1.0,
                    max_timeout_seconds=1800.0,
                ),
                signal=SignalPolicy(
                    warning_fraction=0.8,
                    terminate_grace_seconds=10.0,
                ),
            ),
            TimeoutDomain.UI_GIT_PROBE: TimeoutPolicy(
                domain=TimeoutDomain.UI_GIT_PROBE,
                default_timeout_seconds=5.0,
                min_timeout_seconds=1.0,
                retry_on_timeout=False,
                use_process_group=False,
                backoff=BackoffPolicy(
                    max_attempts=1,
                    multiplier=1.0,
                    max_timeout_seconds=30.0,
                ),
                signal=SignalPolicy(
                    warning_fraction=0.9,
                    terminate_grace_seconds=1.0,
                ),
            ),
            TimeoutDomain.GIT_OPERATION: TimeoutPolicy(
                domain=TimeoutDomain.GIT_OPERATION,
                default_timeout_seconds=30.0,
                min_timeout_seconds=3.0,
                retry_on_timeout=False,
                use_process_group=False,
                backoff=BackoffPolicy(
                    max_attempts=1,
                    multiplier=1.0,
                    max_timeout_seconds=300.0,
                ),
                signal=SignalPolicy(
                    warning_fraction=0.85,
                    terminate_grace_seconds=3.0,
                ),
            ),
        }

    def policy_for(self, domain: TimeoutDomain) -> TimeoutPolicy:
        """Return policy for a specific timeout domain."""
        return self._policies[domain]

    def timeout_for_attempt(
        self,
        context: TimeoutContext,
        attempt: int,
        history_hint_seconds: float | None = None,
    ) -> float:
        """Resolve timeout value for a specific attempt."""
        policy = self.policy_for(context.domain)

        if context.requested_timeout_seconds is not None:
            return self._clamp(
                context.requested_timeout_seconds,
                policy.min_timeout_seconds,
                policy.backoff.max_timeout_seconds,
            )

        base_timeout = self._contextual_base_timeout(policy, context)
        if history_hint_seconds is not None:
            base_timeout = max(base_timeout, history_hint_seconds)

        backoff_multiplier = policy.backoff.multiplier ** max(0, attempt - 1)
        resolved = base_timeout * backoff_multiplier
        return self._clamp(
            resolved,
            policy.min_timeout_seconds,
            policy.backoff.max_timeout_seconds,
        )

    def should_retry_timeout(self, domain: TimeoutDomain, attempt: int) -> bool:
        """Whether timeout should trigger another attempt."""
        policy = self.policy_for(domain)
        return policy.retry_on_timeout and attempt < policy.backoff.max_attempts

    def warning_after_seconds(
        self,
        domain: TimeoutDomain,
        timeout_seconds: float,
    ) -> float | None:
        """Return warning threshold for a command timeout, if enabled."""
        policy = self.policy_for(domain)
        fraction = policy.signal.warning_fraction
        if fraction <= 0.0 or fraction >= 1.0:
            return None
        warning = timeout_seconds * fraction
        if warning <= 0.0 or warning >= timeout_seconds:
            return None
        return warning

    def _contextual_base_timeout(
        self,
        policy: TimeoutPolicy,
        context: TimeoutContext,
    ) -> float:
        """Return context-aware base timeout before backoff."""
        base = policy.default_timeout_seconds
        command = context.command.lower()

        if context.domain == TimeoutDomain.HOST_VALIDATION:
            if "cargo clippy" in command:
                base = max(base, 900.0)
            elif "cargo" in command:
                base = max(base, 600.0)
            elif context.category == "type":
                base = max(base, 420.0)

        return self._clamp(
            base,
            policy.min_timeout_seconds,
            policy.backoff.max_timeout_seconds,
        )

    @staticmethod
    def _clamp(value: float, minimum: float, maximum: float) -> float:
        return max(minimum, min(value, maximum))


_DEFAULT_TIMEOUT_POLICY_REGISTRY = TimeoutPolicyRegistry()


def get_timeout_policy_registry() -> TimeoutPolicyRegistry:
    """Return shared timeout policy registry."""
    return _DEFAULT_TIMEOUT_POLICY_REGISTRY
