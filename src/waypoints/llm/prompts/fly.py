"""FLY phase prompts for waypoint execution and verification."""

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from waypoints.git.config import Checklist
    from waypoints.git.receipt import ChecklistReceipt
    from waypoints.models.waypoint import Waypoint


def build_execution_prompt(
    waypoint: "Waypoint",
    spec: str,
    project_path: Path,
    checklist: "Checklist",
) -> str:
    """Build the execution prompt for a waypoint.

    Args:
        waypoint: The waypoint to execute
        spec: Product specification content
        project_path: Path to the project directory
        checklist: Pre-completion checklist to verify

    Returns:
        Formatted execution prompt string
    """
    # Format criteria with indices for tracking
    criteria_list = "\n".join(
        f"- [ ] [{i}] {c}" for i, c in enumerate(waypoint.acceptance_criteria)
    )

    checklist_items = "\n".join(f"- {item}" for item in checklist.items)
    resolution_notes = ""
    if waypoint.resolution_notes:
        notes_list = "\n".join(f"- {note}" for note in waypoint.resolution_notes)
        resolution_notes = "\n## Resolution Notes (must honor)\n" f"{notes_list}\n"

    return f"""## Current Waypoint: {waypoint.id}
{waypoint.title}

## Objective
{waypoint.objective}

## Acceptance Criteria (must all pass)
{criteria_list}
{resolution_notes}

## Product Spec Summary
## TODO: We should use proper summary of the spec not prefix!
{spec[:2000]}{"..." if len(spec) > 2000 else ""}

## Working Directory
{project_path}

## Instructions
You are implementing a software waypoint. Your task is to:

1. Read any existing code in the project to understand the codebase
2. Create/modify code files to achieve the waypoint objective
3. Write tests that verify the acceptance criteria
4. Run tests with `pytest -v` and ensure they pass
5. If tests fail, analyze the failure and fix the code
6. Iterate until all acceptance criteria are met

## Execution Protocol (Structured Stage Reports)
Work through stages and report each using this exact format:

Stages (in order, repeat FIX as needed):
- analyze, plan, test, code, run, fix, lint, report

After each stage, output a structured report as JSON wrapped in tags:
```xml
<execution-stage>
{{"stage":"analyze|plan|test|code|run|fix|lint|report",
 "success":true,
 "output":"brief description of what you did",
 "artifacts":["file1.py","file2.py"],
 "next_stage":"next_stage_name or null"}}
</execution-stage>
```

Keep `output` brief and factual. Use `artifacts` for files created/modified.

**CRITICAL SAFETY RULES:**
- **STAY IN THE PROJECT**: Only read/write files within {project_path}
- **NEVER** use absolute paths starting with /Users, /home, /tmp, or similar
- **NEVER** access parent directories with ../ to escape the project
- **NEVER** modify files outside the project directory
- All file operations MUST be relative to the project root
- Violations will cause immediate termination and rollback

**Implementation Guidelines:**
- You are the sole owner of this codebase in this session
- If the project is empty, that's expected - build from scratch using the spec
- Work iteratively - read, write, test, fix
- Keep changes minimal and focused on the waypoint objective
- Write idiomatic code for the stack you are working in
- Follow existing code patterns and style in the project
- Create tests before or alongside implementation
- Author tests that capture intent/behavior (not shallow or superficial)
- Run tests after each significant change
- Assume all code (good or bad) is yours to maintain. Do not ignore existing
  errors/warnings because they are “pre-existing” — fix them unless doing so
  would derail the waypoint.

## Pre-Completion Checklist
Before marking this waypoint complete, you must run and **fix** anything
uncovered by these checks. Treat any warning/error as a failure that must be
resolved before completion. Verify the following:
{checklist_items}

Report each checklist item using this structure (one block per item):
```xml
<checklist-item>
<item>Code passes linting</item>
<command>the command(s) you ran</command>
<output>
Key output showing the result
</output>
<analysis>1-2 sentences interpreting the output and whether the item passed</analysis>
<status>pass|fail</status>
</checklist-item>
```

## Validation Commands

Run the appropriate validation commands for the project's stack. Any
warnings/errors must be fixed before completion; include evidence of fixes in
your outputs:
- **Tests**: Run the test suite (e.g., `pytest`, `cargo test`, `npm test`, `go test`)
- **Linting**: Run the linter (e.g., `ruff check`, `cargo clippy`, `eslint`)
- **Formatting**: Check code formatting (e.g., `black --check`, `cargo fmt --check`)

Use the correct tool paths (e.g., `/Users/kulesh/.cargo/bin/cargo` if cargo is there).

**IMPORTANT: Report validation results using this format:**

```xml
<validation>
<command>the exact command you ran</command>
<exit-code>0 or non-zero</exit-code>
<output>
The relevant output (test results, errors, etc.)
</output>
<analysis>1-2 sentences explaining why this output shows success/failure</analysis>
<status>pass|fail</status>
</validation>
```

Output a `<validation>` block for each validation command you run.
This allows the system to capture evidence of your validation work.

## Acceptance Criteria Verification

When you verify each acceptance criterion, report using this format:

```xml
<acceptance-criterion>
<index>N</index>
<status>verified</status>
<text>The criterion text (copy from list above)</text>
<evidence>
Verify each criterion in TWO steps:

STEP 1 - Static Analysis:
- Identify the code that implements this feature
- Reference specific files, functions, line numbers
- Note any relevant tests

STEP 2 - Runtime Verification:
- Run actual commands that exercise the feature
- Show the command and its output
- Explain why the output proves the criterion is met

Example evidence:
STEP 1 - Static Analysis:
src/commands/amend.rs implements --parent flag (lines 193-196)
Test test_reparent_task validates the behavior

STEP 2 - Runtime Verification:
$ tracker amend 5 --parent 2
Item #5 updated: parent changed to #2

$ tracker details 5
Parent: #2 (Feature: User Auth)

Conclusion: The command successfully reparented the task as expected.
</evidence>
</acceptance-criterion>
```

Use `<status>verified</status>` if the criterion passes,
`<status>failed</status>` if it fails.
Output an `<acceptance-criterion>` block for each acceptance criterion.
This allows the system to capture your verification work.

If any validation fails:
1. Analyze the error output
2. Fix the underlying issue
3. Re-run the validation
4. Only mark complete when all validations pass

**COMPLETION SIGNAL:**
When ALL acceptance criteria are met and validation checks pass, output:
<waypoint-complete>{waypoint.id}</waypoint-complete>

Only output the completion marker when you are confident the waypoint is done.
If you cannot complete the waypoint after several attempts, explain what's blocking you.

Begin implementing this waypoint now.
"""


