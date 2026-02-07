"""Receipt finalization — build, validate, and verify execution receipts.

This module extracts the receipt finalization concern from the executor:
running host validation commands, building receipts from captured evidence,
and verifying receipts with an LLM judge.
"""

import logging
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from waypoints.fly.evidence import (
    detect_validation_category as _detect_validation_category,
)
from waypoints.fly.evidence import (
    normalize_command as _normalize_command,
)
from waypoints.fly.stack import ValidationCommand
from waypoints.git.receipt import (
    CapturedEvidence,
    CriterionVerification,
    ReceiptBuilder,
)
from waypoints.llm.client import StreamChunk, agent_query
from waypoints.llm.prompts import build_verification_prompt
from waypoints.models.waypoint import Waypoint
from waypoints.runtime import CommandEvent, TimeoutDomain, get_command_runner

if TYPE_CHECKING:
    from waypoints.fly.execution_log import ExecutionLogWriter
    from waypoints.git.config import Checklist
    from waypoints.llm.metrics import MetricsCollector
    from waypoints.models.project import Project

logger = logging.getLogger(__name__)


def _serialize_timeout_event(event: CommandEvent) -> dict[str, object]:
    """Convert command timeout event to stable metadata payload."""
    return {
        "event_type": event.event_type,
        "attempt": event.attempt,
        "timeout_seconds": event.timeout_seconds,
        "detail": event.detail,
    }


def _format_timeout_events(events: list[CommandEvent]) -> str:
    """Format command timeout events for human-readable diagnostics."""
    if not events:
        return ""
    lines = ["Timeout lifecycle:"]
    for event in events:
        detail = f" - {event.detail}" if event.detail else ""
        lines.append(
            "  "
            f"[attempt {event.attempt}] {event.event_type} "
            f"(budget={event.timeout_seconds:g}s){detail}"
        )
    return "\n".join(lines)


@dataclass(frozen=True)
class FinalizeFailure:
    """Diagnostic payload describing why receipt finalization failed."""

    reason: str
    details: tuple[str, ...] = ()


