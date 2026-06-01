# Mapping the codebase (cartography)

Every Woof consumer repo carries a cartography artefact group at `.woof/codebase/` so dispatched
nodes get prompt-ready repo context cheaply (ADR-004). This is the former `/woof:map-codebase` flow,
now a reference under the `/woof` umbrella. It runs in three layers.

## Design layer (human-authored, durable)

Authored during setup, refreshed only when architectural strategy changes:

- `TARGET-ARCHITECTURE.md` - the shape the project is designed to be (greenfield: written before
  code; brownfield: pragmatic acceptance, aspirational refactor, or mixed).
- `PRINCIPLES.md` - cross-cutting design principles.

Help the operator author or update these by hand; they are not regenerated.

## AS-IS layer (mapper-authored, refreshed on demand)

Seven themed documents describing the repo as it is. Regenerate them by dispatching parallel mapper
subagents (one per theme) via the `Task` tool; each explores the codebase and writes its document
directly to `.woof/codebase/`:

- `CURRENT-ARCHITECTURE.md` - observed patterns, layers, data flow, abstractions, entry points.
- `STACK.md` - languages, frameworks, runtime, dependencies.
- `INTEGRATIONS.md` - external APIs, databases, auth, monitoring, CI/CD.
- `STRUCTURE.md` - directory layout, where to add new code, naming.
- `CONVENTIONS.md` - naming, formatting, imports, comments, function design.
- `TESTING.md` - framework, file organisation, mocking, fixtures, coverage.
- `CONCERNS.md` - tech debt, known bugs, security risks, fragile areas.

Run the mappers in parallel for a full refresh, then update the freshness stamp.

## Mechanical layer (post-commit refreshed)

Regenerated automatically by the Woof-managed post-commit hook, which runs the consumer-owned
`scripts/refresh-cartography`:

- `tags` - a ctags index.
- `tree.txt` - the file tree.
- `freshness.json` - the freshness stamp.

Install the hook once during setup:

```bash
woof hooks install
```

A manual mechanical refresh is just running `./scripts/refresh-cartography` (or making a commit).

See ADR-004 (`docs/adr/004-cartography-prerequisite.md`) for the full rationale.
