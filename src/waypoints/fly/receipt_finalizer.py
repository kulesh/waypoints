"""Receipt finalization — build, validate, and verify execution receipts.

This module extracts the receipt finalization concern from the executor:
running host validation commands, building receipts from captured evidence,
and verifying receipts with an LLM judge.
"""

import logging
import os
import re
import subprocess
from collections.abc import Callable
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

if TYPE_CHECKING:
    from waypoints.fly.execution_log import ExecutionLogWriter
    from waypoints.git.config import Checklist
    from waypoints.llm.metrics import MetricsCollector
    from waypoints.models.project import Project

logger = logging.getLogger(__name__)


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

    def _progress(self, iteration: int, total: int, step: str, output: str) -> None:
        if self._report_progress:
            self._report_progress(iteration, total, step, output)

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

        def _decode_output(data: bytes | str | None) -> str:
            if isinstance(data, bytes):
                return data.decode(errors="replace")
            return data or ""

        for cmd in commands:
            start_time = datetime.now(UTC)
            try:
                result = subprocess.run(
                    cmd.command,
                    shell=True,
                    capture_output=True,
                    text=True,
                    cwd=cmd.cwd or project_path,
                    env=env,
                    executable=shell_executable,
                    timeout=300,
                )
                stdout = _decode_output(result.stdout)
                stderr = _decode_output(result.stderr)
                exit_code = result.returncode
            except subprocess.TimeoutExpired as e:
                stdout = _decode_output(e.stdout)
                stderr = _decode_output(e.stderr) + "\nCommand timed out"
                exit_code = 124
            except Exception as e:  # pragma: no cover - safety net
                stdout = ""
                stderr = f"Error running validation command: {e}"
                exit_code = 1

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
                },
                f"exit_code={exit_code}",
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
            return False

        receipt_path = self._save_receipt(receipt_builder, soft_evidence or None)

        # Quick check: if any commands failed, receipt is invalid
        from waypoints.git.receipt import ChecklistReceipt

        receipt = ChecklistReceipt.load(receipt_path)
        if not receipt.is_valid():
            failed = receipt.failed_items()
            failed_names = ", ".join(item.item for item in failed)
            logger.warning("Validation commands failed: %s", failed_names)
            self._log_writer.log_finalize_end()
            self._log_writer.log_receipt_validated(
                str(receipt_path), False, f"Failed: {failed_names}"
            )
            return False
        if soft_missing:
            self._log_writer.log_finalize_end()
            self._log_writer.log_receipt_validated(
                str(receipt_path), False, "Soft validation evidence missing"
            )
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
                return True
            else:
                logger.warning("Receipt rejected: %s", reasoning)
                return False
        else:
            logger.warning("No verdict marker in LLM response, using format validation")
            self._log_writer.log_finalize_end()
            self._log_writer.log_receipt_validated(
                str(receipt_path), True, "LLM verdict not found, using format check"
            )
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
