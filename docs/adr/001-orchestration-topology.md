# ADR-001: Orchestration Topology

## Status

Accepted. Implemented by the `woof wf --epic <N>` graph path.

## Context

Woof's workflow needs LLM judgement for generation and critique, but successor
selection, gate creation, verification, transaction manifests, and commits must
be deterministic.

If an LLM owns the workflow graph, it can skip or reorder safety steps while
still producing plausible prose. Woof therefore separates orchestration from
inference: Python owns the graph, LLMs own typed producer or reviewer artefacts,
and humans own explicit gate decisions.

## Decision

The orchestrator is a deterministic Python graph. LLM inference and human review
are typed nodes within it. Every node and edge is explicit; no participant
selects successors at runtime.

Concrete rules:

1. The graph is owned by Python code. Stages, transitions, and gate conditions
   are encoded in Woof source, not in prompt prose.
2. Nodes are typed. Each node has a fixed type, schema-governed input/output,
   and explicit successor rules.
3. LLM nodes are pure producers or reviewers. They receive structured input,
   write declared output artefacts, and do not dispatch subprocesses, choose
   successors, write gates, or commit.
4. Human gates are graph states. The graph halts on `gate.md` until
   `woof wf --epic <N> --resolve <decision>` records a structured decision.
5. `woof wf` is the workflow entry point. Operator intervention is represented
   as gate resolution and graph re-entry.
6. Commit-producing transitions use graph-owned transaction manifests. The
   staged file set must match the expected manifest exactly.

## Consequences

- The same filesystem state produces the same successor node sequence.
- Graph state is replayable from `.woof/epics/E<N>/` artefacts and JSONL audit
  streams.
- Prompt templates stay local to producer/reviewer work and cannot become
  orchestration authority.
- Adding a stage, check, or node type is a source, schema, test, and docs
  change.
- Missing, malformed, or unsafe state opens a gate or fails loud instead of
  being silently repaired by a prompt.

## Implementation

- `src/woof/graph/` owns graph state, transitions, node handlers, locking,
  crash-resume, and transaction manifest verification.
- `src/woof/cli/commands/wf.py` is the operator entry point for graph execution,
  epic creation, and structured gate resolution.
- `schemas/node-input.schema.json`, `schemas/node-output.schema.json`,
  `schemas/planning-node-input.schema.json`,
  `schemas/planning-node-output.schema.json`, and
  `schemas/transaction-manifest.schema.json` define graph contracts.
- `playbooks/` holds model-facing producer and reviewer prompts. These prompts
  do not own workflow transitions.
