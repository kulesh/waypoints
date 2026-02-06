# Fly Token Usage Analysis (2026-02-06)

## Purpose

Analyze the Waypoints FLY phase token economics from multiple perspectives:

1. Exact runtime flow and prompt stacking behavior from TUI start to receipt verification.
2. Measured evidence from real session logs and metrics.
3. A per-waypoint budget ledger that separates static and dynamic token drivers.

This document captures findings through commit state on 2026-02-06.

## Data Sources

Code paths:

- `src/waypoints/tui/screens/fly.py`
- `src/waypoints/fly/executor.py`
- `src/waypoints/llm/prompts/fly.py`
- `src/waypoints/fly/receipt_finalizer.py`
- `src/waypoints/llm/client.py`
- `src/waypoints/llm/providers/openai.py`
- `src/waypoints/llm/providers/anthropic.py`

Runtime artifacts (actuator project):

- `/Users/kulesh/dev/flight-test/projects/actuator/sessions/fly/*.jsonl`
- `/Users/kulesh/dev/flight-test/projects/actuator/metrics.jsonl`
- `/Users/kulesh/dev/flight-test/projects/actuator/receipts/*.json`

## End-to-End Fly Call Graph

1. User starts FLY execution:
   - `FlyScreen._execute_current_waypoint` in `src/waypoints/tui/screens/fly.py`.
2. Worker runs `WaypointExecutor.execute()`:
   - `FlyScreen._run_executor` -> `WaypointExecutor.execute` -> `_execute_impl`.
3. Prompt is built once per waypoint run:
   - `build_execution_prompt(...)` in `src/waypoints/llm/prompts/fly.py`.
4. Per executor iteration:
   - `agent_query(...)` call with tools in `src/waypoints/fly/executor.py`.
5. If completion marker is detected:
   - `ReceiptFinalizer.finalize(...)` runs host validation commands and saves receipt.
6. If host evidence is valid enough to proceed:
   - second LLM call: `_verify_with_llm(...)` using `build_verification_prompt(...)`.

## Prompt Stacking Model

### Layer A: Executor outer loop

- Iteration 1 prompt is the full execution prompt (`~8.3k chars` in measured data).
- Iteration 2+ prompt is currently `"Continue implementing."` (very small).
- Outer loop does not explicitly replay full prior assistant text.

### Layer B: Provider internal tool loop (dominant stack)

OpenAI path (`src/waypoints/llm/providers/openai.py`):

1. Initialize `messages = [system?, user(prompt)]`.
2. On each model turn:
   - send full `messages`.
   - receive assistant text and/or tool calls.
3. For each tool call:
   - append assistant message (`message.model_dump()`).
   - execute tool.
   - append tool result message (`role=tool`).
4. Next turn re-sends the growing `messages` list.

Result: model output and tool outputs stack into subsequent input tokens.

Anthropic Claude SDK path (`src/waypoints/llm/providers/anthropic.py`):

- Session context stacking is handled inside Claude CLI/SDK query stream.
- Application layer does not append message history explicitly, but context still grows in-session with tool usage and generated text.

### Layer C: Finalize verification call

- Independent second call with `allowed_tools=[]`.
- Input prompt is receipt-centric (`~3.8k-4.3k chars` in measured data), plus short verification system prompt.

## Pseudocode: Token Send/Receive Lifecycle

```python
def run_waypoint_fly():
    exec_prompt = build_execution_prompt(waypoint, spec, checklist)  # static payload
    system_prompt = executor_system_prompt(project_path)

    for outer_iter in 1..MAX_ITER:
        prompt = exec_prompt if outer_iter == 1 else "Continue implementing."

        stream = agent_query(
            prompt=prompt,
            system_prompt=system_prompt,
            tools=[Read, Write, Edit, Bash, Glob, Grep, WebSearch, WebFetch],
            cwd=project_path,
            phase="fly",
            waypoint_id=waypoint.id,
        )

        full_output = ""
        for ev in stream:
            if ev is StreamChunk:
                full_output += ev.text  # output tokens accrue

            if ev is StreamToolUse:
                tool_output = execute_tool(ev.tool_name, ev.tool_input)

                # OpenAI: assistant/tool messages are appended to history.
                # Next internal turn sends that larger history back to the model.
                # Anthropic CLI path: equivalent growth managed internally.

            if ev is StreamComplete:
                record_tokens(ev.tokens_in, ev.tokens_out, ev.cached_tokens_in)

        if completion_marker in full_output:
            break

    host_evidence = run_validation_commands()
    receipt = build_and_save_receipt(host_evidence, criteria)

    if receipt_precheck_passes:
        verify_prompt = build_verification_prompt(receipt)
        verify_out = agent_query(
            prompt=verify_prompt,
            system_prompt="Verify the checklist receipt. Output your verdict.",
            tools=[],
            phase="fly",
            waypoint_id=waypoint.id,
        )
        parse_verdict(verify_out)
```

## Measured Runtime Facts (Actuator Sessions)

### Prompt lengths

- Main execution prompt (`iteration_start.prompt`):
  - count: 16
  - avg: 8349.9 chars
  - min: 8287
  - max: 8483
- Verification prompt (from saved receipts):
  - range: 3815 to 4258 chars
  - avg (11 receipts): 3950.7 chars

### Tool and output activity (non-zero-cost runs)

- avg tool calls: 54.4
- min tool calls: 30
- max tool calls: 99
- avg model output chars: 13840.7
- max model output chars: 19181

### Correlation with cost (empirical, this dataset)

- `tool_calls` vs cost: `r = 0.947`
- `output_chars` vs cost: `r = 0.918`
- `read_calls` vs cost: `r = 0.927`
- `edit_calls` vs cost: `r = 0.913`
- `prompt_chars` vs cost: weak and negative in this sample (`r = -0.438`)

