# Woof Context

This glossary defines Woof's project-specific terms. Architecture, ADRs, and backlog entries refer here rather than redefining terms.

## Glossary

- **Intake** - the boundary that turns a source idea, issue, docs epic, or pre-decomposed backlog into executable `work_units[]` plus run metadata.
- **Epic** - the upstream design contract for a body of work. An epic may be sparse or enriched; when present, it decomposes into `work_units[]`.
- **Sparse epic** - an initial epic draft, issue body, or idea that may need enrichment before decomposition.
- **Enriched epic** - an epic with enough outcomes, decisions, acceptance criteria, and context to decompose without inventing intent.
- **Brainstorm enrichment** - the interactive design step that can turn a sparse epic into an enriched epic before decomposition.
- **Pre-decomposed intake** - a supplied `work_units[]` backlog that skips epic enrichment and decomposition because the executable work already exists.
- **Work unit** - the executable unit Woof produces, checks, reviews, fixes, publishes, and merges. Avoid: story.
- **Work unit ID** - the stable local identity of a work unit inside one work-unit aggregate. It is human-readable and unique within that aggregate.
- **Qualified work-unit reference** - the structured reference Woof uses outside an aggregate boundary: a discriminated aggregate context (an epic context of `project_ref` + `epic_id`, or a work-unit-set context of `project_ref` + `set_id` + optional `source_ref`) plus the local `work_unit_id`. `set_id` is a stable identity assigned once at intake, never a run UUID.
- **Work-unit aggregate** - the ordered executable collection that owns work-unit identity, dependency closure, dependency acyclicity, and dependency order.
- **Contract-trace fields** - optional work-unit fields that link executable work to epic outcomes, contract decisions, path scope, and test expectations.
- **Run metadata** - source, repo, policy, upstream issue links, run identity, and other non-executable facts needed for audit and orchestration.
- **Producer** - the LLM worker that creates or fixes an artefact for a work unit.
- **Reviewer** - the independent LLM worker that critiques the current diff and evidence.
- **Warm producer** - the producer session kept alive across bounded fix rounds for one work unit.
- **Fresh reviewer** - a reviewer context that is independent for each review round and receives the full current diff and evidence.
- **Attached execution resource** - a live transport session or agent used to perform work while disk remains the authority for what work should happen.
- **Harness backend** - the tmux or herdr transport declared by a harness profile. Project policy selects a harness; the registry resolves its backend.
- **Retained session** - a backend-neutral live worker identity that accepts subsequent turns before explicit close; Woof uses it for a warm producer.
- **Profile A** - worktree-per-work-unit delivery with a pull request and serial merge coordinator.
- **Profile B** - single-tree delivery with graph-owned commit and push.
- **Cartography** - the `.woof/codebase/` artefact group used for repo understanding: design docs, mapper-authored AS-IS docs, lexical files, and optional structural indexes.
- **Cartography floor** - the minimum cartography capability a repo policy requires before a run may proceed.
- **Mechanical layer** - regenerated cartography artefacts such as `tags`, `files.txt`, `freshness.json`, and structural indexes.
- **Structural cartography index** - generated files/symbols/edges data under `.woof/codebase/structural/` for impact-aware review and conformance checks.
- **Structural impact context** - token-bounded callers, callees, imports, and dependency evidence produced from structural cartography.
- **Check floor** - the deterministic checks a repo policy requires before review or publish.
- **Readiness gate** - deterministic halt when an epic or work-unit set is not concrete enough for the next step declared by policy.
- **Blocker evidence** - machine-resolvable evidence attached to a blocker finding, such as file:line, work-unit id, outcome id, contract-decision id, schema ref, or gate id.
- **Conformance audit** - deterministic diff-scoped audit that checks production changes against epic contracts, work-unit trace fields, policy, and cartography.
- **Run lineage** - a single run identity carried by events and artefacts so execution is reconstructable and replayable from disk.
- **Review instability** - conflicting reviewer verdicts over the same diff hash and prompt version.
- **Completed-but-lingering** - a worker that emitted its terminal result but whose process or child process still holds the stream open.
- **Resume-to-correct** - recovery that feeds deterministic failure evidence back to the warm producer instead of cold re-producing.
- **Graded recovery ladder** - bounded recovery before opening a gate: deterministic salvage, safe normalisation, bounded retry, then gate.
- **Worktree engine** - the project-composed, host-level tool that provisions, places, recovers, and tears down per-work-unit worktrees and runs the project's registered lifecycle commands. Woof discovers and validates worktrees but never provisions, mutates, recovers, or invokes the engine (ADR-015).
- **Profile A worktree contract** - the policy-declared worktree root and unit-to-path derivation that let Woof deterministically discover and fail-closed-validate provisioned worktrees without naming or invoking the per-run worktree provider.
- **Sibling conflict** - a shared-file conflict where a ready Profile A pull request cannot merge cleanly against a sibling merged since its base. It fails closed to a human gate with no automatic reapplication (ADR-016).
- **Guard authorisation** - one operator declaration on one work unit that clears a project guard for that unit, carrying the environment the guard reads, the labels the pull request is born with, and the reason. The engine projects it to the producer's session and prompt, the gate, the publish-time re-gate, the pull request, and the merge coordinator's re-gate; the producer runs under it but may not author it, and a producer diff that edits it parks the unit (VaultForeman ADR-011). An undeclared guard is not a cleared guard. Avoid: override, bypass, exemption.
- **Terminal deploy-check set** - the policy-declared checks whose terminal state a deploy-triggering Profile A merge waits for before the next merge.
- **Deploy-aware merge pacing** - the Profile A coordinator behaviour that waits for configured check recompute before merging a rebased PR and waits for the base branch deploy-check set to become terminal between consecutive deploy-triggering merges.
- **Terraform state-lock contention** - a proved deploy-check failure where the check evidence shows Terraform could not acquire the shared state lock. The first-flight behaviour is a safe terminal halt with reconciled artefacts; bounded retry requires later policy.
- **Native drain contract** - the Woof-owned drain-policy declaration (`merge_after_ready_pr`, `rerun_after_merge`, `mark_unit_done_after_publish`, `commit_backlog_state`, `stop_when_no_eligible_units`) that replaces the transitional VaultForeman `executor` block at retirement.
