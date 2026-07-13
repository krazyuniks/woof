# Surfacing and resolving gates

A gate is the graph's pause for a human decision. When the graph opens one it stops and writes
`gate.md` into the epic's state directory in the operator home
(`~/.woof/state/projects/<project-key>/epics/E<N>/gate.md`); `woof wf --epic N` then reports
`HUMAN_REVIEW` until it is resolved.

## Surface it

```bash
woof observe --epic N --view gate
```

Show the operator what the gate is asking - the gate type, the work unit (if any), what triggered it,
and the relevant artefacts (the plan, the critique, the check result). Do not summarise away the
detail they need to decide.

## Resolve it

Resolve only with the operator's explicit decision. Never auto-approve.

```bash
woof wf --epic N --resolve <decision>
```

The valid decisions depend on the gate type:

- readiness gate: `approve_with_reason` | `revise_epic_contract` | `abandon_epic`
- plan gate: `approve` | `revise_plan` | `revise_epic_contract` | `abandon_epic`
- work-unit gate / review gate: `approve` | `retry_work_unit` | `revise_work_unit_scope` |
  `revise_plan` | `abandon_work_unit` | `abandon_epic`
- tracker sync conflict: `keep_local` | `accept_remote` | `hand_merge`

The valid set per gate type is the canonical table in `src/woof/graph/decisions.py`. Every accepted
verb has an implemented effect that moves the graph, and a conformance test fails if any surface
(this list included) drifts from the table.

What each does:

- `approve`: accept the work and let the graph continue.
- `approve_with_reason`: record an audited readiness approval so an unready-but-accepted contract advances to planning without re-gating. The audited record is the approval decision itself; no separate operator reason is captured (the `--note` channel is deferred). Readiness gate only.
- `retry_work_unit`: reset a crashed or aborted work unit to `pending` and clear its executor, check, and critique artefacts so the graph re-dispatches it cleanly without redoing its siblings. Work-unit / review gate only.
- `revise_epic_contract`: archive the prior `EPIC.md` to `definition/EPIC.<n>.archived.md`, snapshot the gate findings, and re-enter Stage 2 Definition with the prior epic plus findings as inputs. Hand-editing the contract stays forbidden.
- `revise_plan`: discard the plan and re-run Breakdown.
- `revise_work_unit_scope`: re-scope the current work unit's paths or acceptance criteria. Split guidance re-enters planning through `revise_plan`.
- `abandon_work_unit`: mark the work unit `abandoned` - a terminal status distinct from `done` - and skip it; the epic still completes on its remaining work units.
- `abandon_epic`: mark the epic abandoned, close the tracker issue as not delivered, and make the epic terminal (distinct from a completed epic).
- `keep_local` / `accept_remote` / `hand_merge`: resolve a divergence between the local epic and the
  tracker issue (GitHub tracker only).

Resolving a gate records the decision in the epic log, removes the derived artefacts the decision
invalidates, and unlinks `gate.md`. Then run `woof wf --epic N` again to advance.

## After resolution

`woof wf --epic N` recomputes the next node from the post-resolution state. A revise decision sends
the graph back to the corresponding stage; an approve lets it move on.
