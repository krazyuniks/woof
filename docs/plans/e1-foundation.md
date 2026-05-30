# E1. Foundation: Graph Library API

## Goal

Expose a stable `woof graph` API for the skill suite. The graph remains the deterministic authority for state, validation, gates, checks, transaction manifests, and typed audit writes. The skill owns LLM dispatch. No graph command dispatches a model.

## Stories

| ID | Story | Acceptance criteria |
|---|---|---|
| E1-S1 | Define graph API contracts | `next-node` JSON includes `kind`, `node_type`, optional `story_id`, `route_key`, transport hints, `prompt_template_path`, `expected_output_paths[]`, `expected_output_schemas[]`, `loaded_docs[]`, and `state_token`; schemas cover command outputs. |
| E1-S2 | Add state-token guarded mutation | Canonical state hashing is implemented; mutating graph commands require the observed token; stale-token mutation returns a structured non-zero error without writing state. |
| E1-S3 | Extract epic create/resume primitives | `woof graph create-epic` and `woof graph resume-epic` cover the behaviour currently hidden in `woof wf new` and cold-start fetch. |
| E1-S4 | Split dispatch contracts from deterministic nodes | Dispatch-shaped nodes return contracts only; deterministic nodes run through `run-deterministic-node`; graph node handlers no longer spawn LLM subprocesses. |
| E1-S5 | Add typed record verbs | Model-produced artefacts are accepted only through typed commands: discovery bucket, discovery synthesis, epic definition, plan, plan critique, executor result, story critique. No `record-disposition` command exists because review disposition is deterministic. |
| E1-S6 | Move gate resolution into graph API | `woof graph resolve-gate` owns the current gate-resolution effects and typed events. |
| E1-S7 | Rewrite route schema | `.woof/agents.toml` uses canonical `producer`, `reviewer`, `mapper`, `gate-resolver` routes with node-group or `route_key` overrides; legacy `primary` / `critiquer` read-tolerance remains migration-only. |
| E1-S8 | Retire new use of `woof wf` | `woof wf` is a temporary shim; init output, observe next actions, docs, and playbook prose no longer direct users or skills to it. |
| E1-S9 | Enrich dispatch-return telemetry | `record-dispatch-returned` records strict outcome/progress fields needed by future run resilience and drift detection: exit type/code, normalised error signature, HEAD/branch before and after, expected output presence/schema status, rate-limit metadata, timing, byte counts, token counts where available, and command count where available. |

## Prompt Sequence

| # | Prompt summary | Files touched | Review checkpoint |
|---|---|---|---|
| 1 | Add schemas and Python types for `next-node` contracts and state-token hashing; no CLI wiring yet. | `schemas/`, `src/woof/graph/state.py`, `src/woof/graph/transitions.py`, focused tests | Contract shape matches architecture; hash inputs are documented and deterministic. |
| 2 | Add `woof graph next-node` as a pure JSON query using the new contract. | `src/woof/cli/`, `src/woof/graph/`, tests | Existing graph state maps to `dispatch`, `deterministic`, `gate`, or `complete`; no mutation occurs. |
| 3 | Add typed dispatch telemetry commands: `record-dispatch-started`, `record-dispatch-returned`. | `src/woof/graph/`, `src/woof/cli/`, schemas/tests | No raw append command; events validate and require `state_token`; return telemetry includes outcome/progress fields for future circuit-breaker, rate-limit handling, and HEAD/branch drift detection. |
| 4 | Add typed model-output record verbs for Stage 1-4 planning outputs. | `src/woof/graph/`, `src/woof/cli/`, schemas/tests | Each verb validates only its expected artefact shape and appends typed events. |
| 5 | Add typed Stage-5 model-output record verbs: `record-executor-result`, `record-story-critique`. | `src/woof/graph/`, `src/woof/cli/`, schemas/tests | `record-disposition` is absent; malformed executor/critique outputs fail before state advances. |
| 6 | Add `run-deterministic-node` for plan gate, review disposition, verification, commit, and gate open. | `src/woof/graph/`, `src/woof/cli/`, tests | Deterministic nodes run without LLM dispatch; dispatch nodes are rejected by this command. |
| 7 | Extract `create-epic`, `resume-epic`, and `resolve-gate` from `wf.py` into graph/library commands. | `src/woof/trackers/`, `src/woof/graph/`, `src/woof/cli/commands/wf.py`, tests | Tracker semantics preserved; `wf.py` becomes a compatibility shim. |
| 8 | Rewrite agents route schema and route resolution around canonical role names and `route_key`. | `schemas/agents.schema.json`, `src/woof/cli/dispatcher.py`, `src/woof/cli/preflight.py`, tests | New configs validate; legacy configs remain read-tolerated with migration notices. |
| 9 | Sweep live operator text away from `woof wf`: init output, observe/preflight next actions, playbook prose. | Markdown docs, `src/woof/cli/init.py`, observe/preflight tests | No current user-facing path points at deleted docs or new `woof wf` usage. |
| 10 | Compatibility and regression pass. | tests/integration, tests/unit | Old `woof wf` tests are either shim tests or ported to `woof graph`; `just check` passes. |

## Risk Register

- Old dispatch loop survives behind a renamed command: dispatch-shaped nodes must be rejected by `run-deterministic-node`, and tests must prove graph commands do not call model CLIs.
- State-token scope is too narrow: include `epic.jsonl`, `plan.json`, `EPIC.md`, gate file, critique/disposition files, and transient Stage-5 result files that affect `next-node`.
- CLI surface becomes too generic: use typed verbs; shared implementation is allowed behind the command boundary.
- Dispatch telemetry is too thin: include operational and git-position fields now while the graph API is being defined, even though the `/woof:run` circuit breaker and drift gate are implemented later.
- Route schema migration breaks existing fixtures: retain read-tolerance for legacy `primary` / `critiquer` during E1 and add migration warnings.
- Compatibility shim consumes effort: keep `woof wf` thin and deadline-bound; do not add new behaviour there except delegation.

## Decisions Resolved During The Epic

| ID | Decision | Resolution |
|---|---|---|
| E1-D1 | One generic `record-output` or typed record verbs | Typed record verbs. Shared internal plumbing is allowed. |
| E1-D2 | Is review disposition model-produced? | No. `review_disposition` is graph-owned deterministic behaviour. |
| E1-D3 | Does `next-node` return cartography content? | No. It returns `loaded_docs[]` references for the skill to load and cache. |
| E1-D4 | Does the skill get a raw event append API? | No. Events are written by typed graph commands only. |
| E1-D5 | Does `/woof:run` cover epic creation? | Yes. The skill can create from spark, resume current, or resume explicit epic through graph primitives. |

## Out Of Scope

- Implementing the Claude Code skills.
- Building cartography refresh templates.
- Running the production-shape eval.
- Removing `woof wf` entirely.

## Done Definition

- All stories' acceptance criteria met.
- Every `woof graph` command has an explicit JSON contract and tests.
- Dispatch-return telemetry is rich enough for later `/woof:run` resilience and HEAD/branch drift detection without changing the event contract.
- Dispatch-shaped nodes no longer spawn model subprocesses from graph code.
- Deterministic nodes remain graph-owned and are not accepted through typed record verbs.
- Route schema uses canonical role names with migration read-tolerance.
- Live docs and operator strings do not direct new usage to `woof wf`.
- `just check` passes, or any unavailable external prerequisite is documented.
