---
type: adr
status: accepted
date: 2026-06-28
---

# ADR-012: Dispatch Runs Through the Interactive tmux Harness

## Context

Headless CLI modes such as `claude -p` and `codex exec` are not allowed in the vault build path. VaultForeman has already hardened the interactive tmux harness path: prompt-file delivery, readiness, done-marker handling, structured verdict capture, multi-line blocker preservation, and warm producer/fresh reviewer fix rounds.

## Decision

Woof dispatch uses the shared interactive tmux harness. The engine consumes structured dispatch results and never parses raw terminal scrollback.

The producer stays warm across bounded fix rounds. The reviewer is fresh each round and independent of the producer.

## Consequences

- Woof's headless dispatcher is removed.
- Harness adapters own TUI-specific behaviour and presentation-chrome stripping.
- Dispatch result shape includes verdict, evidence, usage, session identity, artefact references, and completion classification.
- Live sessions are execution resources only; disk remains state authority.
