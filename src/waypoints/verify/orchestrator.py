"""Verification pipeline orchestrator.

Runs the full verification pipeline:
1. Generate product spec from idea brief
2. Compare to reference spec
3. Generate flight plan from spec
4. Compare to reference plan
5. (Optional) Execute and compare products
"""

import asyncio
import hashlib
import json
import logging
import shutil
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from waypoints.fly.executor import ExecutionResult
from waypoints.fly.intervention import InterventionNeededError
from waypoints.models import Project
from waypoints.models.waypoint import WaypointStatus
from waypoints.orchestration import JourneyCoordinator
from waypoints.verify.compare import compare_flight_plans, compare_specs
from waypoints.verify.models import (
    ComparisonResult,
    ComparisonVerdict,
    VerificationReport,
    VerificationStatus,
    VerificationStep,
)

logger = logging.getLogger(__name__)

REFERENCE_DIR = "reference"
VERIFY_OUTPUT_DIR = "verify-output"
REFERENCE_EXECUTION_SUMMARY = "execution-summary.json"
VERIFY_EXECUTION_SUMMARY = "execution-summary.json"
EXCLUDED_MANIFEST_DIRS = {
    ".git",
    ".waypoints",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "docs",
    "sessions",
    "receipts",
}
EXCLUDED_MANIFEST_FILES = {
    "project.json",
    "flight-plan.jsonl",
}


