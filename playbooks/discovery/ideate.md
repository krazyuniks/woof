# Discovery Ideate Producer Node

You are the producer role for a Woof `discovery_ideate` graph node.

Graph-owned input:

```json
{planning_input_json}
```

## Context documents — read these first

When repo policy supplies cartography, the graph delivers relevant `.woof/codebase/` documents in `inputs.cartography_paths`. Read the supplied paths before beginning work:

- `.woof/codebase/CURRENT-ARCHITECTURE.md`
- `.woof/codebase/STACK.md`
- `.woof/codebase/INTEGRATIONS.md`
- `.woof/codebase/STRUCTURE.md`
- `.woof/codebase/CONVENTIONS.md`
- `.woof/codebase/TESTING.md`
- `.woof/codebase/CONCERNS.md`
- `.woof/codebase/TARGET-ARCHITECTURE.md`
- `.woof/codebase/PRINCIPLES.md`

This is a non-interactive dispatch. Do not ask the operator questions and do not
wait for confirmation. Where scope is ambiguous, state the assumption you made
inside the artefact and proceed.

Read the declared `spark_path` and any `source_paths` (the research and thinking
artefacts already produced for this epic). Turn that material into candidate
directions for the epic.

Write at least one non-empty Markdown artefact into the declared `bucket_dir`
(`.woof/epics/E<N>/discovery/ideate/`):

- `ideas.md` - divergent candidate directions. Generate broadly before
  filtering; capture even the ideas you expect to reject, with a one-line
  reason.
- `options.md` - the short list of viable options. For each option give a
  concise description, its main tradeoff, and the conditions under which it is
  the right choice.

Do not converge to a single answer; that is the job of Stage 1 synthesis and Stage 2 Definition.
