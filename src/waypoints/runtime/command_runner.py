"""Shared subprocess runner with central timeout/backoff/signal policy."""

from __future__ import annotations

import os
import shlex
import signal
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from waypoints.runtime.timeout_history import (
    TimeoutHistory,
    build_command_key,
    get_timeout_history,
)
from waypoints.runtime.timeout_policy import (
    TimeoutContext,
    TimeoutDomain,
    TimeoutPolicyRegistry,
    get_timeout_policy_registry,
)


@dataclass(frozen=True, slots=True)
class CommandEvent:
    """Lifecycle event emitted while running commands."""

    event_type: str
    domain: TimeoutDomain
    command: str
    attempt: int
    timeout_seconds: float
    detail: str = ""


@dataclass(frozen=True, slots=True)
class CommandAttemptResult:
    """Result for one command attempt."""

    attempt: int
    timeout_seconds: float
    duration_seconds: float
    timed_out: bool
    warning_emitted: bool
    exit_code: int | None
    stdout: str
    stderr: str
    signal_sequence: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CommandResult:
    """Aggregated result for command execution."""

    domain: TimeoutDomain
    command: str
    attempts: tuple[CommandAttemptResult, ...]
    total_duration_seconds: float

    @property
    def final_attempt(self) -> CommandAttemptResult:
        return self.attempts[-1]

    @property
    def stdout(self) -> str:
        return self.final_attempt.stdout

    @property
    def stderr(self) -> str:
        return self.final_attempt.stderr

    @property
    def timed_out(self) -> bool:
        return self.final_attempt.timed_out

    @property
    def exit_code(self) -> int | None:
        return self.final_attempt.exit_code

    @property
    def effective_exit_code(self) -> int:
        if self.exit_code is not None:
            return self.exit_code
        if self.timed_out:
            return 124
        return 1

    @property
    def signal_sequence(self) -> tuple[str, ...]:
        return self.final_attempt.signal_sequence


