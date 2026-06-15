# E19. Cartography Consumption

## Goal

Every dispatch-shaped node actually receives the cartography documents it is mapped to in `docs/architecture.md`. Payload construction reads the mapped `.woof/codebase/` documents and records them in `artefacts_loaded[]`; missing documents halt the run with `incomplete_stage_state` rather than silently dispatching cold. Every producer and reviewer playbook explicitly reads those context documents first. Woof itself is a conforming consumer: the `[cartography]` block, `scripts/refresh-cartography`, the post-commit hook, and the AS-IS mapper documents are in place, and Ryan has authored the DESIGN-layer documents, so `woof preflight` passes clean in the Woof repo.

## Stories

| ID | Story | Acceptance criteria |
|---|---|---|
| S1 | Per-node payload wiring + `incomplete_stage_state` halt | Each dispatch node's payload builder reads the mapped `.woof/codebase/` docs (per the `docs/architecture.md` loading map) and appends them to `artefacts_loaded[]`. `executor_dispatch` additionally receives the story-scoped `files.txt` slice via `pathspec`. A missing mapped doc raises `StageStateError` (trigger `incomplete_stage_state`) using the node's natural gate type. Unit tests: payload contains exactly the mapped refs; missing-doc path raises and opens the correct gate type; `files.txt` slice present in executor payload. |
| S2 | Playbook context discipline | Every producer and reviewer playbook (`playbooks/**/*.md`) opens with an explicit "Read these documents first" section that names the context documents listed in the payload. No playbook embeds context that should come from the payload. Integration smoke: dispatch of any node with cartography present logs the mapped paths in `artefacts_loaded[]`. |
| S3 | Woof mechanical self-onboarding (unattended-safe) | `.woof/prerequisites.toml` gains a `[cartography]` block. `scripts/refresh-cartography` is added from the Python language template via `woof init --language python`. The post-commit hook is installed. AS-IS mapper documents are authored via the `/woof` map-codebase flow (`CURRENT-ARCHITECTURE.md`, `STACK.md`, `INTEGRATIONS.md`, `STRUCTURE.md`, `CONVENTIONS.md`, `TESTING.md`, `CONCERNS.md`). Mechanical-layer files (`tags`, `files.txt`, `freshness.json`) are present. `woof preflight` passes all cartography checks except the DESIGN-layer docs (S4 follows). |
| S4 | Woof DESIGN-layer authoring — **OPERATOR-GATED** | Ryan authors `.woof/codebase/TARGET-ARCHITECTURE.md` and `.woof/codebase/PRINCIPLES.md`. A coding agent may scaffold clearly-marked empty stubs for Ryan to fill; it must not write the architectural decisions or principles as authoritative content. Once Ryan has authored both files and removed the stub marker, `woof preflight` passes clean end-to-end. |

## Prompt sequence