Interpretation: dynamic in-session activity dominates cost variance; static prompt length is nearly constant and is not the main differentiator.

### Fitted cost models (non-zero-cost runs)

- Model 1:
  - `cost ≈ -1.1243 + 0.0897 * tool_calls`
  - `R² = 0.897`
- Model 2:
  - `cost ≈ -2.5449 + 0.0715 * tool_calls + 0.1740 * output_kchars`
  - `R² = 0.901`

These are descriptive fits for this run, not universal pricing laws.

## Per-Waypoint Budget Ledger

Heuristic token estimates use `~4 chars/token` for text-derived fields where exact tokens were unavailable in historical logs.

Columns:

- `est_static_tok`: execution prompt chars / 4
- `est_tool_tok`: (serialized tool_input chars + tool_output chars) / 4
- `est_out_tok`: model output chars / 4

| Waypoint | Cost USD | Prompt chars | Verify prompt chars | Tool calls | Read | Bash | Glob | Edit | Output chars | Tool output chars | est_static_tok | est_tool_tok | est_out_tok |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| WP-011 | 7.7705 | 8364 | 3884 | 99 | 46 | 22 | 4 | 18 | 19181 | 1522085 | 2091.0 | 395442.8 | 4795.2 |
| WP-005 | 5.7959 | 8411 | 4062 | 70 | 25 | 26 | 2 | 10 | 15155 | 396406 | 2102.8 | 133372.5 | 3788.8 |
| WP-006 | 5.3613 | 8355 | 3881 | 71 | 34 | 14 | 2 | 14 | 15724 | 412544 | 2088.8 | 122486.2 | 3931.0 |
| WP-009 | 4.7892 | 8295 | 3815 | 60 | 23 | 15 | 4 | 10 | 13130 | 556397 | 2073.8 | 156844.2 | 3282.5 |
| WP-003 | 4.4673 | 8388 | 3914 | 74 | 33 | 11 | 6 | 13 | 15724 | 411101 | 2097.0 | 126805.0 | 3931.0 |
| WP-007 | 3.8531 | 8304 | 3824 | 50 | 23 | 14 | 0 | 12 | 14009 | 462029 | 2076.0 | 126867.8 | 3502.2 |
| WP-008 | 2.7098 | 8358 | 3924 | 34 | 15 | 9 | 4 | 4 | 11361 | 318194 | 2089.5 | 95075.2 | 2840.2 |
| WP-004 | 2.1857 | 8421 | 3991 | 37 | 13 | 18 | 1 | 4 | 13084 | 266871 | 2105.2 | 78044.0 | 3271.0 |
| WP-010 | 1.7122 | 8363 | 3883 | 30 | 7 | 12 | 4 | 1 | 12054 | 218819 | 2090.8 | 63691.5 | 3013.5 |
| WP-002 | 1.4869 | 8422 | 4022 | 33 | 13 | 11 | 3 | 5 | 11759 | 169109 | 2105.5 | 50968.5 | 2939.8 |
| WP-001 | 1.1417 | 8483 | 4258 | 40 | 12 | 23 | 0 | 3 | 11067 | 92796 | 2120.8 | 26309.5 | 2766.8 |

Notes:

- `tool_output_chars` includes potentially very large stdout/file payloads; these are high-leverage context inflators.
- Historical `metrics.jsonl` rows for this run had cost data but no token-in/out values. This limited direct token attribution and required text-size proxies.

## Why spend feels high relative to delivered value

1. The fixed execution prompt is large but stable. It sets a high baseline per run.
2. Most variation comes from tool-loop growth:
   - repeated file reads,
   - high-volume shell output,
   - broad glob/search outputs,
   - assistant explanations that also become context.
3. Verification adds another separate model call with a non-trivial prompt.

## Findings Since Prompt Caching Work

Implemented in current working tree:

- Always-on OpenAI prompt caching with stable project/phase/model cache key.
- `cached_tokens_in` tracking in metrics and TUI.
- Anthropic cached token extraction when usage payload includes cache-read counters.

Impact expectation:

- Caching should reduce repeated-prefix input cost in repeated calls.
- It does not remove dynamic context inflation from large tool outputs.

## Findings Since Provenance Tracking Work

Implemented in current working tree:

- Added workspace before/after snapshot capture per waypoint.
- Added execution-log `workspace_diff` entries with:
  - file-level provenance (`added/modified/deleted`),
  - text-vs-binary change counts,
  - top changed files,
  - rough token proxy from text deltas (`approx_tokens_changed`).
- Added TUI rendering for `workspace_diff` so historical logs show:
  - changed file counts,
  - rough token estimate from text diffs,
  - top changed paths.

Interpretation:

- This is a provenance metric first, token estimator second.
- The estimator is directional and useful for relative comparison between waypoints.
- Billing-accurate token accounting still requires provider-level usage fields.

## Gaps and Measurement Limitations

1. Historical fly metrics for this run lacked `tokens_in/tokens_out` fields.
2. Character-based token estimates are useful for relative comparisons, not billing-accurate totals.
3. Anthropic CLI path abstracts some internal turn-level accounting.

## Recommended Next Instrumentation

1. Add per-turn token deltas (not just per-call totals) for agent tool-loop turns.
2. Track tool-output byte/token contributions per tool name and command.
3. Break out verification-call token totals separately from build-call totals.
4. Add dashboard-style summaries:
   - static prompt tokens,
   - dynamic tool-context tokens,
   - model output tokens,
   - cached tokens (provider-reported).

## Bottom Line

In this dataset, prompt length is not the main reason for high spend variance. Dynamic tool-loop context growth is the dominant driver.