@dataclass(frozen=True)
class ExecutionSnapshot:
    """Execution result snapshot used for product-level comparison."""

    completed_waypoints: list[str]
    failed_waypoints: list[str]
    skipped_waypoints: list[str]
    pending_waypoints: list[str]
    in_progress_waypoints: list[str]
    file_manifest: dict[str, str]

    @property
    def all_complete(self) -> bool:
        return (
            not self.failed_waypoints
            and not self.pending_waypoints
            and not self.in_progress_waypoints
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "completed_waypoints": self.completed_waypoints,
            "failed_waypoints": self.failed_waypoints,
            "skipped_waypoints": self.skipped_waypoints,
            "pending_waypoints": self.pending_waypoints,
            "in_progress_waypoints": self.in_progress_waypoints,
            "all_complete": self.all_complete,
            "file_manifest": self.file_manifest,
            "file_count": len(self.file_manifest),
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "ExecutionSnapshot":
        return cls(
            completed_waypoints=list(data.get("completed_waypoints", [])),
            failed_waypoints=list(data.get("failed_waypoints", [])),
            skipped_waypoints=list(data.get("skipped_waypoints", [])),
            pending_waypoints=list(data.get("pending_waypoints", [])),
            in_progress_waypoints=list(data.get("in_progress_waypoints", [])),
            file_manifest=dict(data.get("file_manifest", {})),
        )


def _collect_product_manifest(project_path: Path) -> dict[str, str]:
    """Collect deterministic file hashes for product artifacts only."""
    manifest: dict[str, str] = {}
    for path in sorted(project_path.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        rel = path.relative_to(project_path)
        if any(part in EXCLUDED_MANIFEST_DIRS for part in rel.parts[:-1]):
            continue
        if rel.name in EXCLUDED_MANIFEST_FILES or rel.name == ".DS_Store":
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        manifest[rel.as_posix()] = digest
    return manifest


def _execute_flight_plan(
    coordinator: JourneyCoordinator,
    *,
    max_iterations: int = 10,
    verbose: bool = False,
) -> ExecutionSnapshot:
    """Execute the generated flight plan and capture a stable snapshot."""
    while True:
        waypoint = coordinator.select_next_waypoint(include_failed=False)
        if waypoint is None:
            break

        if verbose:
            print(f"   Executing {waypoint.id} - {waypoint.title}")
        try:
            result = asyncio.run(
                coordinator.execute_waypoint(
                    waypoint,
                    max_iterations=max_iterations,
                )
            )
            coordinator.handle_execution_result(waypoint, result)
            if verbose:
                marker = "✓" if result == ExecutionResult.SUCCESS else "✗"
                print(f"     {marker} {result.value}")
        except InterventionNeededError as err:
            logger.warning("Execution intervention for %s: %s", waypoint.id, err)
            coordinator.mark_waypoint_status(waypoint, WaypointStatus.FAILED)
            if verbose:
                print("     ⚠ intervention needed")
        except Exception:
            logger.exception("Execution error while running %s", waypoint.id)
            coordinator.mark_waypoint_status(waypoint, WaypointStatus.FAILED)
            if verbose:
                print("     ✗ execution error")

    flight_plan = coordinator.flight_plan
    if flight_plan is None:
        raise RuntimeError("No flight plan loaded after execution")

    completed = sorted(
        wp.id
        for wp in flight_plan.waypoints
        if wp.status in (WaypointStatus.COMPLETE, WaypointStatus.SKIPPED)
    )
    failed = sorted(
        wp.id for wp in flight_plan.waypoints if wp.status == WaypointStatus.FAILED
    )
    skipped = sorted(
        wp.id for wp in flight_plan.waypoints if wp.status == WaypointStatus.SKIPPED
    )
    pending = sorted(
        wp.id for wp in flight_plan.waypoints if wp.status == WaypointStatus.PENDING
    )
    in_progress = sorted(
        wp.id for wp in flight_plan.waypoints if wp.status == WaypointStatus.IN_PROGRESS
    )
    manifest = _collect_product_manifest(coordinator.project.get_path())

    return ExecutionSnapshot(
        completed_waypoints=completed,
        failed_waypoints=failed,
        skipped_waypoints=skipped,
        pending_waypoints=pending,
        in_progress_waypoints=in_progress,
        file_manifest=manifest,
    )


def _compare_execution_snapshots(
    reference: ExecutionSnapshot,
    generated: ExecutionSnapshot,
) -> ComparisonResult:
    """Compare execution outcomes and resulting product files."""
    differences: list[str] = []

    if reference.completed_waypoints != generated.completed_waypoints:
        differences.append(
            "completed waypoints differ "
            f"(ref={len(reference.completed_waypoints)} "
            f"new={len(generated.completed_waypoints)})"
        )
    if reference.failed_waypoints != generated.failed_waypoints:
        differences.append(
            "failed waypoints differ "
            f"(ref={reference.failed_waypoints} new={generated.failed_waypoints})"
        )
    if reference.pending_waypoints != generated.pending_waypoints:
        differences.append(
            "pending waypoints differ "
            f"(ref={reference.pending_waypoints} new={generated.pending_waypoints})"
        )
    if reference.in_progress_waypoints != generated.in_progress_waypoints:
        differences.append(
            "in-progress waypoints differ "
            f"(ref={reference.in_progress_waypoints} "
            f"new={generated.in_progress_waypoints})"
        )

    ref_paths = set(reference.file_manifest.keys())
    gen_paths = set(generated.file_manifest.keys())
    missing = sorted(ref_paths - gen_paths)
    extra = sorted(gen_paths - ref_paths)
    changed = sorted(
        path
        for path in (ref_paths & gen_paths)
        if reference.file_manifest[path] != generated.file_manifest[path]
    )
    if missing:
        differences.append(f"missing files ({len(missing)}): {', '.join(missing[:5])}")
    if extra:
        differences.append(f"extra files ({len(extra)}): {', '.join(extra[:5])}")
    if changed:
        differences.append(
            f"changed file content ({len(changed)}): {', '.join(changed[:5])}"
        )

    equivalent = len(differences) == 0
    verdict = (
        ComparisonVerdict.EQUIVALENT
        if equivalent
        else ComparisonVerdict.DIFFERENT
    )
    rationale = (
        "Execution outcomes and product file manifest match reference."
        if equivalent
        else "Execution outcomes or product files differ from reference."
    )
    confidence = 0.95 if equivalent else 0.8
    return ComparisonResult(
        verdict=verdict,
        confidence=confidence,
        rationale=rationale,
        differences=differences,
        artifact_type="product",
    )


def _find_idea_brief(genspec_dir: Path) -> Path | None:
    """Find idea brief file in genspec directory."""
    # Check docs/ subdirectory first
    docs_dir = genspec_dir / "docs"
    if docs_dir.exists():
        briefs = sorted(docs_dir.glob("idea-brief-*.md"), reverse=True)
        if briefs:
            return briefs[0]

    # Check root directory
    briefs = sorted(genspec_dir.glob("idea-brief*.md"), reverse=True)
    if briefs:
        return briefs[0]

    return None


def _find_product_spec(genspec_dir: Path) -> Path | None:
    """Find product spec file in genspec directory."""
    docs_dir = genspec_dir / "docs"
    if docs_dir.exists():
        specs = sorted(docs_dir.glob("product-spec-*.md"), reverse=True)
        if specs:
            return specs[0]

    specs = sorted(genspec_dir.glob("product-spec*.md"), reverse=True)
    if specs:
        return specs[0]

    return None


def _find_flight_plan(genspec_dir: Path) -> Path | None:
    """Find flight plan file in genspec directory."""
    plan_path = genspec_dir / "flight-plan.jsonl"
    if plan_path.exists():
        return plan_path
    return None


def run_verification(
    genspec_dir: Path,
    bootstrap: bool = False,
    skip_fly: bool = True,
    verbose: bool = False,
) -> int:
    """Run verification pipeline.

    Args:
        genspec_dir: Directory containing genspec artifacts
        bootstrap: If True, create reference from current generation
        skip_fly: If True, skip execution phase (compare artifacts only)
        verbose: If True, show detailed progress

    Returns:
        Exit code: 0 = pass, 1 = fail, 2 = error
    """
    genspec_dir = genspec_dir.resolve()

    if not genspec_dir.exists():
        print(f"Error: Directory not found: {genspec_dir}", file=sys.stderr)
        return 2

    # Find idea brief (required input)
    brief_path = _find_idea_brief(genspec_dir)
    if not brief_path:
        print("Error: No idea-brief found in genspec directory", file=sys.stderr)
        print("  Expected: docs/idea-brief-*.md or idea-brief*.md", file=sys.stderr)
        return 2

    brief_content = brief_path.read_text()
    if verbose:
        print(f"Found idea brief: {brief_path}")
        print(f"  {len(brief_content)} chars")

    reference_dir = genspec_dir / REFERENCE_DIR
    output_dir = genspec_dir / VERIFY_OUTPUT_DIR

    if bootstrap:
        return _run_bootstrap(
            genspec_dir=genspec_dir,
            brief_content=brief_content,
            reference_dir=reference_dir,
            skip_fly=skip_fly,
            verbose=verbose,
        )
    else:
        return _run_verify(
            genspec_dir=genspec_dir,
            brief_content=brief_content,
            reference_dir=reference_dir,
            output_dir=output_dir,
            skip_fly=skip_fly,
            verbose=verbose,
        )


def _run_bootstrap(
    genspec_dir: Path,
    brief_content: str,
    reference_dir: Path,
    skip_fly: bool,
    verbose: bool,
) -> int:
    """Create reference artifacts from current generation."""
    print("Bootstrap mode: Creating reference artifacts...")

    # Create reference directory
    reference_dir.mkdir(parents=True, exist_ok=True)

    # Create temporary project for generation
    project_name = f"verify-bootstrap-{datetime.now(UTC).strftime('%H%M%S')}"
    project = Project.create(project_name, idea=brief_content[:200])

    try:
        coordinator = JourneyCoordinator(project=project)

        # Generate product spec
        print("\n1. Generating product spec from idea brief...")
        start = time.time()

        def on_chunk(chunk: str) -> None:
            if verbose:
                print(chunk, end="", flush=True)

        spec_content = coordinator.generate_product_spec(
            brief=brief_content,
            on_chunk=on_chunk,
        )
        duration = time.time() - start

        if verbose:
            print()
        print(f"   Generated {len(spec_content)} chars in {duration:.1f}s")

        # Save reference spec
        ref_spec_path = reference_dir / "product-spec.md"
        ref_spec_path.write_text(spec_content)
        print(f"   Saved: {ref_spec_path}")

        # Generate flight plan
        print("\n2. Generating flight plan from spec...")
        start = time.time()

        flight_plan = coordinator.generate_flight_plan(
            spec=spec_content,
            on_chunk=on_chunk,
        )
        duration = time.time() - start

        if verbose:
            print()
        print(f"   Generated {len(flight_plan.waypoints)} waypoints in {duration:.1f}s")

        # Save reference plan
        ref_plan_path = reference_dir / "flight-plan.json"
        ref_plan_path.write_text(json.dumps(flight_plan.to_dict(), indent=2))
        print(f"   Saved: {ref_plan_path}")

        # Copy idea brief to reference
        ref_brief_path = reference_dir / "idea-brief.md"
        ref_brief_path.write_text(brief_content)
        print(f"   Saved: {ref_brief_path}")

        if skip_fly:
            print("\n3. Skipping execution phase (--skip-fly)")
        else:
            print("\n3. Executing reference flight plan...")
            start = time.time()
            execution_snapshot = _execute_flight_plan(coordinator, verbose=verbose)
            duration = time.time() - start
            ref_exec_path = reference_dir / REFERENCE_EXECUTION_SUMMARY
            ref_exec_path.write_text(
                json.dumps(execution_snapshot.to_dict(), indent=2),
                encoding="utf-8",
            )
            print(
                "   Saved: "
                f"{ref_exec_path} ({len(execution_snapshot.file_manifest)} files, "
                f"{duration:.1f}s)"
            )

        print("\nBootstrap complete!")
        print(f"Reference artifacts saved to: {reference_dir}")
        return 0

    finally:
        # Clean up temporary project
        project_path = project.get_path()
        if project_path.exists():
            shutil.rmtree(project_path)


def _run_verify(
    genspec_dir: Path,
    brief_content: str,
    reference_dir: Path,
    output_dir: Path,
    skip_fly: bool,
    verbose: bool,
) -> int:
    """Run verification against reference artifacts."""
    if not reference_dir.exists():
        print("Error: No reference found. Run with --bootstrap first.", file=sys.stderr)
        return 2

    # Load reference artifacts
    ref_spec_path = reference_dir / "product-spec.md"
    ref_plan_path = reference_dir / "flight-plan.json"

    if not ref_spec_path.exists():
        print(f"Error: Reference spec not found: {ref_spec_path}", file=sys.stderr)
        return 2
    if not ref_plan_path.exists():
        print(f"Error: Reference plan not found: {ref_plan_path}", file=sys.stderr)
        return 2

    ref_spec = ref_spec_path.read_text()
    ref_plan = json.loads(ref_plan_path.read_text())
    ref_execution: ExecutionSnapshot | None = None
    if not skip_fly:
        ref_exec_path = reference_dir / REFERENCE_EXECUTION_SUMMARY
        if not ref_exec_path.exists():
            print(
                "Error: Reference execution snapshot not found: "
                f"{ref_exec_path}",
                file=sys.stderr,
            )
            print(
                "Run bootstrap without --skip-fly to create execution reference.",
                file=sys.stderr,
            )
            return 2
        ref_execution = ExecutionSnapshot.from_dict(
            json.loads(ref_exec_path.read_text(encoding="utf-8"))
        )

    print("Verification mode: Comparing against reference...")

    # Create output directory
    output_dir.mkdir(parents=True, exist_ok=True)

    # Initialize report
    report = VerificationReport(
        genspec_path=str(genspec_dir),
        reference_path=str(reference_dir),
        overall_status=VerificationStatus.PASS,
    )

    # Create temporary project for generation
    project_name = f"verify-{datetime.now(UTC).strftime('%H%M%S')}"
    project = Project.create(project_name, idea=brief_content[:200])

    try:
        coordinator = JourneyCoordinator(project=project)

        # Step 1: Generate and compare product spec
        print("\n1. Generating product spec...")
        start = time.time()

        def on_chunk(chunk: str) -> None:
            if verbose:
                print(chunk, end="", flush=True)

        new_spec = coordinator.generate_product_spec(
            brief=brief_content,
            on_chunk=on_chunk,
        )
        gen_duration = time.time() - start

        if verbose:
            print()

        # Save generated spec
        (output_dir / "product-spec.md").write_text(new_spec)

        print("   Comparing to reference spec...")
        start = time.time()
        spec_result = compare_specs(new_spec, ref_spec, verbose=verbose)
        cmp_duration = time.time() - start

        spec_passed = spec_result.verdict == ComparisonVerdict.EQUIVALENT
        step1 = VerificationStep(
            name="spec_comparison",
            status="pass" if spec_passed else "fail",
            result=spec_result,
            message=spec_result.rationale,
            duration_seconds=gen_duration + cmp_duration,
        )
        report.add_step(step1)

        _print_step_result("Spec comparison", step1)

        # Step 2: Generate and compare flight plan
        print("\n2. Generating flight plan...")
        start = time.time()

        new_plan = coordinator.generate_flight_plan(
            spec=new_spec,
            on_chunk=on_chunk,
        )
        gen_duration = time.time() - start

        if verbose:
            print()

        # Save generated plan
        (output_dir / "flight-plan.json").write_text(
            json.dumps(new_plan.to_dict(), indent=2)
        )

        print("   Comparing to reference plan...")
        start = time.time()
        plan_result = compare_flight_plans(
            new_plan.to_dict(), ref_plan, verbose=verbose
        )
        cmp_duration = time.time() - start

        plan_passed = plan_result.verdict == ComparisonVerdict.EQUIVALENT
        step2 = VerificationStep(
            name="plan_comparison",
            status="pass" if plan_passed else "fail",
            result=plan_result,
            message=plan_result.rationale,
            duration_seconds=gen_duration + cmp_duration,
        )
        report.add_step(step2)

        _print_step_result("Plan comparison", step2)

        # Step 3: Execute (if not skipped)
        if not skip_fly:
            print("\n3. Executing and comparing product outputs...")
            start = time.time()
            execution_snapshot = _execute_flight_plan(coordinator, verbose=verbose)
            duration = time.time() - start
            assert ref_execution is not None
            execution_result = _compare_execution_snapshots(
                ref_execution,
                execution_snapshot,
            )
            (output_dir / VERIFY_EXECUTION_SUMMARY).write_text(
                json.dumps(execution_snapshot.to_dict(), indent=2),
                encoding="utf-8",
            )
            step3 = VerificationStep(
                name="execution_comparison",
                status=(
                    "pass"
                    if execution_result.verdict == ComparisonVerdict.EQUIVALENT
                    else "fail"
                ),
                result=execution_result,
                message=execution_result.rationale,
                duration_seconds=duration,
            )
            report.add_step(step3)
            _print_step_result("Execution comparison", step3)

        # Finalize report
        report.finalize()

        # Save report
        report_path = output_dir / "verification-report.json"
        report_path.write_text(json.dumps(report.to_dict(), indent=2))

        # Print summary
        print("\n" + "=" * 50)
        print(f"Verification Result: {report.overall_status.value.upper()}")
        print("=" * 50)
        print(f"Report saved: {report_path}")

        return 0 if report.overall_status == VerificationStatus.PASS else 1

    finally:
        # Clean up temporary project
        project_path = project.get_path()
        if project_path.exists():
            shutil.rmtree(project_path)


def _print_step_result(name: str, step: VerificationStep) -> None:
    """Print a step result."""
    if step.status == "pass":
        status_icon = "✓"
    elif step.status == "fail":
        status_icon = "✗"
    else:
        status_icon = "○"
    print(f"   {status_icon} {name}: {step.status}")
    if step.result:
        print(f"     Verdict: {step.result.verdict.value}")
        print(f"     Confidence: {step.result.confidence:.0%}")
        if step.result.differences:
            print("     Differences:")
            for diff in step.result.differences[:3]:  # Show first 3
                print(f"       - {diff}")