class CommandRunner:
    """Runs subprocess commands with timeout policy enforcement."""

    def __init__(
        self,
        *,
        policy_registry: TimeoutPolicyRegistry | None = None,
        timeout_history: TimeoutHistory | None = None,
    ) -> None:
        self._policy_registry = policy_registry or get_timeout_policy_registry()
        self._timeout_history = timeout_history or get_timeout_history()

    def run(
        self,
        *,
        command: str | Sequence[str],
        domain: TimeoutDomain,
        cwd: str | Path | None = None,
        env: Mapping[str, str] | None = None,
        shell: bool = False,
        executable: str | None = None,
        requested_timeout_seconds: float | None = None,
        category: str | None = None,
        command_key: str | None = None,
        on_event: Callable[[CommandEvent], None] | None = None,
    ) -> CommandResult:
        """Run a command according to centralized timeout policy."""
        policy = self._policy_registry.policy_for(domain)
        resolved_cwd = Path(cwd).resolve() if cwd is not None else None
        command_text = _format_command(command)
        context = TimeoutContext(
            domain=domain,
            command=command_text,
            category=category,
            requested_timeout_seconds=requested_timeout_seconds,
        )

        key = command_key or build_command_key(
            domain,
            command_text,
            category=category,
            cwd=resolved_cwd,
        )

        attempts: list[CommandAttemptResult] = []
        started_at = time.perf_counter()

        for attempt in range(1, policy.backoff.max_attempts + 1):
            history_hint = self._timeout_history.recommended_timeout_seconds(
                key,
                policy.default_timeout_seconds,
                ceiling_seconds=policy.backoff.max_timeout_seconds,
            )
            timeout_seconds = self._policy_registry.timeout_for_attempt(
                context,
                attempt,
                history_hint_seconds=history_hint,
            )

            attempt_result = self._run_once(
                command=command,
                domain=domain,
                cwd=resolved_cwd,
                env=env,
                shell=shell,
                executable=executable,
                timeout_seconds=timeout_seconds,
                attempt=attempt,
                use_process_group=policy.use_process_group,
                terminate_grace_seconds=policy.signal.terminate_grace_seconds,
                warning_after_seconds=self._policy_registry.warning_after_seconds(
                    domain,
                    timeout_seconds,
                ),
                on_event=on_event,
            )
            attempts.append(attempt_result)
            self._timeout_history.record(
                key, attempt_result.duration_seconds, attempt_result.timed_out
            )

            if attempt_result.timed_out and self._policy_registry.should_retry_timeout(
                domain,
                attempt,
            ):
                _emit_event(
                    on_event,
                    CommandEvent(
                        event_type="retry",
                        domain=domain,
                        command=command_text,
                        attempt=attempt,
                        timeout_seconds=timeout_seconds,
                        detail="Retrying after timeout with backoff",
                    ),
                )
                continue

            break

        return CommandResult(
            domain=domain,
            command=command_text,
            attempts=tuple(attempts),
            total_duration_seconds=time.perf_counter() - started_at,
        )

    def _run_once(
        self,
        *,
        command: str | Sequence[str],
        domain: TimeoutDomain,
        cwd: Path | None,
        env: Mapping[str, str] | None,
        shell: bool,
        executable: str | None,
        timeout_seconds: float,
        attempt: int,
        use_process_group: bool,
        terminate_grace_seconds: float,
        warning_after_seconds: float | None,
        on_event: Callable[[CommandEvent], None] | None,
    ) -> CommandAttemptResult:
        command_text = _format_command(command)
        started_at = time.perf_counter()
        start_new_session = bool(use_process_group and os.name != "nt")

        process = subprocess.Popen(
            command,
            shell=shell,
            cwd=str(cwd) if cwd is not None else None,
            env=dict(env) if env is not None else None,
            executable=executable,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=start_new_session,
        )

        timed_out = False
        warning_emitted = False
        signal_sequence: list[str] = []
        stdout = ""
        stderr = ""

        try:
            if warning_after_seconds is None:
                stdout, stderr = process.communicate(timeout=timeout_seconds)
            else:
                try:
                    stdout, stderr = process.communicate(timeout=warning_after_seconds)
                except subprocess.TimeoutExpired as exc:
                    warning_emitted = True
                    stdout = _decode_stream(exc.stdout)
                    stderr = _decode_stream(exc.stderr)
                    _emit_event(
                        on_event,
                        CommandEvent(
                            event_type="warning",
                            domain=domain,
                            command=command_text,
                            attempt=attempt,
                            timeout_seconds=timeout_seconds,
                            detail="Timeout threshold approaching",
                        ),
                    )
                    remaining_timeout = max(
                        0.001,
                        timeout_seconds - warning_after_seconds,
                    )
                    stdout2, stderr2 = process.communicate(timeout=remaining_timeout)
                    stdout += _decode_stream(stdout2)
                    stderr += _decode_stream(stderr2)
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            stdout += _decode_stream(exc.stdout)
            stderr += _decode_stream(exc.stderr)

            term_out, term_err, sent_signals = self._terminate_process(
                process=process,
                use_process_group=use_process_group,
                terminate_grace_seconds=terminate_grace_seconds,
                domain=domain,
                command=command_text,
                attempt=attempt,
                timeout_seconds=timeout_seconds,
                on_event=on_event,
            )
            signal_sequence.extend(sent_signals)
            stdout += term_out
            stderr += term_err

        duration_seconds = time.perf_counter() - started_at

        return CommandAttemptResult(
            attempt=attempt,
            timeout_seconds=timeout_seconds,
            duration_seconds=duration_seconds,
            timed_out=timed_out,
            warning_emitted=warning_emitted,
            exit_code=process.returncode,
            stdout=stdout,
            stderr=stderr,
            signal_sequence=tuple(signal_sequence),
        )

    def _terminate_process(
        self,
        *,
        process: subprocess.Popen[str],
        use_process_group: bool,
        terminate_grace_seconds: float,
        domain: TimeoutDomain,
        command: str,
        attempt: int,
        timeout_seconds: float,
        on_event: Callable[[CommandEvent], None] | None,
    ) -> tuple[str, str, list[str]]:
        stdout = ""
        stderr = ""
        signals: list[str] = []

        def _send(sig: int, label: str) -> bool:
            if process.poll() is not None:
                return False
            try:
                if use_process_group and os.name != "nt":
                    os.killpg(process.pid, sig)
                else:
                    process.send_signal(sig)
                signals.append(label)
                return True
            except ProcessLookupError:
                return False
            except Exception:
                return False

        if _send(signal.SIGTERM, "SIGTERM"):
            _emit_event(
                on_event,
                CommandEvent(
                    event_type="terminate",
                    domain=domain,
                    command=command,
                    attempt=attempt,
                    timeout_seconds=timeout_seconds,
                    detail="Sent SIGTERM after timeout",
                ),
            )

        try:
            extra_out, extra_err = process.communicate(timeout=terminate_grace_seconds)
            stdout += _decode_stream(extra_out)
            stderr += _decode_stream(extra_err)
            return stdout, stderr, signals
        except subprocess.TimeoutExpired as exc:
            stdout += _decode_stream(exc.stdout)
            stderr += _decode_stream(exc.stderr)

        if _send(signal.SIGKILL, "SIGKILL"):
            _emit_event(
                on_event,
                CommandEvent(
                    event_type="kill",
                    domain=domain,
                    command=command,
                    attempt=attempt,
                    timeout_seconds=timeout_seconds,
                    detail="Sent SIGKILL after terminate grace period",
                ),
            )

        extra_out, extra_err = process.communicate()
        stdout += _decode_stream(extra_out)
        stderr += _decode_stream(extra_err)
        return stdout, stderr, signals


def _decode_stream(data: str | bytes | None) -> str:
    if data is None:
        return ""
    if isinstance(data, bytes):
        return data.decode(errors="replace")
    return data


def _format_command(command: str | Sequence[str]) -> str:
    if isinstance(command, str):
        return command
    return shlex.join([str(part) for part in command])


def _emit_event(
    on_event: Callable[[CommandEvent], None] | None,
    event: CommandEvent,
) -> None:
    if on_event is None:
        return
    on_event(event)


_DEFAULT_COMMAND_RUNNER = CommandRunner()


def get_command_runner() -> CommandRunner:
    """Return shared command runner instance."""
    return _DEFAULT_COMMAND_RUNNER