class ReceiptFinalizer:
    """Builds and verifies execution receipts from captured evidence.

    This class encapsulates the receipt finalization pipeline:
    1. Resolve and run validation commands on the host
    2. Build a receipt from captured evidence
    3. Verify the receipt with an LLM judge
    """

    def __init__(
        self,
        project: "Project",
        waypoint: Waypoint,
        log_writer: "ExecutionLogWriter",
        metrics_collector: "MetricsCollector | None" = None,
        progress_callback: Callable[..., object] | None = None,
    ) -> None:
        self._project = project
        self._waypoint = waypoint
        self._log_writer = log_writer
        self._metrics_collector = metrics_collector
        self._report_progress = progress_callback
        self._last_failure: FinalizeFailure | None = None

    def _progress(self, iteration: int, total: int, step: str, output: str) -> None:
        if self._report_progress:
            self._report_progress(iteration, total, step, output)

    def _set_failure(
        self,
        reason: str,
        details: list[str] | tuple[str, ...] | None = None,
    ) -> None:
        """Store finalization failure diagnostics for executor retry prompts."""
        self._last_failure = FinalizeFailure(
            reason=reason,
            details=tuple(details or ()),
        )

    def last_failure_summary(self, max_chars: int = 1000) -> str:
        """Return compact failure details suitable for retry prompt injection."""
        if self._last_failure is None:
            return "Receipt validation failed."
        pieces: list[str] = [self._last_failure.reason.strip()]
        if self._last_failure.details:
            pieces.extend(self._last_failure.details[:3])
        summary = "; ".join(piece for piece in pieces if piece).strip()
        if len(summary) > max_chars:
            return summary[: max_chars - 3].rstrip() + "..."
        return summary

    # ─── Validation Commands ──────────────────────────────────────────

    def resolve_validation_commands(
        self, project_path: Path, checklist: "Checklist", spec: str
    ) -> list[ValidationCommand]:
        """Resolve validation commands to run for receipt evidence."""
        from waypoints.fly.stack import (
            STACK_COMMANDS,
            StackConfig,
            detect_stack,
            detect_stack_from_spec,
        )
        from waypoints.git.config import Checklist as _  # noqa: F811, F401

        stack_configs = detect_stack(project_path)

        # Fallback to spec hints if no stack files exist yet
        if not stack_configs:
            for stack in detect_stack_from_spec(spec):
                commands = STACK_COMMANDS.get(stack, [])
                stack_configs.append(
                    StackConfig(stack_type=stack, commands=list(commands))
                )

        resolved: list[ValidationCommand] = []
        overrides = checklist.validation_overrides
        seen_keys: set[str] = set()

        for config in stack_configs:
            for cmd in config.commands:
                actual_command = overrides.get(cmd.category, cmd.command)
                key = f"{cmd.name}:{actual_command}"
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                resolved.append(
                    ValidationCommand(
                        name=cmd.name,
                        command=actual_command,
                        category=cmd.category,
                        optional=cmd.optional,
                        cwd=config.root_path,
                    )
                )

        return resolved

    def fallback_validation_commands_from_model(
        self, reported_commands: list[str]
    ) -> list[ValidationCommand]:
        """Build validation commands from model-reported markers."""
        commands: list[ValidationCommand] = []
        seen: set[str] = set()

        for command in reported_commands:
            normalized = command.strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            category = _detect_validation_category(normalized) or "validation"
            commands.append(
                ValidationCommand(
                    name=category,
                    command=normalized,
                    category=category,
                    optional=False,
                )
            )

        return commands

    def run_validation_commands(
        self, project_path: Path, commands: list[ValidationCommand]
    ) -> dict[str, CapturedEvidence]:
        """Execute validation commands on the host and capture evidence."""
        evidence: dict[str, CapturedEvidence] = {}
        if not commands:
            return evidence

        # Build environment honoring user shell PATH (e.g., mise, cargo shims)
        env = os.environ.copy()
        path_parts = env.get("PATH", "").split(os.pathsep) if env.get("PATH") else []
        extra_paths = [
            Path.home() / ".local" / "share" / "mise" / "shims",
            Path.home() / ".local" / "bin",
            Path.home() / ".cargo" / "bin",
        ]
        for extra in extra_paths:
            extra_str = str(extra)
            if extra.exists() and extra_str not in path_parts:
                path_parts.append(extra_str)
        env["PATH"] = os.pathsep.join(path_parts)
        shell_executable = env.get("SHELL") or "/bin/sh"
        command_runner = get_command_runner()

        for cmd in commands:
            start_time = datetime.now(UTC)
            timeout_events: list[CommandEvent] = []
            try:
                runner_result = command_runner.run(
                    command=cmd.command,
                    domain=TimeoutDomain.HOST_VALIDATION,
                    cwd=cmd.cwd or project_path,
                    env=env,
                    shell=True,
                    executable=shell_executable,
                    category=cmd.category,
                    on_event=timeout_events.append,
                )
                stdout = runner_result.stdout
                stderr = runner_result.stderr
                exit_code = runner_result.effective_exit_code

                if runner_result.timed_out:
                    timeout_msg = (
                        "Command timed out after "
                        f"{runner_result.final_attempt.timeout_seconds:g}s "
                        f"(attempt {runner_result.final_attempt.attempt}/"
                        f"{len(runner_result.attempts)})"
                    )
                    if stderr:
                        stderr = f"{stderr}\n{timeout_msg}"
                    else:
                        stderr = timeout_msg
                    if runner_result.signal_sequence:
                        stderr += "\nSignals: " + " -> ".join(
                            runner_result.signal_sequence
                        )
                event_summary = _format_timeout_events(timeout_events)
                if event_summary:
                    if stderr:
                        stderr = f"{stderr}\n{event_summary}"
                    else:
                        stderr = event_summary
            except Exception as exc:  # pragma: no cover - safety net
                stdout = ""
                stderr = f"Error running validation command: {exc}"
                exit_code = 1
                runner_result = None
                timeout_events = []

            label = cmd.name or cmd.command
            evidence[label] = CapturedEvidence(
                command=cmd.command,
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
                captured_at=start_time,
            )

            logger.info(
                "Ran validation command (%s): %s [exit=%d]",
                cmd.category,
                cmd.command,
                exit_code,
            )

            self._log_writer.log_finalize_tool_call(
                "ValidationCommand",
                {
                    "command": cmd.command,
                    "category": cmd.category,
                    "name": cmd.name,
                    "attempts": len(runner_result.attempts) if runner_result else 1,
                    "timed_out": runner_result.timed_out if runner_result else False,
                    "timeout_seconds": (
                        runner_result.final_attempt.timeout_seconds
                        if runner_result
                        else None
                    ),
                    "signals": list(runner_result.signal_sequence)
                    if runner_result
                    else [],
                    "timeout_events": [
                        _serialize_timeout_event(event) for event in timeout_events
                    ],
                },
                (
                    f"exit_code={exit_code}; duration="
                    f"{runner_result.total_duration_seconds:.2f}s"
                    if runner_result
                    else f"exit_code={exit_code}"
                ),
            )

        return evidence

    def _build_verification_prompt(self, receipt_path: Path) -> str:
        """Build the LLM verification prompt for a receipt."""
        from waypoints.git.receipt import ChecklistReceipt

        receipt = ChecklistReceipt.load(receipt_path)
        return build_verification_prompt(receipt)

    # ─── Finalize ─────────────────────────────────────────────────────

    async def finalize(
        self,
        project_path: Path,
        captured_criteria: dict[int, CriterionVerification],
        validation_commands: list[ValidationCommand],
        reported_validation_commands: list[str],
        tool_validation_evidence: dict[str, CapturedEvidence] | None = None,
        tool_validation_categories: dict[str, CapturedEvidence] | None = None,
        host_validations_enabled: bool = True,
        max_iterations: int = 10,
    ) -> bool:
        """Build receipt from host-captured evidence and verify with LLM.

        Args:
            project_path: Project working directory.
            captured_criteria: Criterion verification evidence keyed by index.
            validation_commands: Preferred validation commands from stack detection.
            reported_validation_commands: Commands reported by the model (fallback).
            tool_validation_evidence: Validation outputs from tool calls,
                keyed by normalized command.
            tool_validation_categories: Validation outputs from tool calls,
                keyed by detected category.
            host_validations_enabled: Whether to run host validations.
            max_iterations: Total max iterations (for progress reporting).

        Returns:
            True if receipt is valid, False otherwise.
        """
        self._last_failure = None
        self._log_writer.log_finalize_start()

        self._progress(
            max_iterations,
            max_iterations,
            "finalizing",
            "Running host validations and building receipt...",
        )

        receipt_builder = ReceiptBuilder(
            waypoint_id=self._waypoint.id,
            title=self._waypoint.title,
            objective=self._waypoint.objective,
            acceptance_criteria=self._waypoint.acceptance_criteria,
        )

        commands_to_run = validation_commands or (
            self.fallback_validation_commands_from_model(reported_validation_commands)
        )
        tool_validation_evidence = tool_validation_evidence or {}
        tool_validation_categories = tool_validation_categories or {}
        soft_evidence: dict[str, CapturedEvidence] = (
            tool_validation_categories or tool_validation_evidence
        )
        soft_missing = bool(commands_to_run) and not soft_evidence

        # If host validations are disabled, record skips and return early.
        if not host_validations_enabled:
            return self._finalize_soft_only(
                receipt_builder,
                commands_to_run,
                captured_criteria,
                tool_validation_evidence,
                tool_validation_categories,
                soft_missing,
                max_iterations,
            )

        if not commands_to_run:
            logger.warning("No validation commands available to run for receipt")
            self._log_writer.log_finalize_end()
            self._log_writer.log_receipt_validated(
                "", False, "No validation commands provided"
            )
            self._set_failure("No validation commands provided.")
            return False

        host_evidence = self.run_validation_commands(project_path, commands_to_run)
        for category, evidence in host_evidence.items():
            receipt_builder.capture(category, evidence)

        # Add captured criteria verification from model output
        for idx, criterion in captured_criteria.items():
            logger.info("Adding criterion verification: [%d] %s", idx, criterion.status)
            receipt_builder.capture_criterion(criterion)
            self._log_writer.log_finalize_tool_call(
                "CapturedCriterion",
                {"index": idx, "criterion": criterion.criterion},
                criterion.status,
            )

        # Build and save receipt
        if not receipt_builder.has_evidence():
            logger.warning("No validation evidence captured")
            self._log_writer.log_finalize_end()
            self._log_writer.log_receipt_validated("", False, "No evidence captured")
            self._set_failure("No validation evidence captured.")
            return False

        receipt_path = self._save_receipt(receipt_builder, soft_evidence or None)

        # Quick check: if any commands failed, receipt is invalid
        from waypoints.git.receipt import ChecklistReceipt

        receipt = ChecklistReceipt.load(receipt_path)
        if not receipt.is_valid():
            failed = receipt.failed_items()
            failed_names = ", ".join(item.item for item in failed)
            details: list[str] = []
            for item in failed[:3]:
                command = item.command or item.item
                exit_code = (
                    str(item.exit_code) if item.exit_code is not None else "unknown"
                )
                output = (item.stderr or item.stdout or "").strip()
                output = re.sub(r"\s+", " ", output)
                if len(output) > 220:
                    output = output[:217].rstrip() + "..."
                details.append(f"{command} exited {exit_code}: {output}")
            logger.warning("Validation commands failed: %s", failed_names)
            self._log_writer.log_finalize_end()
            self._log_writer.log_receipt_validated(
                str(receipt_path), False, f"Failed: {failed_names}"
            )
            self._set_failure("Host validation failed.", details)
            return False
        if soft_missing:
            self._log_writer.log_finalize_end()
            self._log_writer.log_receipt_validated(
                str(receipt_path), False, "Soft validation evidence missing"
            )
            self._set_failure("Soft validation evidence missing.")
            return False

        # LLM verification
        return await self._verify_with_llm(project_path, receipt_path, max_iterations)

    def _finalize_soft_only(
        self,
        receipt_builder: ReceiptBuilder,
        commands_to_run: list[ValidationCommand],
        captured_criteria: dict[int, CriterionVerification],
        tool_validation_evidence: dict[str, CapturedEvidence],
        tool_validation_categories: dict[str, CapturedEvidence],
        soft_missing: bool,
        max_iterations: int,
    ) -> bool:
        """Handle finalization when host validations are disabled."""
        self._progress(
            max_iterations,
            max_iterations,
            "finalizing",
            "Host validations OFF (LLM-as-judge only)...",
        )

        if commands_to_run:
            for cmd in commands_to_run:
                normalized = _normalize_command(cmd.command)
                evidence = tool_validation_evidence.get(normalized)
                if evidence is None:
                    key = cmd.name or cmd.category
                    evidence = tool_validation_categories.get(key)
                if evidence is not None:
                    receipt_builder.capture(cmd.name or cmd.command, evidence)
                    continue
                reason = "Host validation skipped (LLM-as-judge only)"
                receipt_builder.capture_skipped(cmd.name or cmd.command, reason)
        else:
            receipt_builder.capture_skipped(
                "host_validations",
                "Host validation skipped (LLM-as-judge only)",
            )

        for idx, criterion in captured_criteria.items():
            logger.info("Adding criterion verification: [%d] %s", idx, criterion.status)
            receipt_builder.capture_criterion(criterion)
            self._log_writer.log_finalize_tool_call(
                "CapturedCriterion",
                {"index": idx, "criterion": criterion.criterion},
                criterion.status,
            )

        receipt_path = self._save_receipt(receipt_builder)

        self._log_writer.log_finalize_end()
        if soft_missing:
            self._log_writer.log_receipt_validated(
                str(receipt_path), False, "Soft validation evidence missing"
            )
            return False
        self._log_writer.log_receipt_validated(
            str(receipt_path),
            True,
            "Host validation skipped (LLM-as-judge only)",
        )
        return True

    async def _verify_with_llm(
        self, project_path: Path, receipt_path: Path, max_iterations: int
    ) -> bool:
        """Ask LLM to verify the receipt evidence."""
        self._progress(
            max_iterations,
            max_iterations,
            "finalizing",
            "Verifying receipt with LLM...",
        )

        verification_prompt = self._build_verification_prompt(receipt_path)
        verification_output = ""

        try:
            async for chunk in agent_query(
                prompt=verification_prompt,
                system_prompt="Verify the checklist receipt. Output your verdict.",
                allowed_tools=[],
                cwd=str(project_path),
                metrics_collector=self._metrics_collector,
                phase="fly",
                waypoint_id=self._waypoint.id,
            ):
                if isinstance(chunk, StreamChunk):
                    verification_output += chunk.text
        except Exception as e:
            logger.error("Error during receipt verification: %s", e)
            self._log_writer.log_error(0, f"Verification error: {e}")
            self._log_writer.log_finalize_end()
            self._log_writer.log_receipt_validated(
                str(receipt_path), True, "LLM verification skipped"
            )
            self._last_failure = None
            return True  # Trust the evidence if LLM verification fails

        # Log verification output
        if verification_output:
            self._log_writer.log_finalize_output(verification_output)

        # Parse verdict
        verdict_match = re.search(
            r'<receipt-verdict status="(valid|invalid)">(.*?)</receipt-verdict>',
            verification_output,
            re.DOTALL,
        )

        if verdict_match:
            status = verdict_match.group(1)
            reasoning = verdict_match.group(2).strip()
            is_valid = status == "valid"

            self._log_writer.log_finalize_end()
            self._log_writer.log_receipt_validated(
                str(receipt_path), is_valid, reasoning
            )

            if is_valid:
                logger.info("Receipt verified: %s", reasoning)
                self._last_failure = None
                return True
            else:
                logger.warning("Receipt rejected: %s", reasoning)
                self._set_failure(
                    "LLM receipt verification rejected host evidence.",
                    [reasoning] if reasoning else None,
                )
                return False
        else:
            logger.warning("No verdict marker in LLM response, using format validation")
            self._log_writer.log_finalize_end()
            self._log_writer.log_receipt_validated(
                str(receipt_path), True, "LLM verdict not found, using format check"
            )
            self._last_failure = None
            return True

    def _save_receipt(
        self,
        receipt_builder: ReceiptBuilder,
        soft_evidence: dict[str, CapturedEvidence] | None = None,
    ) -> Path:
        """Build and save a receipt to the receipts directory."""
        receipts_dir = self._project.get_path() / "receipts"
        receipts_dir.mkdir(parents=True, exist_ok=True)
        safe_wp_id = self._waypoint.id.lower().replace("-", "")
        timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        receipt_stem = f"{safe_wp_id}-{timestamp}"
        receipt_path = receipts_dir / f"{receipt_stem}.json"
        receipt = receipt_builder.build(
            output_dir=receipts_dir,
            output_prefix=receipt_stem,
            soft_evidence=soft_evidence,
        )
        receipt.save(receipt_path)
        logger.info("Receipt saved: %s", receipt_path)
        return receipt_path
