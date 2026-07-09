---
type: adr
status: accepted
date: 2026-07-09
---

# ADR-017: Engine Config and State Live in the Operator Home, Not the Driven Repo

## Context

Woof's original layout stored repo policy (`.woof/policy.toml`, `agents.toml`, `quality-gates.toml`, `prerequisites.toml`) and durable run state (`.woof/epics/E<N>/...`, review cache, instability records) inside the consumer repo. VaultForeman started with the same shape (repo-root `VAULT_FOREMAN.md`, in-repo `.vf-runs/`) and abandoned it: run state moved to the operator home (vault-foreman `ac9ac02`), then operator-local project config became the only source and the repo fallback was deleted (vault-foreman `ff381dd`). The driving principle from that change: a driven repository must carry no trace of the orchestrator that builds it. Client repos in particular must never accumulate an SDLC tool's config, planning, or tracking files.

## Decision

All Woof engine config and durable engine state live under the operator home, keyed by project. Nothing engine-owned is written into the driven repo.

- Engine home is `~/.woof` (overridable via `WOOF_HOME`).
- Per-project config: `~/.woof/config/projects/<project-key>.toml`. This is the single, engine-neutral consumer delivery declaration (profile, run-profile slots, gate, check floor, cartography floor, drain semantics) plus the scopes previously split across `.woof/agents.toml`, `quality-gates.toml`, and `prerequisites.toml`. A missing project config is a hard preflight error; there is no in-repo fallback, deprecated or otherwise.
- Per-project state: `~/.woof/state/projects/<project-key>/` — runs, epics, review cache, instability records, locks, usage.
- The project key is always explicit at every entry point. It is never derived from a checkout directory name (directory-name derivation collides across worktree containers that share names like `main`).
- Disk authority is unchanged in kind: files are the source of truth over live sessions. Only the home of those files moves.
- Work-source documents (an epic, a `work_units[]` backlog) are inputs, not engine state. They live where the project's PM convention puts them — a self-owned repo's `docs/`, or the operator's PM area for client work — and are read by the engine, which writes engine state only under `~/.woof`.

## Consequences

- The `.woof/` directory in consumer repos is retired entirely; a repo onboards by the operator creating `~/.woof/config/projects/<key>.toml`, not by committing files to the repo.
- This amends `engine-neutral-consumer-policy`'s location (the declare-once principle survives; the declaration's home moves) and amends the architecture's disk-authority wording and topology State row.
- Aligns Woof with VaultForeman's final config model, so the VF-to-Woof cutover migrates per-project TOML between two operator-home trees rather than rewriting consumer repos.
- Backlog unit-state writeback to a work-source document is the one deliberate exception where the engine writes outside `~/.woof`, and it is engine-exclusive: producers are forbidden from mutating unit state, and it targets the PM document, never engine files in the delivery repo.
