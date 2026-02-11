# Skill Packs

Skill packs define reusable, stack-specific execution guidance that the
orchestrator can attach to builder/verifier guidance packets.

## Versioning

Each skill pack file is versioned in its filename:

- `<skill-id>.v<version>.md`

Attached skill IDs in runtime artifacts use the same version marker:

- `python-pytest-ruff@1`
- `typescript-node@1`
- `rust-cargo@1`

## Resolver Rules (Current)

Resolver checks repository root marker files:

1. `python-pytest-ruff@1`
   - markers: `pyproject.toml`, `requirements.txt`, `requirements-dev.txt`
2. `typescript-node@1`
   - markers: `package.json`, `tsconfig.json`
3. `rust-cargo@1`
   - markers: `Cargo.toml`

Resolver output is deterministic and ordered by pack declaration.
