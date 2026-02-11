# typescript-node v1

## Applicability

Use when Node/TypeScript markers exist (`package.json` or `tsconfig.json`).

## Preferred Commands

1. `npm run lint`
2. `npm run typecheck`
3. `npm test`

## Test Strategy

1. cover affected units and integration boundaries
2. keep fixtures deterministic and lightweight
3. verify build output before completion claim

## Anti-Patterns

1. bypassing type errors with `any` for convenience
2. editing generated lock/build artifacts without intent
3. changing runtime behavior without test updates
