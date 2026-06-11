# E17. Gate Decision Semantics

Active per-epic plan for E17 in `docs/backlog.md`. The backlog defines the open
work; this file sequences it into small, independently-reviewable,
independently-committable coding-agent prompts. Each prompt is the contract
between the operator and the coding agent: a tight scope, the files it touches,
the tests it adds, and a review checkpoint.

E17 makes every accepted gate decision verb produce its documented effect, and
makes the advertised decision surface provably equal to the implemented one. It
owns the readiness-gate resolution work moved out of E2. It does not add a `woof
graph` API, a split-skill suite, confidence-based gating, or auto-approval of any
human gate (the one opt-in plan auto-approve is E21 S5, not E17). The graph stays
graph-led: deterministic Python picks the next node; gate resolution is the one
operator decision point and every verb it offers must move the graph.

## As-is (what E17 changes)

Verified against the current tree, so the delta is exact:

- **One verb surface, six hand-maintained copies.** The valid-decision set is
  duplicated across `argparse --resolve choices` (`cli/commands/wf.py`), two
  inline `if decision not in {...}` blocks inside `_apply_gate_resolution_effects`
  (plan-gate and story/review branches), the `GateDecision` `Literal` in
  `graph/state.py`, the `gate_resolved.decision` enum in
  `schemas/jsonl-events.schema.json`, and the operator docs
  (`skills/woof/references/gates.md`, `skills/woof/SKILL.md`,
  `docs/architecture.md`). They already disagree in places. E17 makes one data
  table canonical and derives the rest.
- **`readiness_gate` has no legal resolution.** `_apply_gate_resolution_effects`
  has no `readiness_gate` branch, so it falls through to `return changed` with no
  effect; `_gate_resolved_event_name` returns `None` for it; and
  `transitions.readiness_satisfied` only treats a `readiness_passed` event after
  the latest `definition_closed` as satisfying. There is no verb that records
  readiness approval, so a readiness failure during consumer bootstrap is a
  reset-or-stuck loop. This is the slice that blocks E3.
- **`abandon_story` lies.** It writes `status="done"` and appends a
  `story_completed` event (`cli/commands/wf.py`). There is no `abandoned`
  terminal status; `StorySpec.status` is `Literal["pending","in_progress","done"]`.
- **`abandon_epic` is a no-op.** It is in the valid sets for plan and story/review
  gates but no branch implements an effect; it falls through. The epic is not
  marked abandoned and the tracker issue is not closed-as-not-delivered.
- **`revise_epic_contract` is not a real channel.** For the plan gate it only
  deletes `plan.json`/`PLAN.md`/`critique/plan.md`; it does not archive the prior
  `EPIC.md` or re-dispatch definition with the prior epic plus findings.
- **`split_story` is advertised everywhere but duplicates re-scope.** It is in the
  argparse choices, the story/review valid set, the `GateDecision` literal, the
  jsonl enum, and the docs, and its effect is identical to `revise_story_scope`.
- **No `retry_story`.** A crashed/aborted executor has no verb to reset the story
  to `pending` and clear its artefacts.

## Goal

After E17, a single canonical per-gate-type decision table in Python is the only
place the verb-to-gate mapping and per-verb effect live. The CLI `--resolve`
choices, `_apply_gate_resolution_effects`, the `gate.md` rendered resolution
options, the schemas, and the docs all derive from or are conformance-checked
against that table. Resolving an open gate with a verb not valid for its type is a
structured error that names the valid set. Every advertised verb has an observable
effect and a test: a per-verb forward-progress property test plus a
decision-surface conformance test (the analogue of the check-matrix conformance
test) guarantee advertised equals implemented. `split_story` is dropped; split
guidance travels as an optional note in the resolution payload and re-enters
planning through `revise_plan`. Stories and epics gain an `abandoned` terminal
status: `abandon_story` marks `abandoned` (not `done`), `abandon_epic` marks the
epic abandoned, closes the tracker issue as not delivered, and makes `next_node`
terminal for the epic. `revise_epic_contract` is a real channel that archives the
prior `EPIC.md` and re-dispatches definition with the prior epic plus
critique/readiness findings as declared inputs, hand-editing forbidden.
`retry_story` resets a crashed story to `pending`, clears its
executor/check/critique artefacts, and audits the reset. Readiness gates resolve
through the same table: `approve_with_reason` records audited readiness approval so
the unchanged epic does not re-gate, and `revise_epic_contract`/`abandon_epic` use
the shared canonical effects.

