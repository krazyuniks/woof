# Woof — Research & Evidence Base

> **Purpose:** Stable reference for Woof's design decisions. Updates when new evidence arrives, not during design iteration.
> **Active architecture doc:** `docs/architecture.md`.
> **Naming note:** "GTS" in this document refers to the predecessor workflow pipeline, not the current Guitar Tone Shootout consumer application.

---

## 1. Framework Evaluation (2026-03-26)

Four AI workflow frameworks compared across 15 capability areas. Local clones preserved at `~/Work/superpowers/`, `~/Work/taches-cc-resources/`, `~/Work/get-shit-done/`.

| Framework | Architecture |
|---|---|
| **Superpowers** (obra, v5.0.6) | 16 prompt-only skills, zero runtime code. |
| **taches-cc-resources** | Router-pattern skills + slash commands + meta-prompting system. |
| **GSD** (get-shit-done) | 44 commands, 18 agents, 60+ CLI subcommands. File-based state. |
| **GTS** (pre-deletion) | Python orchestrator, 13 shell hooks, JSONL state, multi-provider dispatch. |

### Capability Comparison — Summary

| # | Capability | Best | Runner-up |
|---|---|---|---|
| 1 | Task Breakdown | GSD (wave parallelism, granularity config) | Superpowers (2-5 min tasks) |
| 2 | Brainstorming | GSD (decision categorisation) + taches (thinking models) | Superpowers (hard gate) |
| 3 | Research | taches (8 specialised types) | GSD (parallel researchers) |
| 4 | Testing | **GTS** (no-mock enforcement, hook-enforced) | Superpowers (iron law TDD) |
| 5 | Quality Gates | **GTS** (deterministic + adversarial) | GSD (3-level verification) |
| 6 | Code Review | Superpowers (two-stage) | GSD (cross-AI review) |
| 7 | Debugging | Superpowers (3-fix escalation) | GSD (persistent state) |
| 8 | Conflict Resolution | GSD (CONTEXT.md fidelity) | **GTS** (failure classification) |
| 9 | Session Management | GSD (structured pause/resume) | **GTS** (crash-resume) |
| 10 | Context Management | GSD (thin orchestrator, visual bar) | taches (4-threshold) |
| 11 | Commits/PRs | **GTS** (test-before-commit, auto-teardown) | GSD (PR branch filtering) |
| 12 | Architecture Decisions | GSD (locked/deferred/discretion) | Superpowers (YAGNI) |
| 13 | Agent Orchestration | GSD (16 agents, wave parallelism) | Superpowers (status protocol) |
| 14 | Enforcement | **GTS** (13 hard-gate hooks) | Superpowers (iron law prompts) |
| 15 | Extensibility | taches (5 meta-skills) | Superpowers (TDD for skills) |

### GTS Strengths to Keep (7)

1. **Hard enforcement hooks** — 13 hooks that actually block bad behaviour
2. **Deterministic validation** — Phase A 12 structural checks (referential integrity, truth coverage, journey coverage, scope coherence, dependency ordering, etc.)
3. **Cross-AI critique** — multi-provider dispatch (Claude + Codex) with role-based agent assignments
4. **Worktree isolation** — full Docker/port/auth/env isolation per worktree
5. **JSONL crash-resume** — pipeline survives interruption
6. **Real-service testing** — no mocks, hook-enforced
7. **Auto-teardown on merge** — closes issue, tears down worktree

### GTS Gaps to Fill (6, priority-ordered)

1. **Brainstorming** — adopt thinking models (taches), decision categorisation (GSD), hard gate (Superpowers)
2. **Context management** — adopt token threshold monitoring (taches/GSD), thin orchestrator budget (GSD)
3. **Debugging** — adopt systematic debugging with 3-fix escalation (Superpowers), persistent debug state (GSD)
4. **Research** — adopt specialised research types (taches), parallel researchers (GSD)
5. **Extensibility** — taches installed ephemerally at `~/Work/taches-cc-resources/`; don't reinvent
6. **Architecture decisions** — adopt locked/deferred/discretion (GSD)

---

## 2. E146 Contract Fidelity Failure

### What happened

Epic #146 (prompt-hardening) exposed a **planner–verifier deadlock**:

1. Curation surfaced epic-vs-repo contract mismatch correctly
2. Planner built the plan around **repo convention** (`PATCH /api/shootouts/{shootout_id}/comments/{comment_id}` with `content`)
3. Phase A passed — structurally valid
4. Phase B rejected — epic contract (`PATCH /api/v1/comments/<id>` with `body`) was substituted
5. Planner revision explained the substitution but did not change it
6. Verifier rejected again — loop with no human escape

Root cause: no invariant that **the epic contract is law**. The planner had implicit permission to substitute repo convention. Phase A was structurally valid but not contract-fidelity-checked.

### Contract-Fidelity Requirements

1. **Schema** — add `ContractDecision` as first-class typed data on the plan. Fields: decision ID, epic contract, repo convention, canonical resolution (`epic` | `bridge` — *no `repo`* option), bridge description, affected stories.
2. **Curation** — surfaces mismatches neutrally, does not recommend winners
3. **Planner prompt** — hard rule: implement the epic contract; optional bridge for repo convention; never drop the epic surface
4. **Phase A** — deterministic contract-fidelity check: extract route surfaces from `EPIC.md`, verify each appears in observable truths / journeys / acceptance criteria / contract decisions
5. **Verifier** — reviews each recorded contract decision explicitly
6. **PLAN.md** — dedicated contract-decision table visible to the human gate

### Key invariant

> **Epic's observable outcomes define the canonical user-facing contract. Repo conventions may inform implementation shape, adapters, aliases, redirects, or bridge code — but they may never replace the epic contract.**

The current architecture encodes this invariant through `contract_decisions[]` and Stage-5 Check 4.

---

## 3. Lessons

### 3.1 The 2026-04-08 Critique Overcorrection

**What happened:** On 2026-04-08, the discovery design was stress-tested with four thinking-model analyses (via-negativa, first-principles, inversion, second-order). The inversion analysis produced the axiom:

> "The moment there's a Python module, an adapter interface, a registration system, a dispatch layer — you're rebuilding the pipeline that died. v1 is a `.md` skill file. No Python. No scripts. No adapter classes."

This was **wrong**. It conflated *"the specific old pipeline that deadlocked"* with *"Python infrastructure in general"*. The pipeline didn't die from being Python; it died from hard agent-to-agent agreement gates with auto-revision loops and no human conversation channel.

The axiom was baked into the revised `Discovery-Workflow-Design.md` (same day) and **silently dropped cross-AI critique** (Strength #3 from the 2026-03-26 evaluation). Rejected 2026-04-18.

**Rule derived:** *Always distinguish a specific failure mode from the general architectural choice it occurred within. "Python infrastructure" ≠ "the pipeline that deadlocked".*

### 3.2 Research the delta, not the whole topic

Training-data coverage is adequate for most mainstream technologies. Research should target *specific unknowns* (current API surfaces, compatibility matrices, pricing, breaking changes), not re-learn basics. "Here's what I know about X. The gap: Y. Research Y specifically."

### 3.3 Planner integration is the critical untested contract

Discovery output is only valuable if the planner *reads* and *respects* it. The contract between discovery and planning must be tested, not assumed. A plan produced after discovery must honour locked decisions unmodified.

### 3.4 Silent degradation is banned

If required infrastructure is missing, stop and report — don't produce inferior output with thinner evidence. Workarounds are banned.
