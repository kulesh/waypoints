# Waypoints Protocol v1 (Draft)

This document defines a text-first protocol for the Waypoints engine. Clients
send JSON commands to stdin and receive JSONL events from stdout.

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

## Command Types (v1)

- start_qa
- continue_qa
- generate_brief
- generate_spec
- generate_plan
- add_waypoint
- update_waypoint
- delete_waypoint
- reorder_waypoints
- execute_waypoint
- pause
- resume
- intervene
- export_genspec
- status

## Event Types (v1)

- state_changed
- dialogue_chunk
- dialogue_completed
- artifact_saved
- flight_plan_updated
- waypoint_status_changed
- execution_log
- metrics_updated
- warning
- error

## Payload Schemas (Selected)

### state_changed

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

### flight_plan_updated

```json
{
  "change": "generated",
  "waypoints": [
    {"id": "WP-001", "title": "Setup", "status": "pending"}
  ]
}
```

### execution_log

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
  "total_cost_usd": 0.42,
  "total_tokens": 12500,
  "phase": "fly",
  "waypoint_id": "WP-003"
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
{"schema_version":"1.0","command_id":"cmd-0002","command_type":"generate_plan","project_slug":"demo","timestamp":"2026-01-20T12:40:00Z","payload":{"spec_path":"docs/product-spec.md"}}
```

Events:

```json
{"schema_version":"1.0","event_id":"evt-0002","event_type":"state_changed","command_id":"cmd-0002","project_slug":"demo","timestamp":"2026-01-20T12:40:00Z","payload":{"from":"shape:spec:review","to":"chart:generating"}}
{"schema_version":"1.0","event_id":"evt-0003","event_type":"flight_plan_updated","command_id":"cmd-0002","project_slug":"demo","timestamp":"2026-01-20T12:40:03Z","payload":{"change":"generated","waypoints":[{"id":"WP-001","title":"Setup","status":"pending"}]}}
{"schema_version":"1.0","event_id":"evt-0004","event_type":"state_changed","command_id":"cmd-0002","project_slug":"demo","timestamp":"2026-01-20T12:40:03Z","payload":{"from":"chart:generating","to":"chart:review"}}
```
