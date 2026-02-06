# ADR 0003: Execution Report Model

Date: 2026-02-05
Status: Accepted

## Context

Execution outcomes were logged but lacked a structured report for summarizing
waypoint attempts. This made it hard to aggregate metrics or build future
observability features on top of execution artifacts.

## Decision

Introduce `ExecutionReport` as a structured summary of a waypoint execution
attempt, capturing result, timestamps, and completion data.

## Consequences

- Establishes a durable schema for execution summaries.
- Enables future aggregation and reporting without parsing logs.
- Keeps the report model independent of UI layers.