## Adopted decision table

The canonical mapping (gate type to allowed verbs). `split_story` is removed;
tracker-conflict decisions are unchanged and stay owned by the tracker layer.

| Gate type | Allowed verbs |
|---|---|
| `readiness_gate` | `approve_with_reason`, `revise_epic_contract`, `abandon_epic` |
| `plan_gate` | `approve`, `revise_plan`, `revise_epic_contract`, `abandon_epic` |
| `story_gate` / `review_gate` | `approve`, `retry_story`, `revise_story_scope`, `revise_plan`, `abandon_story`, `abandon_epic` |
| `tracker_sync_conflict` | `keep_local`, `accept_remote`, `hand_merge` (unchanged) |

## Stories

| ID | Story | Acceptance criteria |
|---|---|---|
| S1 | Canonical decision table as data + structured invalid-verb error | A single module (`src/woof/graph/decisions.py`) defines, per gate type, the allowed verbs and a per-verb effect descriptor. `argparse --resolve choices`, `_apply_gate_resolution_effects`'s validity checks, and the `GateDecision` literal derive from it; the `jsonl-events` decision enum and the `gates.md`/`SKILL.md` verb lists are conformance-checked against it. Resolving an open gate with a verb not valid for its type raises a `StageStateError` that names the valid set for that gate. `split_story` is removed from every surface. No behaviour change to the verbs that already work. |
| S2 | Readiness-gate resolution verbs (E3 unblocker) | `_apply_gate_resolution_effects` gains a `readiness_gate` branch. `approve_with_reason` records an audited readiness approval (a `readiness_gate_resolved` event with `decision=approve_with_reason` after the latest `definition_closed`) that `transitions.readiness_satisfied` treats as satisfying, so the unchanged epic advances to planning instead of re-gating. `_gate_resolved_event_name` maps `readiness_gate -> readiness_gate_resolved`. `revise_epic_contract` and `abandon_epic` route to the shared canonical effects (S5/S4 deepen them; S2 wires the legal seam). A readiness gate is legally resolvable end to end. |
| S3 | `retry_story` for crashed/aborted executors | Story/review gates accept `retry_story`: reset the story to `pending`, remove that story's `check-result.json`, `executor_result.json`, and story critique/disposition artefacts, and append an audited `story_retried` event. `next_node` re-dispatches the story cleanly. No effect on sibling stories. |
| S4 | `abandoned` terminal status | Add `abandoned` to `StorySpec.status` and `plan.schema.json`'s story status enum. `abandon_story` marks the story `abandoned` (not `done`) and no longer appends `story_completed`; it appends `story_abandoned`. `abandon_epic` marks the epic abandoned (a graph-owned `epic_abandoned` event / marker), closes the tracker issue as not delivered, and `next_node` becomes terminal for an abandoned epic (distinct from `EPIC_COMPLETE`). Reconstruction from disk distinguishes abandoned from done. |
| S5 | Real `revise_epic_contract` channel | For plan and readiness gates, `revise_epic_contract` archives the prior `EPIC.md` (e.g. `definition/EPIC.<n>.archived.md`) and re-dispatches the definition node with the prior epic plus the relevant critique/readiness findings as declared `inputs`. Hand-editing the contract remains forbidden. The plan-gate path keeps removing the now-stale plan artefacts; the readiness-gate path re-enters definition rather than just deleting plan files. |
| S6 | Decision-surface conformance test | A conformance test, modelled on the check-matrix conformance test, asserts: every verb advertised for a gate type has an implemented effect and a forward-progress test; no implemented effect exists for an unadvertised verb; the `argparse` choices, `GateDecision` literal, jsonl decision enum, and `gates.md`/`SKILL.md` verb lists all match the canonical table. `split_story` appears on no surface. |

## Prompt sequence

Sequenced per the backlog instruction to front-load the decision table and the
readiness verbs ahead of the story/plan-gate verbs and abandoned-status semantics,
because the readiness slice (P1 + P2) is what unblocks E3. P1 is pure
consolidation with no behaviour change; P2 makes readiness gates resolvable; P3-P5
deepen the story/epic verbs; P6 locks advertised-equals-implemented.

