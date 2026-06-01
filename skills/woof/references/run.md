# Running and resuming an epic

Drive one epic forward through the deterministic graph. This is the former `/woof:run` flow, now a
reference under the `/woof` umbrella. The graph is deterministic: `woof wf` reads the epic's
filesystem state and runs the next node. You re-run it to advance; you never pick the stage.

## Create or cold-start

```bash
woof wf new "<spark>"     # new epic: opens the tracker issue, writes spark.md, sets .current-epic
woof wf --epic N          # existing epic: initialises from the tracker if the local dir is absent
```

## Lead the design (interactive)

Before running the headless graph, hand off to `/woof:brainstorm` to run the two design loops. It
writes the resolved bundle into `.woof/epics/E<N>/discovery/brainstorm/`. With that bucket present,
the graph skips the headless research/thinking/ideate chain.

If no human leads the design, skip this step: `woof wf --epic N` runs the headless discovery chain
as the autonomy fallback.

## Drive forward

```bash
woof wf --epic N           # run nodes until the graph pauses (a gate, or epic complete)
woof wf --epic N --once    # run a single node and stop (step through)
```

Each call runs the graph from current state: synthesis, then Definition (locks the surface),
Breakdown (the story plan), and per-story execution (dispatch, critique, disposition, verification,
commit). Re-run `woof wf --epic N` after each pause to continue.

The graph dispatches producer and reviewer work to model CLIs via the roles in `.woof/agents.toml`.
Surface progress from the command output; inspect deeper with `woof observe` (see below).

## Resume after an interruption

`woof wf --epic N` is resumable: it recomputes the next node from filesystem state, including a
half-finished commit. If it reports `incomplete_stage_state`, read the named artefact - do not edit
`.woof/` by hand. Either complete the step it expects or open/resolve a gate.

## Inspect

```bash
woof observe --epic N --view status     # current stage and story states
woof observe --epic N --view timeline    # the full epic.jsonl history
woof observe --epic N --view gate        # the open gate, if any
woof observe --epic N --view all
```

## Redo the design

```bash
woof wf reset --epic N      # DESTRUCTIVE: back to spark; confirm at the prompt
```

Then re-run `/woof:brainstorm` (or `woof wf --epic N` for the headless path). This is what the
Start-fresh choice in `/woof:brainstorm` runs.

## Gates

When the graph opens a gate it stops and writes `gate.md`. See `gates.md` for surfacing and
resolving it.
