---
type: adr
status: accepted
date: 2026-07-11
---

# ADR-012: Dispatch uses interactive harness transports

## Context

Woof must run subscription-billed coding agents as interactive TUIs, keep producer context across fix rounds, isolate each reviewer round, recover from process loss, and classify completion or blocked input without making terminal presentation the workflow authority.

Tmux supplies a portable retained terminal and supports TUIs without lifecycle integrations. Herdr supplies semantic lifecycle events for integrated agents through a named-session socket. Harness and model choice remain project policy; transport mechanics belong behind the dispatch registry.

## Decision

Every Woof worker runs as an interactive TUI through the backend declared by its harness profile. Profiles declare tmux or herder explicitly. Project policy selects a harness, model, and effort; it never selects or branches on a transport.

The dispatch substrate exposes one backend-neutral one-shot and retained-session contract. It owns launch, receipt-confirmed prompt-file delivery, lifecycle observation, payload capture, evidence, compatibility metadata, and close. The engine consumes structured results and does not parse raw terminal scrollback as workflow state.

The producer remains the same retained worker across bounded fix rounds. Each reviewer round uses a fresh independent worker and the complete current diff. Resume records enough backend-neutral session identity to reattach when safe or respawn from disk authority.

Herder-backed turns arm lifecycle observation before prompt submission. `working -> idle` or `done` completes only when the payload exists. `blocked` and timeout are distinct typed outcomes with evidence. Tmux remains supported for profiles whose TUI lacks a validated herdr integration.

Herder compatibility is determined from the running server reached through the profile's named-session socket. A protocol mismatch fails preflight. Protocol development and live smokes use a disposable named session rather than an operator's active server.

Headless `claude -p`, `codex exec`, and equivalent one-shot reasoning commands are not dispatch backends.

## Consequences

- Harness routing remains data in the registry rather than workflow branches.
- Tmux-specific session, pane, socket, and metadata names are normalised at the transport boundary.
- Live workers are attached execution resources only; disk remains state authority.
- The first disposable flight exercises the herder retained-session path as well as explicit tmux fallback.
- Adding a backend requires transport-contract tests, preflight compatibility checks, lifecycle evidence, and documentation in the same change.