Landed on `main`: prompt 1 (S1) — `src/woof/graph/decisions.py` is canonical;
the `--resolve` choices, `GateDecision` literal, and `jsonl-events` decision enum
all match its union; `split_story` is removed from every surface it touched.
Prompt 2 (S2, the E3 unblocker) — `decisions.py` now carries a `readiness_gate`
row (`approve_with_reason`, `revise_epic_contract`, `abandon_epic`),
`_apply_gate_resolution_effects` validates and resolves readiness gates,
`_gate_resolved_event_name` maps `readiness_gate -> readiness_gate_resolved`, and
`transitions.readiness_satisfied` now treats a `readiness_gate_resolved` event
with `decision=approve_with_reason` after the latest `definition_closed` as
satisfying, so an operator-approved unready contract advances to planning without
re-gating (D-RA settled); readiness `revise_epic_contract`/`abandon_epic` are
wired as legal seams that P5/P4 deepen. Prompt 3 (S3) — `retry_story` is now a
story/review-gate verb: `_apply_gate_resolution_effects` resets the story to
`pending` via `mark_story_status`, clears that story's `check-result.json`,
`executor_result.json`, and critique/disposition artefacts, and appends an audited
`story_retried` event (added to the `decisions.py` story/review rows, the
`GateDecision` literal, and the `jsonl-events` decision/event enums), so `next_node`
re-dispatches the crashed story without touching its siblings; the verb is guarded
to its domain, rejecting both a story-less gate and an already-`done` story with a
structured error (it resets crashed/aborted executors, never completed stories, so
it cannot strand a `story_completed` event), with post-completion completion-event
reconciliation deferred to E18. Prompt 4 (S4) — stories and epics gain an
`abandoned` terminal status (D-AB settled): `StorySpec.status` and the
`plan.schema.json` story-status enum now include `abandoned`; `abandon_story` marks
the story `abandoned` (not `done`) and appends a `story_abandoned` event instead of
`story_completed`, so `next_node` treats it as terminal-skipped and the epic still
completes on its remaining stories; `abandon_epic` is now one shared effect routed
from every gate type that offers it (readiness/plan/story/review) — it closes the
tracker issue as not delivered (`Tracker.close_not_delivered`, with GitHub using the
`not planned` close reason, and `complete_epic`'s done-guard now accepting the
terminal `abandoned` status) and appends a graph-owned `epic_abandoned` marker that
`transitions.next_node` consults to return a distinct `NodeStatus.EPIC_ABANDONED`
terminal (surfaced by the runner, never conflated with `EPIC_COMPLETE`), and
reconstruction from disk keeps `abandoned` distinct from `done`. Prompts 5-6 remain.

