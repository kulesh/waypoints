"""Verification pipeline orchestrator.

Runs the full verification pipeline:
1. Generate product spec from idea brief
2. Compare to reference spec
3. Generate flight plan from spec
4. Compare to reference plan
5. (Optional) Execute and compare products
"""
from __future__ import annotations

import json
import logging
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

from waypoints.models import Project
from waypoints.orchestration import JourneyCoordinator
from waypoints.verify.compare import compare_flight_plans, compare_specs
from waypoints.verify.models import (
    ComparisonVerdict,
    VerificationReport,
    VerificationStatus,
    VerificationStep,
)

logger = logging.getLogger(__name__)

REFERENCE_DIR = "reference"
VERIFY_OUTPUT_DIR = "verify-output"


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
    verbose: bool,
) -> int:
    """Create reference artifacts from current generation."""
    print("Bootstrap mode: Creating reference artifacts...")

    # Create reference directory
    reference_dir.mkdir(parents=True, exist_ok=True)

    # Create temporary project for generation
    project_name = f"verify-bootstrap-{datetime.now().strftime('%H%M%S')}"
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
    project_name = f"verify-{datetime.now().strftime('%H%M%S')}"
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
            print("\n3. Execution phase skipped (not implemented in V1)")
            step3 = VerificationStep(
                name="execution",
                status="skipped",
                message="Execution comparison not implemented in V1",
            )
            report.add_step(step3)

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