| # | Prompt summary | Files touched | Review checkpoint |
|---|---|---|---|
| 1 | **(S1)** Add cartography-doc path resolution to each dispatch node's artefact loader. Per the `docs/architecture.md` loading map, inject the mapped `.woof/codebase/` docs into `inputs` and `artefacts_loaded[]` for: `discovery_research` (STACK, INTEGRATIONS, CONCERNS), `discovery_thinking` (CURRENT-ARCHITECTURE, STRUCTURE), `discovery_synthesis` (same as thinking bucket), `epic_definition` (CURRENT-ARCHITECTURE, STRUCTURE, CONCERNS, TARGET-ARCHITECTURE, PRINCIPLES), `contract_readiness` (CURRENT-ARCHITECTURE, STRUCTURE, CONVENTIONS, TESTING, TARGET-ARCHITECTURE, PRINCIPLES, `files.txt`), `breakdown_planning` (CURRENT-ARCHITECTURE, STRUCTURE, TARGET-ARCHITECTURE, PRINCIPLES), `plan_critique` (CURRENT-ARCHITECTURE, STRUCTURE, CONCERNS, TARGET-ARCHITECTURE), `executor_dispatch` (STRUCTURE, CONVENTIONS, TARGET-ARCHITECTURE, PRINCIPLES, story-scoped `files.txt` slice), `critique_dispatch` (CONVENTIONS, TESTING, CONCERNS). A missing mapped doc raises `StageStateError(gate_type=<node-appropriate gate type>, triggered_by=["incomplete_stage_state"])`. Include the legality matrix and event-log consumer checklist (see below). | `src/woof/graph/nodes.py`, `tests/unit/test_nodes.py` | Each node's payload contains exactly the mapped refs when docs are present; a missing required doc opens the correct gate; `executor_dispatch` payload includes a story-scoped `files.txt` subset. `just check` green. |
| 2 | **(S2)** Update every playbook (`playbooks/discovery/*.md`, `playbooks/planning/*.md`, `playbooks/execution/story.md`, `playbooks/critique/plan.md`, `playbooks/critique/story.md`) to start with an explicit "Context documents — read these first" section listing the names of documents that the payload delivers. Remove any playbook prose that embeds context that should come from payload-delivered docs. | `playbooks/**/*.md` | Each playbook's read-first section names the doc set that matches the architecture loading map for that node. No duplication of context that lives in `artefacts_loaded[]`. |
| 3 | **(S3)** Mechanical Woof self-onboarding: add the `[cartography]` block to `.woof/prerequisites.toml` (declaring Python language, `staleness_floor_hours`, `summary_min_chars`, `stub_marker`); run `woof init --language python` to compose `scripts/refresh-cartography`; install the post-commit hook via `woof hooks install`; run the `/woof` map-codebase flow to author the seven AS-IS mapper documents; run `scripts/refresh-cartography` to produce `tags`, `files.txt`, `freshness.json`. | `.woof/prerequisites.toml`, `scripts/refresh-cartography`, `.woof/codebase/CURRENT-ARCHITECTURE.md`, `.woof/codebase/STACK.md`, `.woof/codebase/INTEGRATIONS.md`, `.woof/codebase/STRUCTURE.md`, `.woof/codebase/CONVENTIONS.md`, `.woof/codebase/TESTING.md`, `.woof/codebase/CONCERNS.md` | `woof preflight` passes all cartography checks that do not require the DESIGN-layer docs. The seven AS-IS docs are present, non-stub, and above `summary_min_chars`. `just check` green. |
| 4 | **[OPERATOR-GATED — S4]** Scaffold empty stub files `.woof/codebase/TARGET-ARCHITECTURE.md` and `.woof/codebase/PRINCIPLES.md` with the `stub_marker` in place and clear placeholder prose. Ryan fills in the architectural decisions and principles; removes the stub marker. After authoring, Ryan runs `woof preflight` to confirm the full cartography check passes. | `.woof/codebase/TARGET-ARCHITECTURE.md`, `.woof/codebase/PRINCIPLES.md` | `woof preflight` exits 0 with no cartography findings. The DESIGN-layer documents are authored (not stub) as confirmed by `summary_min_chars` and absence of `stub_marker`. |

### `incomplete_stage_state` legality matrix (P1)

New call sites introduced by S1 raise `StageStateError` (`operator_recoverable=True`, `triggered_by=["incomplete_stage_state"]`) when a mapped cartography document is missing from `.woof/codebase/` at payload-build time. The gate type follows the node:

| Node | Gate type opened |
|---|---|
| `discovery_research`, `discovery_thinking`, `discovery_ideate`, `discovery_synthesis` | `plan_gate` (no story_id at Stage 1) |
| `epic_definition`, `contract_readiness` | `plan_gate` (no story_id at Stage 2) |
| `breakdown_planning`, `plan_critique` | `plan_gate` |
| `executor_dispatch`, `critique_dispatch` | `story_gate` (story_id always present at Stage 5) |

### Event-log consumer checklist (P1)

Every reader of a `gate_opened` / `incomplete_stage_state` event must handle the new cartography-missing halt path without misclassifying it:

| Consumer | Required behaviour |
|---|---|
| `woof wf` runner (`src/woof/graph/runner.py`) | Catches `StageStateError(operator_recoverable=True)` from both `next_node` and node handler calls; calls `_open_stage_state_gate` in both paths. Node handlers raise `StageStateError` directly; `_cartography_missing_gate` was removed (R1 dedup). |
| `woof observe` (`src/woof/cli/commands/observe.py`) | Already maps `incomplete_stage_state` trigger to a labelled node display (lines ~295, ~302, ~330). Verify the cartography-missing variant surfaces with a readable message pointing at the missing file. |
| Bench harness (`src/woof/bench/efficiency.py`) | Reads `gate_opened` events for gate summary. A cartography-missing gate must not be mistaken for a plan gate; `gate_type` field in `gate.md` must be correct so `_gate_summary` classifies it correctly. |
| `woof wf --resolve` | Resolves via the existing story-gate or plan-gate decision path. No new verb required. The gate body must describe the missing document so the operator knows to run `scripts/refresh-cartography` or re-author missing docs before resolving. Resolution with `approve` uses `NON_APPROVING_TRIGGERS` guard (R1) so no plan-summary or plan-approval effects run. |
| `plan_gate_resolved()` (`src/woof/graph/transitions.py`) | **R1 fix.** Now excludes `NON_APPROVING_TRIGGERS` (`incomplete_stage_state`) from the approve branch, identically to `CONFLICT_TRIGGERS`. A `gate_resolved` event with `gate_type=plan_gate`, `decision=approve`, `triggered_by=[incomplete_stage_state]` returns False — the mandatory Stage-4 plan approval is not satisfied. |
| `_apply_gate_resolution_effects` (`src/woof/cli/commands/wf.py`) | **R1 fix.** Early-returns with no effects when `decision=approve` and any `NON_APPROVING_TRIGGERS` trigger is present. Prevents `tracker.push_plan_summary` from running before `plan.json` exists (pre-plan guard) and prevents story-completion effects for a cartography story_gate halt. |
| End-of-epic detection (`next_node`) | A halted epic with `gate.md` present is not complete. No special case needed; the existing gate-presence short-circuit in `next_node` handles it. Verify no path skips the gate check for this trigger. |
| Tracker sync | Gate state is not a terminal state. Trackers must not mark the epic closed. Existing tracker logic reads graph status, not `triggered_by`; verify no regression. |

