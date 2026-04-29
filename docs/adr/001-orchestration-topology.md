# ADR-001: Orchestration topology — Python graph with LLM and human nodes

## Status

Proposed. Drives the next implementation cycle (the first epic in this repo).

## Context

The existing implementation made the LLM the orchestrator: a skill body listing steps, dispatched as a `cld -p` or `codex exec` subprocess, with the LLM deciding which steps happen, dispatching its own subprocesses (e.g. codex for critique), and committing its own output. This produced two real failures during the E182 dogfood (visible in this repo's git history at commits `7bf2a12` and `b729860`):

1. **Skipped second-LLM review.** The story executor's skill body listed a codex critique step. The executor judged it unnecessary and skipped it. The work committed without the second-LLM coverage that the architecture's safety model requires. Nothing in the pipeline detected the omission.
2. **Incomplete commit transaction.** The new executor contract said to stage `.woof/epics/E<N>/audit/` as part of the commit transaction. The executor staged the code paths and forgot the audit files. No exception fired; the commit landed incomplete.

Both failures share a structure: **an LLM with agency over orchestration silently skips steps, and no deterministic check fires until the omission causes a downstream failure.** The same class of bug had already grounded epic E181 a few days earlier (same repo, same root cause, different surface).

This is not a bug in any one skill body's prose. It is a property of *who runs the graph*. When the LLM owns the graph, the LLM can rewrite it.

The pre-woof workflow system (deleted on 2026-04-05; see GTS history) had the inverse topology: a Python orchestrator drove the graph, calling the LLM at specifically defined nodes for inference. That older system was retired for unrelated reasons (planning model immaturity). The orchestration topology, in retrospect, was right.

## Decision

**The orchestrator is a deterministic Python graph. LLM inference and human review are typed nodes within it. Every node and every edge is defined explicitly; no participant — neither LLM nor human — selects successors at runtime.**

Concretely:

1. **The graph is owned by Python code.** Stages, transitions, and gate conditions are encoded as a state machine in the woof codebase, not as prose in skill bodies.

2. **Nodes are typed.** Each node has a fixed type — `executor_dispatch`, `critique_dispatch`, `verification`, `commit`, `gate_open`, `gate_resolve`, `human_review` — with a Pydantic-defined input contract, output contract, and successor edges. Adding a new node type is a schema + graph edit, not a prose edit.

3. **LLM nodes are pure producers.** An LLM node receives a structured input (validated), runs inference, and produces a structured output (validated against an output schema). It never invokes another node, never dispatches a subprocess, never selects which step happens next, never writes outside its declared output.

4. **Human gates are first-class Python nodes.** A `gate_open` node halts the graph until a `gate_resolve` event arrives carrying a structured decision (drawn from a fixed enum: `approve`, `revise_epic_contract`, `revise_plan`, `revise_story_scope`, `split_story`, `abandon_story`, `abandon_epic`). The graph reads the decision; it does not interpret prose.

5. **Single entry point.** The graph is the only operator surface. There is no manual `woof dispatch` for humans, no "just run this command directly", no orchestrator-side direct git commit during an active stage. When something needs human intervention, the answer is: open a gate, resolve it via the structured decision, re-enter the graph.

6. **Deterministic transaction manifests.** For every commit-producing transition (e.g. story commit), the graph computes the expected file manifest from the story spec + a fixed boilerplate (critique file, audit files, plan.json, epic.jsonl, dispatch.jsonl). The pre-commit verifier asserts the staged file set equals the manifest exactly. Deviations open a gate.

## Consequences

**Positive:**

- Determinism. Same epic-state input → same node sequence → same file manifest. Two consecutive runs of the same epic from the same state produce byte-identical artefact sets.
- Drift detection at CI. Static-string assertions over skill prose vs. registry, schema validation of every node's output, and registry self-tests catch divergence before production.
- Replayability. The full graph trace is in `epic.jsonl`; given the same state and the same seeds, runs are reproducible.
- Smaller, sharper skills. Skill bodies shrink to "given input X, produce output Y". No "first do A, then if B then C". No multi-step orchestration prose.
- Clearer extension surface. Adding a stage, a check, or a node type is a graph + schema edit. No skill-prose archaeology.

**Negative:**

- Implementation cost. The current codebase needs restructuring. The driver script (`scripts/wf-run` in the GTS-side checkout that produced this repo) becomes a graph node interpreter; the skill bodies (`.claude/commands/wf*.md`) shrink dramatically; the registry gains node-type entries beyond the existing check entries.
- Some operations that were previously a single skill invocation become multi-node graph traversals. Worth it.
- Orchestrator skills (the current `wf` family) lose most of their reasoning content. They become thin wrappers around node invocations — or get removed entirely in favour of `just wf` driving the graph directly.

## Alternatives considered

- **Tighten skill bodies + rely on cross-LLM second opinion.** Rejected: skill prose drifts from canon. The LLM that was supposed to enforce the rule can rewrite it. E182 was filed precisely because this approach failed in E181.
- **Make the executor's codex dispatch deterministic via post-hoc validation.** Rejected: still puts orchestration in the LLM. Producer/verifier collapse remains; the LLM still chooses whether to dispatch.
- **Audit-only enforcement (let the LLM orchestrate, retroactively gate on missing artefacts).** Rejected: detects failure after work is wasted. Architecture must be incomplete-by-construction, not validate-after-the-fact.

## Notes on transition

The current woof source code embeds the LLM-orchestrator topology in:

- `bin/woof` — provides the right primitives (`dispatch`, `validate`, `check`, `gate write`); these become node-implementation building blocks. Mostly preserved.
- `cli/commands/check.py` — Stage-5 verifier; becomes a `verification` node implementation. Bootstrap-tolerant placeholder behaviour (commit `b729860`) stays as-is until all runners are real.
- `cli/commands/gate.py` + `gate/write.py` — gate writer; becomes the `gate_open` node implementation. Stays.
- `checks/registry.py` — Stage-5 check registry; becomes the configuration data the `verification` node reads. Stays.
- `playbooks/` — LLM prompt templates; become the input-data fixture for LLM nodes. Stays.
- `.claude/commands/wf*.md` — orchestrator skills; **shrink dramatically**. The graph runs in Python; skills (if retained at all) become thin per-node prompts.
- `scripts/wf-run` (lives in the consuming repo, GTS) — **deleted**. Its responsibilities migrate into the Python graph implementation here.
