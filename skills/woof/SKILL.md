---
name: woof
description: Drive the Woof delivery graph from Claude Code. The operator's map of the `woof` shell CLI - create and run epics, resolve gates, reset, validate, observe, and onboard a repo - plus the router to /woof:brainstorm for the design phase. Use when running Woof, an epic or a story, resolving a Woof gate, or onboarding a repo to Woof.
allowed-tools: Bash(woof:*), Bash(git:*), Task
---

# Woof - delivery graph operator

Woof is a deterministic delivery graph: from a one-line spark it drives an epic through design,
definition, breakdown, and per-story execution, pausing at gates for a human decision. This skill
is the operator's map of the `woof` shell CLI. You run `woof` commands and surface what the graph
decides; the graph owns the control flow.

`woof` is a shell command (the Python engine). Every command below is a `Bash(woof:*)` call. The
one interactive part - leading the design conversation - is a separate specialist, the
`woof-brainstorm` skill (`/woof:brainstorm`), which this skill routes to.

## Guardrails

- Never hand-edit anything under `.woof/`. Every state change goes through a `woof` verb; the JSONL
  audit log and gates depend on it.
- The graph picks the next node. You run `woof wf`; you do not choose stages. If `woof wf` reports
  `incomplete_stage_state`, fix the named artefact or open a gate - do not patch state by hand.
- Surface gates; never auto-approve. When the graph opens a gate, show it to the operator and
  resolve it only with their explicit decision (`woof wf --epic N --resolve <decision>`).
- Destructive verbs confirm first. `woof wf reset` deletes derived state; run it only on an explicit
  operator request and let it prompt (or pass `--yes` only when the operator already confirmed).
- Commit through `Bash(git:*)` when the operator asks; Woof's own commit-transaction check guards
  what a story lands.

## The flow (spark to stories)

1. Create an epic: `woof wf new "<spark>"`. The `github` tracker opens an issue and records it; the
   `local` tracker creates the epic on disk. Both write `spark.md` and set `.woof/.current-epic`.
2. Design (interactive): hand off to `/woof:brainstorm`. It runs the two design loops and writes the
   resolved bundle into the epic's `discovery/brainstorm/` bucket. This is the only interactive
   stage; route to it rather than driving design from here.
3. Run the graph: `woof wf --epic N`. With a brainstorm bundle present the graph skips the headless
   research/thinking/ideate chain, runs synthesis, then definition, breakdown, and per-story
   execution. Re-run `woof wf --epic N` to advance after each pause.
4. Resolve gates: when a gate opens, surface it (`woof observe --epic N --view gate`) and resolve
   with the operator's decision (`woof wf --epic N --resolve <decision>`).
5. Redo: `woof wf reset --epic N` returns an epic to its spark for a fresh design pass.

## Command map

### Epics and the graph - `woof wf`

```bash
woof wf new "<spark>"                  # create a tracker-backed epic (+ spark.md, .current-epic)
woof wf --epic N                       # run the graph forward (initialises from the tracker if cold)
woof wf --epic N --once                # run a single node and stop
woof wf --epic N --resolve <decision>  # resolve the currently open gate
woof wf reset --epic N [--yes]         # DESTRUCTIVE: reset the epic to its spark
woof wf ... --format json              # machine-readable output
```

`woof wf reset` deletes every derived artefact (discovery, EPIC.md, the plan, critiques, gate and
result files) and keeps only `spark.md`, the tracker linkage, and the epic log. It powers the
Start-fresh path in `/woof:brainstorm`.

### Design phase - route out

For the design conversation, invoke `/woof:brainstorm` (the `woof-brainstorm` skill). It owns the
two loops and writes the bundle into `discovery/brainstorm/`; come back here to run `woof wf`.

### Inspect (read-only)

```bash
woof observe --epic N [--view status|timeline|gate|audit|all] [--format json]
woof check stage-5 --epic N --story S1     # run the Stage-5 boundary checks for a story
woof check-cd <path/to/EPIC.md>            # verify each contract decision's referenced artefact
woof validate <path>... [--schema NAME]    # validate an artefact against a woof JSON Schema
```

### Onboard / maintain a consumer repo

```bash
woof init [--tracker github|local]   # scaffold .woof/ config + .gitignore
woof preflight                       # check local prerequisites
woof hooks install                   # install the post-commit cartography hook
woof render-epic --epic N [--sync]   # render EPIC.md front-matter into the tracker body
woof audit-bundle E<N>               # copy referenced Claude transcripts into the epic audit folder
```

### Gate decisions (for `woof wf --epic N --resolve`)

- plan gate: `approve` | `revise_epic_contract` | `revise_plan` | `abandon_epic`
- story / review gate: `approve` | `revise_story_scope` | `split_story` | `revise_plan` |
  `abandon_story` | `abandon_epic`
- tracker sync conflict: `keep_local` | `accept_remote` | `hand_merge`

Current limitation: the accepted gate verb set is wider than the implemented effect set. Until E17 lands, inspect `gate.md` and prefer the smallest known-progress resolution; do not assume `revise_epic_contract`, `split_story`, or `abandon_epic` performs the future documented behaviour.

## Specific flows

- Onboarding a repo to Woof: [references/setup.md](references/setup.md)
- Mapping the codebase (cartography): [references/map-codebase.md](references/map-codebase.md)
- Running and resuming an epic: [references/run.md](references/run.md)
- Surfacing and resolving gates: [references/gates.md](references/gates.md)

## Lineage

This single-umbrella operator model refines ADR-005 (which split the surface into peer skills). See
`docs/adr/007-operator-skill-umbrella.md`.
