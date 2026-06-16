# Woof Context

The glossary for Woof's design docs - what the words in `architecture.md`, the ADRs, and the
backlog mean. New or changed terms are promoted here at epic close-out (see `architecture.md`
section 15, Change control).

## Glossary

- **Cartography** - the artefact group at `.woof/codebase/`: durable design docs, mapper-authored AS-IS docs, and mechanical files.
- **Mechanical layer** - `tags`, `files.txt`, `freshness.json`, and planned generated indexes such as `.woof/codebase/structural/`; cheap, post-commit refreshed, and gitignored.
- **Structural cartography index** - ADR-009's generated files/symbols/edges SQLite artefact under `.woof/codebase/structural/`.
- **Structural impact context** - token-bounded callers/callees/dependencies output from `woof cartography impact`, used first by the Stage-5 reviewer.
- **Producer** - the LLM-dispatched node that creates an artefact for a graph stage.
- **Reviewer** - the LLM-dispatched node that critiques the producer's output in an isolated context.
- **Mapper subagent** - a Claude subagent launched by the `/woof` map-codebase flow to author one or two cartography docs.
- **Readiness gate** - deterministic halt after Stage 2 when `EPIC.md` is not concrete enough for planning.
- **Strict quality gate** - quality gate mode where any failure blocks.
- **Baseline quality gate** - quality gate mode where a pre-existing red command is recorded and only deterioration blocks.
- **Blocker evidence** - machine-resolvable evidence attached to a blocker finding, such as file:line, story id, outcome id, contract-decision id, schema ref, or gate id.
- **HEAD/branch drift** - unexpected git position movement during dispatch or commit that is not explained by a graph-owned commit.
- **Conformance audit** - deterministic diff-scoped audit that checks implemented production changes against `EPIC.md`, plan contracts, and consumer invariants.
- **Run lineage** - a single `run_id` carried by every epic/dispatch event so one epic execution is reconstructable as a single end-to-end trace and replayable from disk.
- **Completed-but-lingering** - a dispatched worker that emitted its terminal result but whose process has not exited because a spawned child holds the stdout pipe open. Classified as completed, not as a timeout.
- **Resume-to-correct** - on a recoverable producer-output failure, resuming the producer's captured model session with the deterministic failure evidence as feedback, instead of a cold re-dispatch.
- **Graded recovery ladder** - bounded sequence applied before a gate-open: deterministic salvage, normalisation with safe defaults, bounded retry (compacted payload or resume-to-correct), then gate.
