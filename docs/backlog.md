# Woof Backlog

> **Purpose:** Current implementation backlog and roadmap for Woof.
> **Authority:** Woof repo.
> **Companion docs:** `docs/architecture.md` defines the operating contract; `docs/adr/001-orchestration-topology.md` defines the graph topology; `docs/research.md` records the evidence base.

## Baseline

Woof has an implemented ADR-001 Stage-5 execution path. `woof wf --epic <N>` runs the deterministic Python graph for story selection, executor dispatch, critique dispatch, verification, gate opening, structured gate resolution, and commit transaction verification.

Foundational surfaces:

- CLI wrapper and commands: `wf`, `validate`, `dispatch`, `render-epic`, `check-cd`, `check stage-5`, `gate write`.
- Python graph runtime: typed node contracts, transition table, transaction manifest generation, and manifest/index verification.
- Schemas for plans, gates, critiques, JSONL events, node I/O, executor results, check results, transaction manifests, language registry, quality gates, docs paths, agents, prerequisites, and test markers.
- Language registry files for Python, TypeScript, Rust, and Go.
- Stage-5 Check 6 real runner (`check_6_critique_blocker`).
- Dogfood evidence under `examples/dogfood/`.

## Roadmap

### 1. Woof Core Completion

- Tighten `woof wf` around all node transitions: idempotence, crash recovery, transient-file cleanup, gate re-entry, and JSON output stability.
- Represent every incomplete state as an explicit graph state or gate. Missing required artefacts must produce structured failures rather than permissive skips.
- Keep graph-owned orchestration as the only execution model. Prompt files remain producer-node prompts or thin wrappers; they do not choose successors, open commits, or dispatch critique.
- Expand unit coverage around graph transitions, transaction manifests, gate resolution, and audit-log reconstruction.
- Keep CLI help and README aligned with the actual operator surface.

### 2. Stage-5 Check Runners

Check 6 is implemented. Checks 1-5 and 7-9 still need production runners:

| Check | Work remaining |
|---|---|
| 1 `check_1_quality_gates` | Read `.woof/quality-gates.toml`, run configured commands with timeouts, capture command output, and return structured findings. |
| 2 `check_2_outcome_markers` | Resolve story `satisfies[]`, inspect staged test diff using `.woof/test-markers.toml`, and require each outcome marker. |
| 3 `check_3_scope` | Compare staged paths with `story.paths[]` plus allowed durable `.woof/` paths using git pathspec semantics. |
| 4 `check_4_contract_refs` | Verify owned contract refs via native tooling: OpenAPI/Schemathesis, Pydantic import+resolution, JSON Schema/ajv. Preserve the E146 invariant. |
| 5 `check_5_plan_crossrefs` | Validate plan schema plus cross-artefact invariants: outcome refs, contract-decision refs, CD ownership, dependency closure, acyclicity, and status coherence. |
| 7 `check_7_commit_transaction` | Assert commit-readiness: staged diff present unless gated as empty, required durable `.woof` files staged, no unstaged/foreign paths. |
| 8 `check_8_docs_drift` | Honour optional `.woof/docs-paths.toml`; code-path changes require mapped docs-path changes in the same transaction. |
| 9 `check_9_review_valve` | Open periodic/end-of-epic review gates summarising accumulated minor critique findings. |

The E146 contract-fidelity fixture remains the highest-value regression target for Check 4.

### 3. Stage 1-4 Graph Migration

- Promote Discovery, Definition, Breakdown, and Plan Gate into the same Python graph topology used by Stage 5.
- Define typed node input/output schemas for discovery synthesis, epic definition, planning, plan critique, mandatory plan gate opening, and plan-gate resolution.
- Move Stage-3 plan generation from design prose into a producer node with deterministic schema validation and cross-reference checks around it.
- Ensure Stage 4 always opens for human review after a valid plan and critique exist.
- Preserve the architectural seam: Discovery locks direction; Definition locks user-visible surface.

### 4. Dogfood Operating Model

- Keep dogfood artefacts under `examples/dogfood/` as curated evidence, not as runtime state.
- Record each dogfood epic's contract, plan, critiques, dispatch/audit summaries, gates, and lessons where they demonstrate a Woof behaviour or failure mode.
- Use dogfood to harden graph behaviour. Every observed skip or manual workaround must become either a graph transition, a checker, or a documented operating constraint.
- Maintain interview-review polish: dogfood examples should be readable without private GTS context and should make the safety model visible.

### 5. GTS-As-Consumer Integration

- Treat `guitar-tone-shootout` as a consumer checkout with `.woof/` runtime state and consumer config.
- Do not vendor-copy Woof back into GTS. GTS should depend on the Woof CLI/source checkout in the same way other consumers will.
- Consumer responsibilities: declare `.woof/prerequisites.toml`, optional `.woof/agents.toml`, `.woof/test-markers.toml`, `.woof/quality-gates.toml`, `.woof/docs-paths.toml`, and project-specific hooks.
- Woof responsibilities: provide graph execution, schemas, validation, dispatch, gates, check runners, language registry, and documentation.
- Keep consumer-specific policies outside Woof unless they generalise into configurable checks.

### 6. GitHub Issue Sync

- Finish the GitHub issue lifecycle around `EPIC.md` structured front-matter: cold-start pull, new-epic creation, Definition close push, plan summary update, completion close.
- Preserve free-form issue prose above the first managed heading and rewrite the managed structured sections deterministically.
- Implement conflict detection using `updatedAt` and body hash in `.last-sync`, opening a gate with a three-way diff on divergence.
- Fail loud on missing `gh` auth, repo access, network failures, or rate limits. No offline fallback.
- Keep `E<N>` equal to GitHub issue `#<N>`.

### 7. Preflight, Environment, Hooks, Tooling

- Implement preflight as a first-class CLI path, not a GTS shell convention.
- Validate required wrappers, GitHub access, language tools, LSP plugins, Tree-sitter grammar parsing, quality-gate commands, and consumer config schemas.
- Cache preflight results by prerequisite hash while keeping short-lived runtime checks for network/auth.
- Install git hooks idempotently through project tooling; preserve user-managed hook content.
- Keep redaction and size caps on audit files before commit; raw oversized output must remain gitignored.
- Keep `just check` as Woof's local quality gate and ensure it remains the required pre-push quality signal.

### 8. Documentation And Evidence Polish

- Keep README as the entry map and avoid duplicating architecture detail there.
- Keep `docs/architecture.md` focused on the design contract and current implementation boundary.
- Keep `docs/research.md` as stable evidence and lineage, not a live backlog.
- Keep this backlog as the live roadmap.
- Keep ADR-001 as the accepted topology decision; create new ADRs for decisions that change the graph contract.
- Add concise examples that demonstrate: graph-owned orchestration, second-LLM critique enforcement, manifest-verified commits, gate resolution, and E146 contract fidelity.
