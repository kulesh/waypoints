# rust-cargo v1

## Applicability

Use when Rust project marker exists (`Cargo.toml`).

## Preferred Commands

1. `cargo fmt --check`
2. `cargo clippy --all-targets --all-features -- -D warnings`
3. `cargo test`

## Test Strategy

1. unit tests for core logic
2. integration tests for crate boundaries
3. include regression tests for bug fixes

## Anti-Patterns

1. suppressing clippy warnings without rationale
2. mixing unrelated refactors with bug fixes
3. accepting flaky tests