## Risk register

- **Silent cold-dispatch if check is gated on preflight**: the payload builder must check doc presence at build time, not rely on preflight having run. Preflight happens per-repo setup, not per-dispatch. Mitigation: implement the missing-doc check directly in each artefact loader, not as a preflight fast-path.
- **`files.txt` scope is the full repo, not story-scoped**: the architecture loading map says `executor_dispatch` receives the "story-scoped `files.txt` slice" via the pathspec module. The current `pathspec` module (`src/woof/graph/pathspec.py`) operates on staged paths and `git ls-files`, not a pre-sliced `files.txt`. Mitigation: P1 must clarify whether the executor payload injects `files.txt` as-is or filters it through the story's `paths[]`; record the decision in the Decisions table before implementing.
- **Playbook edits cause token regressions**: adding "read these first" sections increases playbook prompt size. Mitigation: P2 removes duplicated embedded context when it is now payload-delivered, targeting no net increase.
- **AS-IS mapper docs go stale quickly**: they are authored once during S3 and not auto-refreshed mid-epic. Mitigation: ADR-004 intentionally leaves mid-epic remap to the operator. S3 produces the initial run; the post-commit hook keeps the mechanical layer fresh. Stale-AS-IS is a warning, not a blocker.
- **DESIGN-layer stub leaks into a dispatch before Ryan authors it**: if S3 lands before S4 and a dispatch runs, `TARGET-ARCHITECTURE.md` and `PRINCIPLES.md` may be stub files, triggering a preflight failure or a cartography-missing halt. Mitigation: sequence S3 verification step to confirm preflight status before opening S4; document that stub files are intentional at S3 close, and S4 removes the stub marker.

## Decisions resolved during the epic

| ID | Decision | Resolution |
|---|---|---|
| D1 | Is the `files.txt` slice in `executor_dispatch` a full-file copy or a pathspec-filtered subset? | Filtered subset. `_executor_files_txt_slice` reads `files.txt`, then calls `pathspec.filter_paths_matching(repo_root, candidates, story.paths)` to produce a story-scoped list. If `files.txt` is empty or `story.paths` is empty, the candidates list is returned as-is without a git call. The filtered list goes into `inputs.files_txt_slice`; `files.txt` itself is recorded in `artefacts_loaded`. |
| D2 | Which gate type does a cartography-missing halt open at Stage 1 and Stage 2 nodes (no plan yet)? | `plan_gate`. The gate schema requires `story_gate` to carry a non-null `story_id`; planning nodes have no story_id. `plan_gate` (null story_id, stage 4) is the correct type for all non-execution nodes. `executor_dispatch` and `critique_dispatch` (which always have a story_id) use `story_gate`. The legality matrix above has been corrected accordingly. |
| D3 | Does S3 commit the AS-IS mapper docs, or are they gitignored? | ADR-004 says the design and AS-IS markdown docs are committed planning state; the mechanical layer (`tags`, `files.txt`, `freshness.json`) is gitignored. Confirm `.gitignore` matches before P3 commit. |

## Out of scope

- Structural cartography index (ADR-009 extension under `.woof/codebase/structural/`) — E12 owns that pivot.
- Ranked or semantic retrieval — E14.
- Mid-epic auto-remap of the AS-IS or DESIGN layers (ADR-004: captured once; remap is operator-triggered).
- Any new operator surface or `woof graph` API (withdrawn directions).
- Cross-repo changes to any consumer other than Woof itself; this epic onboards only the Woof repo.
- Woof's `TARGET-ARCHITECTURE.md` and `PRINCIPLES.md` content decisions — Ryan authors those; the agent only scaffolds stubs.

## Done definition

- All stories' acceptance criteria met.
- All review checkpoints passed.
- All decisions in the table resolved.
- `woof preflight` passes clean in the Woof repo (zero cartography findings, DESIGN-layer docs authored by Ryan and above `summary_min_chars`).
- Integration smoke: a dispatch run records the mapped cartography paths in `artefacts_loaded[]` in `dispatch.jsonl`.
- `just check` green.
