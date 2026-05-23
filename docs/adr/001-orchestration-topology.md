# ADR-001: Orchestration topology — Python graph with LLM and human nodes

## Status

Accepted and implemented for the Stage-5 execution path. `woof wf --epic <N>` is now the graph entry point; the remaining stages adopt the same topology as they are implemented.

Related: ADR-002 keeps this topology and defines semantic primary/reviewer role
routing. Provider choice changes, but orchestration authority remains in the
Python graph.

## Context

Woof's product goal is agentic multi-step software delivery. The workflow needs
LLM judgement for generation and critique, but successor selection, gate
creation, verification, transaction manifests, and commits must be
deterministic.

If an LLM owns the workflow graph, it can skip or reorder safety steps while
still producing plausible prose. The architecture therefore separates
orchestration from inference: Python owns the graph, LLMs own typed producer or
reviewer artefacts, and humans own explicit gate decisions.

## Decision

**The orchestrator is a deterministic Python graph. LLM inference and human review are typed nodes within it. Every node and every edge is defined explicitly; no participant — neither LLM nor human — selects successors at runtime.**

Concretely:

1. **The graph is owned by Python code.** Stages, transitions, and gate conditions are encoded as a state machine in the woof codebase, not as prose in skill bodies.

2. **Nodes are typed.** Each node has a fixed type drawn from the graph's `NodeType` enum, with JSON Schema-governed input and output contracts, Pydantic runtime models for Python serialisation, and explicit successor edges. Adding a new node type is a schema + graph edit, not a prose edit.

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

- Implementation cost. The graph runtime, transition table, node registry,
  prompt templates, and check registry must remain aligned.
- Some operations that were previously a single skill invocation become multi-node graph traversals. Worth it.
- Orchestrator skills (the current `wf` family) lose most of their reasoning content. They become thin wrappers around node invocations — or get removed entirely in favour of `just wf` driving the graph directly.

## Alternatives considered

- **Tighten skill bodies + rely on cross-LLM second opinion.** Rejected: skill prose drifts from canon. The LLM that was supposed to enforce the rule can rewrite it. E182 was filed precisely because this approach failed in E181.
- **Make the executor's codex dispatch deterministic via post-hoc validation.** Rejected: still puts orchestration in the LLM. Producer/verifier collapse remains; the LLM still chooses whether to dispatch.
- **Audit-only enforcement (let the LLM orchestrate, retroactively gate on missing artefacts).** Rejected: detects failure after work is wasted. Architecture must be incomplete-by-construction, not validate-after-the-fact.

## Notes on transition

Implementation landed in:

- `src/woof/graph/` — typed graph state, transition table, node registry, transaction manifest verification.
- `src/woof/cli/commands/wf.py` — single operator entry point for graph execution and structured gate resolution.
- `schemas/{node-input,node-output,transaction-manifest}.schema.json` — graph contracts.
- `.claude/commands/wf*.md` — reduced to wrappers / pure producer-node prompts.

The current woof source code embeds the LLM-orchestrator topology in:

- `bin/woof` — provides the right primitives (`dispatch`, `validate`, `check`, `gate write`); these become node-implementation building blocks. Mostly preserved.
- `cli/commands/check.py` — Stage-5 verifier; becomes a `verification` node implementation. All nine Stage-5 runners are now implemented.
- `cli/commands/gate.py` + `gate/write.py` — gate writer; becomes the `gate_open` node implementation. Stays.
- `checks/registry.py` — Stage-5 check registry; becomes the configuration data the `verification` node reads. Stays.
- `playbooks/` — LLM prompt templates; become the input-data fixture for LLM nodes. Stays.
- `.claude/commands/wf*.md` — orchestrator skills; **shrink dramatically**. The graph runs in Python; skills (if retained at all) become thin per-node prompts.
- External driver scripts — **deleted**. Their responsibilities live in the Python graph implementation here.
