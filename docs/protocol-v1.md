# Waypoints Protocol v1 (Draft)

This document defines a text-first protocol for the Waypoints engine. Clients
send JSON commands to stdin and receive JSONL events from stdout.

Terminology in this draft follows:
- `docs/domain-model-ubiquitous-language.md`

## Design Goals

- Text-first, streamable, and append-only
- Replayable event log
- Forward-compatible via schema versions
- Human-inspectable with standard CLI tools

## Transport

- Commands: JSON objects (one per line) sent to engine stdin
- Events: JSONL (one event per line) emitted on stdout

## Command Envelope

All commands follow this envelope:

```json
{
  "schema_version": "1.0",
  "command_id": "cmd-0001",
  "command_type": "generate_spec",
  "project_slug": "my-project",
  "timestamp": "2026-01-20T12:34:56.000000Z",
  "payload": {}
}
```

Required fields:
- schema_version
- command_id
- command_type
- project_slug
- timestamp
- payload

## Event Envelope

All events follow this envelope:

```json
{
  "schema_version": "1.0",
  "event_id": "evt-0001",
  "event_type": "state_changed",
  "command_id": "cmd-0001",
  "project_slug": "my-project",
  "timestamp": "2026-01-20T12:34:56.123456Z",
  "payload": {}
}
```

Required fields:
- schema_version
- event_id
- event_type
- command_id (may be null for system events)
- project_slug
- timestamp
- payload

## Error Envelope

Errors are emitted as events with type `error`:

```json
{
  "schema_version": "1.0",
  "event_id": "evt-0099",
  "event_type": "error",
  "command_id": "cmd-0001",
  "project_slug": "my-project",
  "timestamp": "2026-01-20T12:35:00.000000Z",
  "payload": {
    "code": "validation_failed",
    "message": "Invalid waypoint JSON",
    "severity": "error",
    "retryable": false,
    "details": {}
  }
}
```

## Command Types (v1, canonical)

- `shape_start_qa`
- `shape_continue_qa`
- `shape_generate_brief`
- `shape_generate_spec`
- `chart_generate_flight_plan`
- `chart_add_waypoint`
- `chart_update_waypoint`
- `chart_delete_waypoint`
- `chart_reorder_waypoints`
- `fly_execute_waypoint`
- `fly_pause`
- `fly_resume`
- `fly_resolve_intervention`
- `genspec_export`
- `project_status`

## Legacy Command Aliases (compatibility)

- `start_qa` -> `shape_start_qa`
- `continue_qa` -> `shape_continue_qa`
- `generate_brief` -> `shape_generate_brief`
- `generate_spec` -> `shape_generate_spec`
- `generate_plan` -> `chart_generate_flight_plan`
- `add_waypoint` -> `chart_add_waypoint`
- `update_waypoint` -> `chart_update_waypoint`
- `delete_waypoint` -> `chart_delete_waypoint`
- `reorder_waypoints` -> `chart_reorder_waypoints`
- `execute_waypoint` -> `fly_execute_waypoint`
- `pause` -> `fly_pause`
- `resume` -> `fly_resume`
- `intervene` -> `fly_resolve_intervention`
- `export_genspec` -> `genspec_export`
- `status` -> `project_status`

## Event Types (v1, canonical)

- `journey_state_changed`
- `dialogue_chunk`
- `dialogue_completed`
- `artifact_saved`
- `flight_plan_changed`
- `waypoint_status_changed`
- `execution_log_entry`
- `metrics_updated`
- `warning`
- `error`

## Legacy Event Aliases (compatibility)

- `state_changed` -> `journey_state_changed`
- `flight_plan_updated` -> `flight_plan_changed`
- `execution_log` -> `execution_log_entry`

## Payload Schemas (Selected)

### journey_state_changed

```json
{
  "from": "shape:spec:review",
  "to": "chart:generating",
  "reason": "chart.generate"
}
```

### dialogue_chunk

```json
{
  "message_id": "msg-0007",
  "role": "assistant",
  "content": "What user problem are we solving...",
  "final": false
}
```

### artifact_saved

```json
{
  "artifact_type": "product_spec",
  "file_path": "docs/product-spec-20260120-123456.md"
}
```

### flight_plan_changed

```json
{
  "change": "generated",
  "waypoints": [
    {"id": "WP-001", "title": "Setup", "status": "pending"}
  ]
}
```

### execution_log_entry

```json
{
  "waypoint_id": "WP-003",
  "entry_type": "iteration_start",
  "iteration": 1,
  "content": "Starting iteration 1"
}
```

### metrics_updated

```json
{
  "role": "builder",
  "waypoint_id": "WP-003",
  "delta_cost_usd": 0.08,
  "delta_tokens_in": 1200,
  "delta_tokens_out": 340,
  "delta_cached_tokens_in": 950,
  "waypoint_cost_usd": 0.42,
  "waypoint_tokens_in": 6100,
  "waypoint_tokens_out": 2200,
  "project_cost_usd": 1.36,
  "project_tokens_in": 18200,
  "project_tokens_out": 7100,
  "project_cached_tokens_in": 8400,
  "tokens_known": true,
  "cached_tokens_known": true,
  "phase": "fly",
  "message": "builder:metrics_updated"
}
```

## Event Ordering and Idempotency

- Events are append-only and ordered by emission time.
- Commands should be idempotent when retried with the same command_id.
- Clients should tolerate duplicate events and out-of-order arrival.

## Versioning Rules

- schema_version follows semver.
- New optional fields are backward compatible.
- Removing/renaming fields or changing semantics requires a major bump.

## Example Interaction

Command:

```json
{"schema_version":"1.0","command_id":"cmd-0002","command_type":"chart_generate_flight_plan","project_slug":"demo","timestamp":"2026-01-20T12:40:00Z","payload":{"spec_path":"docs/product-spec.md"}}
```

Events:

```json
{"schema_version":"1.0","event_id":"evt-0002","event_type":"journey_state_changed","command_id":"cmd-0002","project_slug":"demo","timestamp":"2026-01-20T12:40:00Z","payload":{"from":"shape:spec:review","to":"chart:generating"}}
{"schema_version":"1.0","event_id":"evt-0003","event_type":"flight_plan_changed","command_id":"cmd-0002","project_slug":"demo","timestamp":"2026-01-20T12:40:03Z","payload":{"change":"generated","waypoints":[{"id":"WP-001","title":"Setup","status":"pending"}]}}
{"schema_version":"1.0","event_id":"evt-0004","event_type":"journey_state_changed","command_id":"cmd-0002","project_slug":"demo","timestamp":"2026-01-20T12:40:03Z","payload":{"from":"chart:generating","to":"chart:review"}}
```