def build_verification_prompt(receipt: "ChecklistReceipt") -> str:
    """Build the LLM verification prompt for a receipt.

    Args:
        receipt: The checklist receipt to verify

    Returns:
        Verification prompt string
    """
    # Build context section
    context_section = ""
    if receipt.context:
        criteria_list = "\n".join(
            f"  - {c}" for c in receipt.context.acceptance_criteria
        )
        context_section = f"""### Waypoint Context
- **Title**: {receipt.context.title}
- **Objective**: {receipt.context.objective}
- **Acceptance Criteria**:
{criteria_list}

"""

    # Build evidence section
    evidence_sections = []
    for item in receipt.checklist:
        status_emoji = "✅" if item.status == "passed" else "❌"
        output = item.stdout or item.stderr or "(no output)"
        # Truncate long outputs
        if len(output) > 500:
            output = output[:500] + "\n... (truncated)"
        evidence_sections.append(
            f"""**{item.item}** {status_emoji}
- Command: `{item.command}`
- Exit code: {item.exit_code}
- Output:
```
{output}
```"""
        )

    evidence_text = "\n\n".join(evidence_sections)
    soft_evidence_text = ""
    if receipt.soft_checklist:
        soft_sections = []
        for item in receipt.soft_checklist:
            status_emoji = "✅" if item.status == "passed" else "❌"
            output = item.stdout or item.stderr or "(no output)"
            if len(output) > 500:
                output = output[:500] + "\n... (truncated)"
            soft_sections.append(
                f"""**{item.item}** {status_emoji}
- Command: `{item.command}`
- Exit code: {item.exit_code}
- Output:
```
{output}
```"""
            )
        soft_evidence_text = "\n\n".join(soft_sections)
    soft_section = ""
    if soft_evidence_text:
        soft_section = f"\n### Soft Validation Evidence\n\n{soft_evidence_text}\n"

    return f"""## Receipt Verification

A receipt was generated for waypoint {receipt.waypoint_id}. Please verify it.

{context_section}### Captured Evidence

{evidence_text}
{soft_section}

### Verification Task

Review the captured evidence and answer:
1. Did all checklist commands succeed (exit code 0)?
2. Does the output indicate genuine success (not empty, no hidden errors)?
3. If host and soft evidence conflict, treat host evidence as authoritative and
   mark the receipt invalid.
4. Based on the evidence, is this waypoint complete?

Output your verdict:
<receipt-verdict status="valid|invalid">
Brief reasoning here
</receipt-verdict>
"""