| # | Prompt summary | Files touched | Tests | Review checkpoint |
|---|---|---|---|---|
| 1 | **(S1)** Add `src/woof/graph/decisions.py`: a `GATE_DECISIONS` table mapping each gate type to its allowed verbs and a per-verb effect tag, plus helpers `allowed_decisions(gate_type)` and `validate_decision(gate_type, decision)` raising a `StageStateError` naming the valid set. Refactor `_apply_gate_resolution_effects` and `_resolve_gate` to validate through the table instead of inline `if decision not in {...}` blocks; derive `argparse --resolve choices` from the table union; derive the `GateDecision` literal from the table (or assert equality in a test). Remove `split_story` from the table, the literal, the argparse choices, and the jsonl enum. No effect-behaviour change for surviving verbs. | `src/woof/graph/decisions.py` (new), `src/woof/cli/commands/wf.py`, `src/woof/graph/state.py`, `schemas/jsonl-events.schema.json`, this plan | `tests/unit/test_gate_decisions.py` (new): table-derived argparse choices; invalid verb per gate type raises naming the valid set; `split_story` rejected; surviving verbs unchanged. Update any decision-enum snapshot. | One table is canonical; an invalid verb is a structured error; `split_story` is gone everywhere; `just check` green. |
| 2 | **(S2, E3 unblocker)** Add the `readiness_gate` branch to `_apply_gate_resolution_effects`. `approve_with_reason` appends a `readiness_gate_resolved` event (carrying the decision and an optional guidance note) after the latest `definition_closed`; extend `transitions.readiness_satisfied` to accept that event as satisfying so the unchanged epic advances. Map `readiness_gate -> readiness_gate_resolved` in `_gate_resolved_event_name`. Route readiness `revise_epic_contract`/`abandon_epic` to the shared canonical-effects path (full behaviour lands in P5/P4; here they must be legal and not no-op). Add `approve_with_reason` to the jsonl decision enum and the readiness-gate row of the table. | `src/woof/cli/commands/wf.py`, `src/woof/graph/transitions.py`, `src/woof/graph/decisions.py`, `schemas/jsonl-events.schema.json` | `tests/unit/test_contract_readiness.py` / `tests/unit/test_gate_decisions.py`: `approve_with_reason` advances the unchanged epic to planning without re-gating; a re-closed contract re-runs readiness; readiness `abandon_epic` is terminal; invalid readiness verb errors. | A readiness gate is legally resolvable end to end and `approve_with_reason` does not re-gate; `just check` green. (Settles D-RA.) |
| 3 | **(S3)** Add `retry_story` to the story/review branch: reset the story to `pending` via `mark_story_status`, remove `check-result.json`, `executor_result.json`, and the story critique/disposition artefacts, and append an audited `story_retried` event. Confirm `next_node` re-dispatches the reset story without redoing siblings. | `src/woof/cli/commands/wf.py`, `src/woof/graph/transitions.py` (only if next_node needs to recognise a reset), `src/woof/graph/decisions.py` | `tests/unit/test_graph.py` / `test_wf_*`: `retry_story` resets to pending and clears artefacts; sibling stories untouched; reset is audited; re-dispatch path reached. | A crashed story re-runs cleanly via one verb; `just check` green. |
| 4 | **(S4)** Add `abandoned` terminal status. Extend `StorySpec.status` and `plan.schema.json` story status enum with `abandoned`. `abandon_story` marks the story `abandoned` and appends `story_abandoned` (not `story_completed`). `abandon_epic` appends a graph-owned `epic_abandoned` event, closes the tracker issue as not delivered, and `next_node` returns an abandoned-terminal outcome distinct from `EPIC_COMPLETE`. | `src/woof/graph/state.py`, `schemas/plan.schema.json`, `schemas/jsonl-events.schema.json`, `src/woof/cli/commands/wf.py`, `src/woof/graph/transitions.py`, `src/woof/trackers/*` (close-not-delivered) | `tests/unit/test_graph.py`, `test_trackers.py`, `test_wf_*`: abandoned story is terminal and not `done`; abandoned epic closes the tracker issue not-delivered and `next_node` is terminal; reconstruction distinguishes abandoned from done. | Abandon verbs are honest and terminal; `just check` green. |
| 5 | **(S5)** Make `revise_epic_contract` a real channel for plan and readiness gates: archive the prior `EPIC.md` to `definition/EPIC.<n>.archived.md`, re-dispatch the definition node with the prior epic plus critique/readiness findings as declared `inputs`, and keep hand-editing forbidden. Plan-gate path still clears stale plan artefacts; readiness-gate path re-enters definition. | `src/woof/cli/commands/wf.py`, `src/woof/graph/transitions.py`, `src/woof/graph/nodes.py` (definition re-dispatch inputs), `src/woof/graph/decisions.py` | `tests/unit/test_wf_*` / `test_graph.py`: revise_epic_contract archives the prior EPIC.md, re-enters definition with findings as inputs, forbids hand-edit; round-trips from both plan and readiness gates. | The contract-revision channel actually re-opens definition with evidence; `just check` green. |
| 6 | **(S6)** Add `tests/unit/test_decision_surface_conformance.py`: assert every advertised verb per gate type has an implemented effect and a forward-progress test, no effect exists for an unadvertised verb, and the argparse choices, `GateDecision` literal, jsonl enum, and `gates.md`/`SKILL.md` verb lists all equal the canonical table. Update `gates.md`/`SKILL.md` verb lists to the real set (drop the "current limitation" hedges that E17 resolves); leave the deeper section-10 prose reconciliation to E23. | `tests/unit/test_decision_surface_conformance.py` (new), `skills/woof/references/gates.md`, `skills/woof/SKILL.md` | The conformance test itself; it fails if any surface drifts from the table. | Advertised equals implemented, enforced by a test; `just check` green. |

