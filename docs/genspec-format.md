# Generative Spec (genspec.jsonl) Format

This document describes the `genspec.jsonl` format used to export and import
Waypoints projects. The format is JSON Lines (JSONL): one JSON object per line.
The first line is always a header. Subsequent lines are entries.

Location: exported to `{project_slug}.genspec.jsonl` via `waypoints export`.

## 1) JSONL Structure

### 1.1 Header (line 1)

The header identifies the schema and version and captures top-level metadata.

```json
{
  "_schema": "genspec",
  "_version": "1.0",
  "waypoints_version": "0.1.0",
  "source_project": "my-project",
  "created_at": "2026-01-19T23:52:10.123456",
  "model": "claude-sonnet-4",
  "model_version": "2024-12-01",
  "initial_idea": "An IDE for generative software"
}
```

Required fields:
- `_schema`: must be `"genspec"`.
- `_version`: format version (current: `"1.0"`).
- `waypoints_version`: version of Waypoints at export time.
- `source_project`: project slug at export time.
- `created_at`: ISO 8601 timestamp.

Optional fields:
- `model`, `model_version`, `initial_idea`.

### 1.2 Entry types (lines 2+)

Each following line is a JSON object with a `type` field:
- `"step"`: a generative step (LLM call or user input step)
- `"decision"`: a user decision about generated content
- `"artifact"`: a generated artifact (idea brief, product spec, flight plan)

Example:

```json
{"type":"step", ...}
{"type":"decision", ...}
{"type":"artifact", ...}
```

## 2) Artifact Types

Artifacts capture the final outputs of each major phase.

### 2.1 IDEA_BRIEF
- `artifact_type`: `"idea_brief"`
- `content`: Markdown
- `file_path`: relative path within project (optional)

### 2.2 PRODUCT_SPEC
- `artifact_type`: `"product_spec"`
- `content`: Markdown
- `file_path`: relative path within project (optional)

### 2.3 FLIGHT_PLAN
- `artifact_type`: `"flight_plan"`
- `content`: JSON array of waypoints (serialized as a string)
- `file_path`: usually `"flight-plan.jsonl"`

Waypoint JSON fields in `content`:
- `id`: string, e.g. `"WP-001"` or `"WP-001a"`
- `title`: string
- `objective`: string
- `acceptance_criteria`: array of strings
- `parent_id`: string or null
- `dependencies`: array of waypoint IDs
- `status`: string (`pending|in_progress|complete|failed|skipped`)

Example artifact entry:

```json
{
  "type": "artifact",
  "artifact_type": "flight_plan",
  "content": "[{\"id\":\"WP-001\",\"title\":\"Setup\",...}]",
  "file_path": "flight-plan.jsonl",
  "timestamp": "2026-01-19T23:52:10.123456"
}
```

## 3) Step Structure

Steps capture the input, output, and metadata for a single generative event.

### 3.1 StepInput
- `system_prompt`: string (optional)
- `user_prompt`: string (optional)
- `messages`: list of `{role, content}` (optional)
- `context`: object (optional)

### 3.2 StepOutput
- `content`: string
- `type`: `"text" | "json" | "markdown"`
- `parsed`: any (optional)

### 3.3 StepMetadata
- `tokens_in`: number (optional)
- `tokens_out`: number (optional)
- `cost_usd`: number (optional)
- `latency_ms`: number (optional)
- `model`: string (optional)

### 3.4 Step Entry Example

```json
{
  "type": "step",
  "step_id": "step-003",
  "phase": "shape_spec",
  "timestamp": "2026-01-19T23:52:10.123456",
  "input": {
    "system_prompt": "You are a senior product manager...",
    "user_prompt": "Generate the product spec",
    "messages": [
      {"role":"user","content":"..."},
      {"role":"assistant","content":"..."}
    ]
  },
  "output": {
    "content": "# Product Specification...",
    "type": "markdown"
  },
  "metadata": {
    "tokens_in": 1250,
    "tokens_out": 2230,
    "cost_usd": 0.21,
    "latency_ms": 4200,
    "model": "claude-sonnet-4"
  }
}
```

## 4) Phase Enum

`phase` values are defined by `genspec.spec.Phase`:
- `spark`: initial idea capture (user input only)
- `shape_qa`: Q&A dialogue (clarifying questions)
- `shape_brief`: idea brief generation
- `shape_spec`: product spec generation
- `chart`: waypoint generation
- `chart_breakdown`: AI-assisted breakdown of a waypoint
- `chart_add`: AI-assisted add waypoint
- `fly`: waypoint execution

## 5) Version Compatibility

### Current Version
- Format version: `1.0`
- Header `_version` is used for compatibility checks.

### Breaking Changes
Breaking changes require a new `_version` and a migration path for:
- renamed fields
- removed required fields
- semantic changes to enums

### Non-Breaking Changes
Non-breaking changes may add optional fields or new entry types that consumers
can ignore.

### Migration Guidance
- Consumers should ignore unknown fields.
- If `_schema` is not `"genspec"`, the file is invalid.
- If `_version` is greater than supported, consumers should refuse or warn.

## Appendix: Decisions

User decisions are recorded when a user accepts, rejects, or edits a step.

```json
{
  "type": "decision",
  "step_id": "step-004",
  "phase": "shape_spec",
  "decision": "accept",
  "timestamp": "2026-01-19T23:52:10.123456",
  "edits": {
    "product_spec": "User-edited text..."
  }
}
```

Fields:
- `decision`: `accept|reject|edit`
- `edits`: optional mapping of edited content
