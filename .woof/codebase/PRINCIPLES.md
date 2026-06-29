---
layer: design
status: complete
authored_by: operator
refresh: human-driven (only when design principles change)
---

# Principles

Cross-cutting design principles for Woof. Design-layer cartography (ADR-004):
human-authored, loaded into definition, planning, and execution dispatch payloads.
These are the rules a producer or reviewer must not violate; `CONVENTIONS.md` covers
line-level style, this covers shape and intent.

## Orchestration and state

- **Deterministic orchestration.** Graph transitions, gates, schema validation, and
  transaction manifests are deterministic Python. LLM inference is typed producer /
  reviewer / mapper work *within* the graph. No LLM picks a successor; control flow is a
  function of on-disk state.
- **State on disk is authoritative.** Filesystem state under `.woof/` is canonical.
  Operator-skill and dispatched context is opportunistic and must be reconstructable from
  disk. On crash-resume, if the JSONL audit and the filesystem disagree, the filesystem
  wins.
- **Idempotent.** Migrations, ingestion, setup, and refresh scripts are safe to replay.
  Running a node twice from the same disk state yields the same position.

## Contracts and types

- **Contract-first.** JSON Schema is the canonical, portable contract for every artefact
  that crosses a durable JSON, CLI, LLM-node, or consumer boundary. Python implements
  transitions and validation; prompt files give guidance only; shell snippets are
  examples, never orchestration authority.
- **Typed boundaries, one shape.** Pydantic at schema/serialisation boundaries;
  dataclasses for trusted in-process records. No transitional mirrors and no
  shape-divergence between a model's output and its projection — enforce the same shape at
  every seam that touches it.

## Safety and failure

- **Fail loud.** Missing, malformed, or unsafe state opens a gate or fails preflight. It
  is never silently repaired by a prompt or a tolerant parser. Graded recovery (salvage,
  normalise, bounded retry) is deterministic and fails loud on anything it cannot prove.
- **Commit-safety is a hard boundary.** A producer cannot land changes outside its
  declared work-unit scope: the transaction manifest enumerates the expected file set and the
  commit aborts on any divergence. Unexpected HEAD/branch movement opens a drift gate.
- **Reviewer findings cite evidence.** A `blocker` resolves to concrete current
  artefacts (file:line, work-unit id, outcome id, decision id, schema ref, or gate id).
  Confidence is advisory metadata, never part of the gate decision.

## Execution discipline

- **Tracer-bullet TDD.** Stage 5 is red-green-refactor per declared outcome: one
  assertion-bearing RED test before implementation, the smallest vertical GREEN slice,
  then refactor with tests as the harness. Horizontal slicing that mirrors guessed data
  structures rather than proving declared behaviour is rejected.
- **No mocks.** Tests exercise real services and real artefacts. Verification is the
  deterministic check matrix, not a mocked stand-in.
- **No tech debt carried forward.** Converge toward `TARGET-ARCHITECTURE.md`; close
  shortfalls in that direction rather than extending them.

## Surfaces

- **One operator surface.** The `/woof` umbrella covers setup, map-codebase, run, gate,
  reset, observe, and onboarding over the `woof` CLI; `/woof:brainstorm` is the only
  specialist because interactive design is a distinct loop. No proliferation of
  per-action skills.
- **Opinionated expert workstation.** Woof may require expert-local tooling when it
  materially improves supervision or correctness. tmux is allowed as a long-run
  monitor/supervisor but never owns workflow state or graph transitions.
