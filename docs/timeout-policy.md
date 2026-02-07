# Timeout Policy

This document defines centralized timeout behavior for subprocess execution.

## Goals

- Keep timeout decisions in one place.
- Use domain-specific defaults instead of one global ceiling.
- Escalate predictably on timeout: warning -> `SIGTERM` -> `SIGKILL`.
- Adapt over time with observed command durations.

## Runtime Components

- `src/waypoints/runtime/timeout_policy.py`
  - Domain policies, bounds, and backoff strategy.
- `src/waypoints/runtime/timeout_history.py`
  - In-process command duration history and adaptive timeout hints.
- `src/waypoints/runtime/command_runner.py`
  - Shared subprocess runner with timeout, retries, and signal lifecycle.

## Timeout Domains

- `host_validation`
  - For fly finalize host checks (lint/test/type/format).
  - Retries on timeout with exponential backoff.
  - Context hint: Rust (`cargo`, especially `cargo clippy`) gets higher base budget.
- `llm_tool_bash`
  - For model-issued `Bash` tool calls.
  - Single attempt by default; timeout is still clamped to safe bounds.
- `flight_test`
  - For smoke-test execution in flight test suites.
- `ui_git_probe`
  - For short non-critical git status probes in TUI/debrief.
- `git_operation`
  - Reserved for longer git mutations where needed.

## Signal Lifecycle

1. Run command with domain timeout budget.
2. Emit warning event at configured threshold (fraction of timeout).
3. On hard timeout:
   - send `SIGTERM` to process group (or process),
   - wait `terminate_grace_seconds`,
   - if still running, send `SIGKILL`.
4. Capture and return stdout/stderr and signal sequence for diagnostics.

## Adaptive Timeout Behavior

- Commands are keyed by domain + category + cwd + normalized command.
- After each attempt, duration and timeout outcome are recorded.
- Future runs can increase base budget when observed durations or timeout rate rise.
- Backoff still enforces min/max clamps per domain.

## Current Integrations

- `src/waypoints/fly/receipt_finalizer.py`
  - Host validation commands now run through `CommandRunner`.
- `src/waypoints/llm/tools.py`
  - `bash` tool now uses centralized policy and signaling.
- `src/waypoints/flight_tests/runner.py`
  - Smoke tests use centralized timeout handling.
- `src/waypoints/tui/screens/fly.py`
  - Git status probes use `ui_git_probe` policy.
- `src/waypoints/orchestration/debrief.py`
  - Debrief git probes use `ui_git_probe` policy.