## Risk register

- Verb surface re-fragments after consolidation: P6's conformance test fails if any of the six surfaces drifts from `decisions.py`. Treat the table as the only place to add or remove a verb. (Echoes the implementation-plan risk "Gate verbs look available but do not move the graph".)
- The readiness slice slips behind the rest and keeps E3 blocked: P1+P2 are front-loaded and self-contained; `approve_with_reason` and a legal `revise_epic_contract`/`abandon_epic` are the only verbs E3 bootstrap strictly needs, so E3 can proceed after P2 even before P3-P5 land.
- `abandoned` status leaks into "done" accounting: P4 adds a distinct status and a distinct `story_abandoned`/`epic_abandoned` event, and `next_node` returns an abandoned-terminal outcome separate from `EPIC_COMPLETE`; reconstruction tests assert the two are not conflated.
- `revise_epic_contract` silently loses the prior contract: P5 archives `EPIC.md` before re-dispatch rather than deleting it, and feeds the prior epic plus findings as declared inputs so the revision is evidence-driven, not a blank re-write.
- E17 over-reaches into E23 doc work: E17 updates only the verb lists that derive directly from the table (`gates.md`, `SKILL.md`) and the jsonl enum; the section-10/section-3 prose reconciliation and the story-gate `stage: 5` schema migration ride E23, which depends on E17's settled table.
- Auto-approval creeps in: E17 never auto-resolves a human gate. The only auto-approve is E21 S5's opt-in plan-gate path, explicitly out of E17 scope.

## Decisions resolved during the epic

| ID | Decision | Resolution |
|---|---|---|
| D-DT | Where does the canonical decision table live and what shape is it? | A new `src/woof/graph/decisions.py` module: `GATE_DECISIONS` mapping gate type -> ordered allowed verbs, plus a per-verb effect tag. Validity, argparse choices, the `GateDecision` literal, and the gate.md option render all derive from it; schemas and docs are conformance-checked against it. Settled in P1. |
| D-RA | How does an `approve_with_reason` readiness resolution satisfy readiness without re-running the node? | Event-based, matching the D-RD pattern from E2: a `readiness_gate_resolved` event with `decision=approve_with_reason` recorded after the latest `definition_closed` satisfies `readiness_satisfied`, alongside the existing `readiness_passed` path. A re-closed contract (new `definition_closed`) re-arms readiness. Settled in P2. |
| D-AB | What records epic-level abandoned terminality? | A graph-owned `epic_abandoned` event consulted by `next_node` to return an abandoned-terminal outcome distinct from `EPIC_COMPLETE`, plus the tracker issue closed as not delivered. Story-level uses `status=abandoned` and a `story_abandoned` event. Settled in P4. |
| D-RC | What does `revise_epic_contract` do with the prior `EPIC.md`? | Archive it to `definition/EPIC.<n>.archived.md` and re-dispatch definition with the prior epic plus critique/readiness findings as declared `inputs`; hand-editing stays forbidden. Settled in P5. |
| D-SS | What replaces `split_story`? | Dropped from every surface. Split guidance travels as an optional guidance note in the resolution payload and re-enters planning through `revise_plan`. Settled in P1. |

## Out of scope

- A `woof graph` command API, a split-skill suite, or any new peer skill (withdrawn directions; ADR-007).
- Confidence-based gating or any model-to-model debate loop (ADR-006).
- Plan-gate auto-approval (E21 S5, opt-in) and trivial-epic tiers (E21 S4). E17 never auto-resolves a human gate.
- The story-gate `stage: 5` schema migration and the section-10/section-3 architecture prose reconciliation (E23, which depends on E17's settled table). E17 updates only the table-derived verb lists.
- Run-resilience, drift, baseline-mode, and blocker-evidence work (E2). E17 only consumes E2's shipped S1/S2 readiness seam.
- Cross-repo edits to any consumer; E17 changes Woof itself.

## Done definition

- All stories' acceptance criteria met.
- All review checkpoints passed.
- All decisions in the table resolved.
- One canonical decision table; every advertised verb per gate type has an observable effect and a forward-progress test; the decision-surface conformance test passes; `split_story` is absent from every surface.
- A readiness gate is legally resolvable end to end, unblocking E3.
- `just check` green.
