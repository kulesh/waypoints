# ADR 0002: Flight Test Harness

Date: 2026-02-05
Status: Accepted

## Context

The testing strategy defined flight tests (L0–L5) but lacked operational tooling.
To improve iteration discipline, we needed a repeatable harness that records
results and validates generated projects against minimal expectations.

## Decision

Add `scripts/run_flight_test.py` to execute a flight test against an existing
project directory. The runner:
- Creates timestamped results directories
- Validates minimum expected files
- Runs optional smoke tests
- Writes a `meta.json` summary

Seed L0–L2 fixtures under `flight-tests/` to make the harness immediately usable.

## Consequences

- Provides a repeatable baseline for flight test validation.
- Creates an audit trail for regressions and improvements.
- Keeps generation concerns decoupled from validation so the harness is usable
  before full automation is in place.
