# Surfacing and resolving gates

A gate is the graph's pause for a human decision. When the graph opens one it stops and writes
`.woof/epics/E<N>/gate.md`; `woof wf --epic N` then reports `HUMAN_REVIEW` until it is resolved.

## Surface it

```bash
woof observe --epic N --view gate
```

Show the operator what the gate is asking - the gate type, the story (if any), what triggered it,
and the relevant artefacts (the plan, the critique, the check result). Do not summarise away the
detail they need to decide.

## Resolve it

Resolve only with the operator's explicit decision. Never auto-approve.

```bash
woof wf --epic N --resolve <decision>
```

The valid decisions depend on the gate type:

- plan gate: `approve` | `revise_epic_contract` | `revise_plan` | `abandon_epic`
- story gate / review gate: `approve` | `revise_story_scope` | `split_story` | `revise_plan` |
  `abandon_story` | `abandon_epic`
- tracker sync conflict: `keep_local` | `accept_remote` | `hand_merge`

Current limitation: the CLI accepts several verbs whose effects are incomplete. E17 owns the canonical decision table and conformance tests. Until then, treat `revise_epic_contract`, `split_story`, and `abandon_epic` as hazardous unless you have checked the current implementation path.

What each does:

- `approve`: accept the work and let the graph continue.
- `revise_epic_contract`: target behaviour is to re-open Stage 2 Definition; current behaviour does not provide a real revision channel.
- `revise_plan`: discard the plan and re-run Breakdown.
- `revise_story_scope` / `split_story`: target behaviour is to re-scope or split the story; current `split_story` duplicates the re-scope path.
- `abandon_story` / `abandon_epic`: target behaviour is to stop the story or the whole epic; current `abandon_story` records the story as completed, and current `abandon_epic` is not a terminal path.
- `keep_local` / `accept_remote` / `hand_merge`: resolve a divergence between the local epic and the
  tracker issue (GitHub tracker only).

Resolving a gate records the decision in the epic log, removes the derived artefacts the decision
invalidates, and unlinks `gate.md`. Then run `woof wf --epic N` again to advance.

## After resolution

`woof wf --epic N` recomputes the next node from the post-resolution state. A revise decision sends
the graph back to the corresponding stage; an approve lets it move on.
