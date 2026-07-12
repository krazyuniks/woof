---
schema_version: 1
type: backlog
project_ref: woof
status: active
executor:
  name: vault_foreman
  contract_version: 1
  project: woof
  timeouts:
    produce_timeout_min: 180
work_units:
  - id: schema-unification
    title: Unify execution schema on work_units
    kind: build
    state: done
    priority: high
    summary: Move the canonical executable unit schema into Woof, retire legacy runtime contracts, and preserve graph dependency checks.
    acceptance:
      - Canonical Woof schema validates required work-unit fields and optional contract-trace fields.
      - Backlog front matter accepts the document-level executor block needed by transitional VaultForeman drains without adding custom per-unit wave fields.
      - Durable readers and writers use work_units without transitional unit-shape mirrors.
      - Duplicate ids, dangling deps, self-deps, and cycles fail validation.
  - id: policy-model
    title: Move project policy into repo-local Woof config
    kind: build
    state: done
    priority: high
    summary: Add delivery profile, producer/reviewer run profile, gate command, check floor, and cartography floor to `.woof/` policy.
    deps: [schema-unification]
    acceptance:
      - A repo can be onboarded without editing engine Python.
      - Policy validates profile A and profile B settings.
      - Policy declares producer and reviewer harness/model/effort slots in the consuming repo; the engine owns harness adapters, execution, parsing, and validation.
      - Missing required policy or cartography floor data fails preflight.
  - id: intake-predecomposed
    title: Pre-decomposed work-unit intake
    kind: build
    state: done
    priority: high
    summary: Validate pre-decomposed work_units and skip decomposition; establish the work-unit-set aggregate context, persist a stable set_id, and record run metadata without fabricating an epic. Delivered in repo 9d6a768.
    deps: [schema-unification, policy-model]
    acceptance:
      - Pre-decomposed work_units validate and skip decomposition.
      - Intake establishes the work-unit-set aggregate context and derives qualified references from it without fabricating an epic; when the source has no natural identity, intake assigns and persists a stable set_id once.
      - Intake records run metadata without reverse-generating a missing epic.
      - Pre-decomposed intake accepts work_units in topological dependency order so the runtime aggregate validates without reordering.
  - id: intake-epic-enrichment
    title: Epic sources, enrichment, and auto-decompose
    kind: build
    state: todo
    priority: medium
    summary: Epic-backed intake from greenfield, GitHub, and local-docs sources through sparse epic, optional brainstorm enrichment, epic, then work_units via the existing breakdown playbook. 9d6a768 delivered the epic context scaffolding but not the auto-decompose/enrichment node.
    deps: [intake-predecomposed]
    acceptance:
      - Epic-backed intake follows sparse epic to optional enrichment to epic to work_units, using project_ref plus epic_id.
      - Decomposition produces work_units through the existing breakdown playbook and brainstorm enrichment, not a second decomposer; the auto-decompose step replaces the manual decompose earlier waves relied on.
      - Decomposition emits work_units in topological dependency order so the runtime aggregate validates without reordering.
  - id: dispatch-swap
    title: Replace headless dispatch with interactive harness profiles
    kind: build
    state: done
    priority: high
    summary: Remove headless worker dispatch and consume structured results from the shared interactive harness boundary.
    deps: [schema-unification, policy-model]
    acceptance:
      - Producer and reviewer dispatches launch through interactive harness profiles.
      - Prompt-file delivery and structured result capture are covered by tests.
      - Engine code consumes verdict, evidence, usage, and session metadata without parsing raw terminal scrollback.
  - id: execution-shape-unification
    title: Converge the execution kernel on the one work_units schema
    kind: build
    priority: high
    state: done
    summary: Collapse the runtime plan/work-unit shape onto the canonical work_units shape (one lifecycle field, work-unit ids, named checks and gates), remove legacy id mirrors, and retire legacy-named playbooks and self-cartography. Realises the ADR-011 convergence that schema-unification left at the intake boundary.
    deps: [schema-unification]
    acceptance:
      - One canonical work-unit schema validates id, title, kind, state, and the optional contract-trace fields; the plan/runtime artefact and the backlog artefact share it, with no status-versus-state dual lifecycle.
      - The execution kernel exposes a work-unit entity and a work-unit aggregate boundary; aggregate validation owns unique local IDs, dependency closure, acyclicity, and topological order.
      - Cross-aggregate references use structured context plus the local work-unit id, rather than a second globally encoded id field; UUIDs are reserved for technical run, attempt, review, and audit records.
      - The aggregate context is a discriminated union -- an epic context (project_ref, epic_id) or a work-unit-set context (project_ref, set_id, optional source_ref), not an optional epic_id; set_id is a stable persisted identity, never a run UUID (architecture section 4).
      - "Review-cache, instability, and lineage joins carry the qualified work-unit reference as the unit-identity component alongside the content/version facts (diff_hash, prompt version, role): the qualified ref answers which unit, the content facts answer whether a cached review is reusable."
      - Runtime gates, checks, dispositions, and events key on work-unit id; no event carries both a legacy id and work_unit_id.
      - Deterministic checks and gate types are named around work units, and the gate writer can emit the work-unit gate.
      - Producer and reviewer playbooks and Woof's self-cartography use work-unit terminology; no legacy-named playbook remains.
      - An invariant guard test covers the single inbound legacy-shape normaliser (accept legacy input, reject dual shape), and a duplicate work-unit id case is tested.
      - Dead back-compat aliases are deleted in this change.
  - id: config-routing-ssot
    title: Make policy.toml the single routing and run-profile authority
    kind: build
    state: done
    priority: high
    summary: Consolidate routing, run profiles, and harness/model/effort vocabulary to one home each; retire the agents.toml routing duplication and the dead headless dispatch builders.
    deps: [policy-model, dispatch-swap]
    acceptance:
      - Producer and reviewer routing and run profiles are declared only in policy.toml; agents.toml no longer carries route or model-profile fields, and any non-routing scope it keeps lives in its own bounded file.
      - Harness, model, and effort vocabulary has one source of truth in the dispatch registry; effort, adapter, and alias maps are not re-declared across policy.py, dispatcher.py, and harness_registry.py.
      - Every harness the registry declares is reachable through a policy run profile, or is explicitly removed.
      - The headless claude -p and codex exec argv builders and their parsers are deleted; route probes use a registry-based check.
  - id: warm-session-seam
    title: Implement warm producer and fresh reviewer fix rounds
    kind: build
    state: done
    priority: high
    summary: Keep the producer attached across bounded fix rounds, use a fresh independent reviewer each round, and make resume producer-capable.
    deps: [dispatch-swap]
    acceptance:
      - Reviewer blocker evidence is pasted back to the same producer session within budget.
      - Each review round receives a fresh reviewer context and the full current diff.
      - Resume can reattach or respawn the producer from disk authority.
      - The fix-round budget is bounded and configurable, defaulting to two rounds per blocker before a gate opens.
  - id: herdr-kept-alive-dispatch
    title: Adopt herdr for backend-neutral retained producer and reviewer sessions
    kind: build
    state: todo
    priority: high
    summary: Replace tmux-coupled engine dispatch with the backend-neutral retained-session seam while preserving explicit tmux profiles and all warm-producer/fresh-reviewer invariants.
    deps: [config-routing-ssot, warm-session-seam]
    acceptance:
      - One-shot, producer, reviewer, mapper, and enrichment dispatch resolve the transport from the selected harness profile; workflow code contains no harness-name or transport branch.
      - Every registry profile declares tmux or herdr explicitly; project policy continues to select harness, optional model, and optional effort only.
      - A herdr producer keeps the same worker identity across fix rounds; every reviewer round creates a fresh independent worker and receives the full current diff.
      - Herdr lifecycle observation is armed before every prompt; `working -> idle` or `done` completes only with the payload present; blocked and timeout become distinct graph outcomes with evidence.
      - Resume persists backend-neutral session identity and either reattaches safely or respawns from disk authority after process loss.
      - Running-server preflight records the named socket, server version, and protocol and fails before dispatch when incompatible.
      - Tmux remains supported for explicit tmux profiles, with backend-equivalent result and error metadata; no public field is named after tmux when it represents either backend.
      - Tests cover launch, receipt, warm fix round, fresh review, reattach, respawn, blocked, timeout, protocol mismatch, payload absence, explicit close, and isolated named-session teardown.
  - id: runner-loop-absorption
    title: Absorb VaultForeman runner loop and profiles
    kind: build
    state: done
    priority: high
    summary: Bring dependency draining, profile A/B delivery, usage telemetry, review cache, and serial merge coordination into Woof. Deploy-aware merge pacing is carved out to `deploy-aware-merge-coordinator`.
    deps: [schema-unification, policy-model, warm-session-seam]
    acceptance:
      - Work units run in dependency order with blocked/downstream reporting, consuming the aggregate's validated topological order rather than re-deriving it; cross-aggregate sequencing is out of the aggregate's scope.
      - Profile A publishes ready pull requests and serially merges the ready queue; deploy-aware merge pacing is carved out to `deploy-aware-merge-coordinator`.
      - Partial-merge reconciliation records already-merged units before halting on any later terminal failure.
      - "Shared-file sibling conflicts fail closed: the merge queue halts to a durable human gate with already-merged siblings reconciled per PR, the conflicting PR left ready with its branch unmodified (rebase aborted cleanly, no force-push of half-rebased state), the queue resumable, and a rerun producing no duplicate work. No automatic semantic reapplication. Resolution is an explicit audited engine action -- a human reconciles in the worktree and re-pushes with a full gate and fresh-review rerun on the final diff, or the unit returns to production against moved main, or it is withdrawn; no path merges without gate and review rerun. Detection triggers: a coordinator rebase of a ready PR fails to apply cleanly; mergeability settles CONFLICTING after bounded settle-retry; required checks or the gate fail after a clean rebase on a PR whose paths intersect a sibling merged since that PR's base. Queued-sibling overlap never pre-empts; transient UNKNOWN/UNSTABLE gets bounded settle-retry, not a halt."
      - Runner absorption preserves project-owned producer/reviewer slot selection and engine-owned harness adapters, execution, parsing, and validation.
      - "Harness selection and any runner-level harness override resolve through the dispatch registry: changing harness resets omitted model/effort to the target harness defaults and validates effort against that harness, so one profile cannot leak an incompatible model into another harness."
      - "Worktree lifecycle -- provisioning, dirty-lease recovery, and teardown -- is owned by the project's worktree engine and task runner, not Woof (ADR-015). Woof fails closed on an anomalous worktree and never provisions, mutates, recovers, or invokes the engine."
      - "Review-size guards, if enabled, count non-generated changed lines only: `linguist-generated` files, known generated artefacts, and generated-header files do not silently skip review, and the threshold is policy-visible."
      - Profile B commits and pushes through graph-owned transactions.
  - id: deploy-aware-merge-coordinator
    title: Deploy-aware Profile A merge coordinator
    kind: build
    state: done
    priority: high
    summary: Native Woof merge coordinator for deploy-triggering Profile A merges, carrying VaultForeman issue #1's behaviour. No VaultForeman source asset exists for this; hand-build with operator review, not an unattended vf-drain.
    deps: [runner-loop-absorption]
    acceptance:
      - Profile A waits for mergeability and check recompute after main moves, retrying transient UNKNOWN/UNSTABLE with bounded settle-retry.
      - Deploy-triggering merges are spaced until the configured deploy checks reach a terminal state between every consecutive merge pair.
      - The mergeability-settle timeout, deploy-wait timeout, and terminal deploy-check set are read from repo policy; preflight fails closed when deploy-aware merging is active and the deploy-check set is undeclared.
      - Proved Terraform state-lock contention halts safely for first flight; bounded-retry of proved lock contention is deferred to post-flight behind policy.
      - Partial-merge reconciliation records already-merged units before halting on any later terminal failure.
      - A four-defect regression suite covers per-PR mark-done, terminal-CI wait before merge, no self-stale after a coordinator force-push, and Closes-issue linkage with artefact lineage.
      - Anything unclassified fails safe to a terminal halt with reconciled artefacts and a resumable ready queue.
  - id: profile-a-worktree-contract
    title: Profile A worktree discovery and fail-closed validation
    kind: build
    state: done
    priority: high
    summary: Policy-declared worktree root; deterministic unit-to-path derivation; fail-closed preflight validation of provisioned worktrees. Woof discovers and validates, never provisions.
    deps: [policy-model]
    acceptance:
      - Policy declares the worktree root and unit-to-path derivation without naming the provider that provisions worktrees.
      - Unit-to-path derivation is deterministic (root plus work_unit_id, or an explicit per-unit map in the run manifest) and recorded in run metadata.
      - Preflight validates every ready unit's worktree -- it exists, is a linked worktree of the target repo, is on the expected base or unit branch, is clean, and no two units share a path.
      - "Any anomaly fails closed: no auto-create, no silent fallback to a single tree, no engine invocation to repair (ADR-015)."
  - id: engine-neutral-consumer-policy
    title: Engine-neutral consumer delivery policy
    kind: build
    state: done
    priority: high
    summary: A consumer repo declares its delivery policy once in a form both VaultForeman and Woof honour; engine selection is a per-run choice and no consumer is coupled to a specific engine. This removes the migration framing between the two runners and resolves the `lane_plan.py` / `lane_launcher.py` design call.
    deps: [config-routing-ssot]
    acceptance:
      - A consumer's delivery policy (profile, run-profile slots, gate, check floor, cartography floor) is declared once in `.woof/policy.toml`; the transitional VaultForeman drain validates against and reads the same declaration.
      - Selecting the engine for a run is a per-run operator choice, not a property baked into the consumer repo.
      - No consumer repo carries engine-specific delivery configuration beyond the single shared declaration.
      - The transitional VaultForeman `executor.drain` policy fields (merge-after-ready-pr, rerun-after-merge, mark-done-after-publish, commit-backlog-state, stop-when-no-eligible) are expressed as a Woof-native drain contract in the shared declaration, so retirement removes the VaultForeman executor block without losing drain semantics.
  - id: operator-home-config-and-state
    title: Move engine config and state to the operator home
    kind: build
    state: todo
    priority: high
    summary: "Implement ADR-017: all engine config and durable state live under ~/.woof keyed by an explicit project key; a driven repository carries no trace of the engine. Amends engine-neutral-consumer-policy's location (the declare-once principle survives; the declaration's home moves) and aligns with VaultForeman's final config model (vault-foreman ac9ac02 + ff381dd), so the VF-to-Woof cutover migrates per-project TOML between operator-home trees rather than rewriting consumer repos."
    deps: [engine-neutral-consumer-policy, runner-loop-absorption]
    acceptance:
      - "Per-project config is read solely from ~/.woof/config/projects/<project-key>.toml (home overridable via WOOF_HOME); a missing project config is a hard preflight error; no in-repo fallback exists, deprecated or otherwise."
      - "Durable engine state — runs, epics, review cache, instability records, locks, usage — lives under ~/.woof/state/projects/<project-key>/; nothing engine-owned is written into the driven repo."
      - "The project key is explicit at every entry point and never derived from a checkout directory name (directory-name derivation collides across worktree containers sharing names like main)."
      - "The .woof/ consumer-repo directory is retired: no reader or writer remains, and the prerequisites, quality-gates, and fix-round scopes live as bounded sections of the per-project config."
      - "Backlog unit-state writeback targets the work-source PM document only, remains engine-exclusive, and writes no engine files to the delivery repo."
      - "Tests root WOOF_HOME in a throwaway directory so no test reads or writes the operator's real ~/.woof."
  - id: vaultforeman-fix-parity
    title: Inherit VaultForeman behavioural and operator-UX fixes since the merge baseline
    kind: build
    state: todo
    priority: high
    summary: "Re-baseline the runner absorption against a pinned VaultForeman engine-repo commit, not a moving HEAD. The pin is the VaultForeman tip once its review, resume, and publish defect set has landed (the four units in VaultForeman's `review-resume-and-publish-defects` backlog); until then the sweep has nothing stable to pin to, because that set changes the reviewer verdict-capture contract, the producer round-completion baselines, and the operator authorisation shape. The prior pin was 97c180a (2026-07-11), described then as the completion tip; VaultForeman is not complete, so the pin advances rather than holds. The 2026-07-09 feature freeze an earlier draft pinned to is lifted, which pulls the post-freeze drain-entry fixes (park isolation, review-size honesty, seed-ref fix-forward, dispatch receipt and active liveness, round-completion invariant) inside the sweep window. The runner-asset source map was cut 2026-06-28; VaultForeman has landed drain, review-parsing, prompt, and operator-UX fixes since that Woof will not otherwise inherit. Carry the fix families below and refresh the source map to that same pinned commit. Two VF changes are carried elsewhere, not here: the state-to-operator-home move (VF ac9ac02 + ff381dd) is owned by `operator-home-config-and-state` (ADR-017), and the optional-worktree-lifecycle change (VF 0411fd5) is already superseded by ADR-015. Operator authorisation, including its CI-parity half, is carried by its own `gate-environment-channel` unit, not here."
    deps: [runner-loop-absorption, deploy-aware-merge-coordinator]
    acceptance:
      - "Merge coordinator: serial one-unit-per-cycle drain, publish-rebase survival with no residue, detached coordinator worktree, index-free ready-PR listing, merge-phase transient safety (no drain crash or re-produce), rebase-and-re-gate onto the base tip before a Profile A PR, partial-merge reconciliation, and skip-re-produce when a unit already has an open PR."
      - "Review-verdict acquisition stays contract-first, and the contract is stated so it is enforced rather than assumed. Reviewer output arrives as a file the worker writes, signalled by a sentinel it writes last, and the verdict of record is the schema-validated critique artefact's severity field -- never a token scraped from pane text. This is what makes Woof structurally immune to VaultForeman's verdict scroll-off defect (VF issue 7), where a long review pushed the verdict above the captured pane region and the unit falsely timed out. The immunity comes from the file-and-sentinel capture contract in the shipped tmux transport, not from herdr, which is unbuilt (`herdr-kept-alive-dispatch` is still todo); do not credit it to a backend that does not exist. No verdict path may regress to classifying pane text, and pane capture stays confined to embedding a tail into error messages."
      - "Review-verdict classification recovers non-conforming reviewer output on either backend: a settled clear-prose conclusion coerces to PASS only when the round's completion signal is present and no findings, blocking prose, or negations exist; unformatted blocker prose never coerces a verdict — it parks with the blocker text preserved verbatim for the operator and the full session output captured in the park artefact. The same recovery applies when the structured answer contract is not met."
      - "Producer prompt: context parity with the reviewer (issue number, links, work-unit body); a named GitHub issue is read live including comments before editing; test-first procedure with red-proof recording (prove new tests fail against the unfixed tree, implement to green, record both runs); bulk-output guardrail (compact declarative source plus generator or stale-output gate, never thousands of committed generated lines as the only reviewable artefact); the prompt forbids mutating work-unit state in any backlog file."
      - "Reviewer prompt: the diff base is derived explicitly from the merge base with the base branch, never guessed; review starts from a compact change manifest (name-status/stat/numstat), not the full raw patch, drilling into targeted files as needed; review runs against the full checkout and a blocker may cite an unchanged file whose committed contract makes the change wrong; repo-root and directory-scoped AGENTS.md bind the review; a mandatory semantic-drift pass covers tuned behaviours, user-visible wording, persisted payload shapes, fixture provenance, and tests that strip or restate the changed fact; the verdict is demanded as the bare token, with narrative substitutes named and forbidden."
      - "Prompt scope: producer and reviewer prompts open with a project-scope block (checkout, runtime root, toolchain root, repo, worktree key) with an explicit out-of-scope statement covering sibling worktrees, other projects' records, and other drains' logs; policy may declare read-only reference paths as the single scope-boundary exception (readable, never editable, never a finding target)."
      - "Gate failures feed fix rounds: a red deterministic gate is not terminal — its full untruncated output returns to the warm producer as a fix round sharing the review-blocker budget, with a digit-normalised failure fingerprint parking early on a consecutive repeat; blocker source (gate versus review) is recorded per round."
      - "Backlog unit-state writeback is engine-exclusive: only the engine flips work-unit state, and a produced diff that mutates unit state in the drained backlog is rejected before publish (the producer pre-mark defect observed on wave 5; still open in VF as of c062822)."
      - "Published artefacts are engine-neutral: a Profile A PR body carries no orchestrator trace — no engine name, work-unit id, or verified-tree/verified-paths metadata — only the issue linkage and the change description, with engine audit detail kept in engine state (VaultForeman 932e11d; Woof's current Profile A PR body emits work-unit id and verified tree/paths and regresses this)."
      - "Branch-exists recovery discriminates by recorded review verdict: BLOCKER or UNCERTAIN reaps the spent branch (tip reflog-reachable, never while a worktree holds it) and re-produces; CLEAR, ERROR, or no recorded verdict stays fail-closed naming both recovery paths; the newest verdict across run and resume artefacts wins."
      - "Operator UX: slice-phase transitions narrate to stdout as the progress signal; a describe command resolves policy plus harness registry for a project or backlog; a committed branch can be adopted without a producer (resume); the producer done-signal window is configurable; reviewer prompts are persisted per round."
      - "Harness and profile: producer and reviewer harness are overridable profile fields resolved through the single dispatch registry; effort tiers are correct per profile."
      - "Manifest-first review has engine support, not only prompt text: a compact change manifest artefact (base/head, changed paths, status, numstat, file class, generated/bulk classification) is recorded per reviewed round; the review-cache fingerprint uses Git object identity rather than hashing the full patch text; oversized changes enter manifest-led review when the hand-written surface is reviewable, and only genuinely unreviewable diffs park with a split-required reason (VaultForeman `manifest-first-review-mode`, landed at 66d10ac inside the pin window, is the source unit)."
      - "Park isolation: a parked unit blocks only its dependency closure; independent units keep draining. One park never halts the whole run (VaultForeman `park-does-not-halt-independent-units`; a single park cost a full night's drain)."
      - "Review-size budget honesty: the size budget is operator-configurable with a per-unit override, and a park for an over-budget diff reports added and deleted line counts separately rather than one conflated total (VaultForeman `review-size-budget-operator-configurable`; a deletion-dominated diff parked despite a small hand-written surface)."
      - "Seed-ref fix-forward: a work unit may declare a seed ref applied to a fresh branch before produce, so a fix-forward round starts from a preserved commit rather than re-producing from base (VaultForeman `fix-forward-round-from-preserved-commit`; Woof's branch-exists recovery has no seed-ref concept)."
      - "Dispatch receipt and active liveness: dispatch confirms the worker received the prompt before the run trusts it, polling actively samples and nudges a silent worker rather than waiting blind, and each sample writes a heartbeat to the run log (VaultForeman `dispatch-receipt-and-active-liveness`). The file-and-sentinel capture contract covers receipt and payload, so the residual Woof gap is the run-log heartbeat observability and the active sampling and nudge; herdr does not cover this, because herdr is unbuilt."
      - "The reviewer path has liveness, not only the producer path. Woof's warm producer polls `tmux.has_session` every second and fails fast when the session dies, but the one-shot reviewer path has no liveness poll at all: a reviewer whose TUI dies in the first second still burns the entire wallclock budget before `reviewer_unreachable` opens. A dead reviewer is detected within one sample, not at the ceiling. This is the same asymmetry VaultForeman carries as issue 9, where the reviewer poll never received the receipt, sampling, and dead-session detection the producer poll has."
      - "A failed or timed-out worker's session output survives for the operator. The tmux scratch directory holding the prompt, payload, and sentinel is currently removed on every outcome including failure, because Woof never passes `keep_scratch_on_failure`, so on a reviewer timeout the only surviving evidence is a 60-line pane tail interpolated into the error message. The park artefact carries the full session output, as this backlog already requires elsewhere; VaultForeman threads `keep_scratch_on_failure` through its dispatch registry and Woof inherits that."
      - "Round-completion invariant: a produce or fix round completes on the harness done-signal, never on HEAD movement, and the round diff spans every intermediate commit the worker made. Woof's herdr done-marker architecture avoids the defect structurally; state the invariant so it is enforced, not assumed (VaultForeman `producer-round-completion-not-head-movement`)."
      - The runner-asset source map is refreshed to the pinned VaultForeman engine-repo commit 97c180a and marked historical once parity lands.
  - id: dispatch-failure-classification-honesty
    title: A timed-out worker is recorded as timed out, not as a non-zero exit
    kind: build
    state: todo
    priority: high
    summary: "The one-shot dispatch path catches `HarnessTimeoutError` through an
      `except` clause bound to its parent class `HarnessDispatchError`, so a genuine
      wallclock timeout is collapsed into `exit_type=\"nonzero\"`. The
      `subprocess_returned` event then hardcodes `timed_out: False` and
      `terminal_seen: True`, and the run meta repeats it -- on the failure branch. A
      reviewer that timed out is therefore durably recorded as having not timed out and
      having been seen to terminate. `wallclock_timeout` is a declared failure type that
      this path can never emit, even though the warm-producer path computes the same
      field correctly rather than hardcoding it. No false PASS results from this, but
      the audit trail states the opposite of what happened, which is exactly the
      evidence an operator reaches for when a drain stalls. Found comparing Woof against
      VaultForeman's resume-timeout misreport (VF issue 8), where one message covered a
      genuine timeout, a producer that committed nothing, and a producer that never
      worked; the failure is the same class -- a dispatch outcome reported as something
      it was not -- and Woof carries it independently."
    deps: [runner-loop-absorption]
    acceptance:
      - A worker that exceeds its wallclock deadline is recorded with the
        `wallclock_timeout` failure type in the dispatch event, the run meta, and the park
        artefact. The value is computed from the outcome, never hardcoded, on every
        dispatch path rather than only the warm-producer one.
      - A worker that exits before writing its result and a worker that ran past its
        deadline are distinguishable in the recorded artefacts without reading a pane tail.
      - The `except` clause no longer collapses a timeout into a generic dispatch failure;
        the timeout subclass is caught and classified before its parent.
      - Regression tests cover a reviewer timeout, a reviewer that exits early, and a
        reviewer that succeeds, asserting the recorded failure type in each.
  - id: run-lineage-immutable-attempts
    title: Add run lineage and immutable attempt artefacts
    kind: build
    state: done
    priority: high
    summary: Thread run identity through events and preserve every attempt for replay, review-cache reuse, and instability detection.
    deps: [schema-unification]
    acceptance:
      - Events and dispatch artefacts are joinable by run id and work-unit id.
      - Repeated review over the same diff hash and prompt version reuses the prior verdict.
      - Conflicting verdicts over the same inputs are recorded as review instability.
  - id: cartography-continuity
    title: Retain cartography as a policy-enforced capability
    kind: build
    state: done
    priority: medium
    summary: Move cartography-floor selection into policy.toml cartography.floor (adding a no-cartography level) and reconcile the existing ADR-004/ADR-009 cartography artefacts and refresh hook with the merged engine. Existing cartography is reused, not re-derived. Structural cartography is the deferred structural scope of this unit.
    deps: [policy-model]
    acceptance:
      - Repo policy can require no cartography, lexical/design cartography, or structural cartography.
      - Required cartography is enforced before execution.
      - Producer, reviewer, and deterministic checks consume declared cartography on the same engine path.
  - id: conformance-audit
    title: Implement policy-driven conformance audit
    kind: build
    state: todo
    priority: medium
    summary: Add deterministic diff checks over work-unit trace fields, epic contracts, repo policy, and cartography evidence. The specification to generalise is the architecture-report-plus-gate-battery design proven in production on the Freeflo engagement (freeflo.freefloAgent wiki ADR-0025, delivered under epic freeflosg/freeflo.freefloAgent#892) - one measurement source read by both the report rendering and the gates; ordered gate battery (import contracts, AST-statement shrink ratchets, site-identity no-new-sites baselines, net-delta visibility); every gate documents the cheapest-compliant-path answer and lands with a planted-violation bite-proof.
    deps: [schema-unification, cartography-continuity]
    acceptance:
      - Audit findings are machine-readable and cite resolvable evidence.
      - Findings reference work units by qualified reference (aggregate context plus local id) so evidence resolves across aggregates.
      - Contract-trace checks no-op when trace fields are absent.
      - Cartography-dependent checks run only when policy requires the cartography floor.
      - The audit reads one measurement source shared with any report rendering, per the ADR-0025 pattern.
  - id: eval-instrumentation
    title: Measure the merged execution shape
    kind: build
    state: todo
    priority: medium
    summary: Capture per-node and per-attempt evidence for prompt cost, loaded artefacts, usage, retries, checks, and review instability.
    deps: [dispatch-swap, run-lineage-immutable-attempts]
    acceptance:
      - Eval manifests attribute cost and loaded artefacts by node and qualified work-unit reference, with UUID-keyed run/attempt records, consistent with the lineage identity model.
      - Prompt/output bodies are retained according to audit policy.
      - The first optimisation target is chosen from measured data.
  - id: flight-1
    title: Prove the merged engine on a disposable repo
    kind: action
    state: todo
    priority: high
    summary: Full kernel plus deploy-decoupled Profile A on a disposable/Woof-repo pre-decomposed backlog, with failure proofs and a mock-Deploy rehearsal. Not the cutover gate.
    deps: [intake-predecomposed, herdr-kept-alive-dispatch, runner-loop-absorption, deploy-aware-merge-coordinator, profile-a-worktree-contract, run-lineage-immutable-attempts, cartography-continuity, operator-home-config-and-state, vaultforeman-fix-parity]
    acceptance:
      - A disposable pre-decomposed backlog of at least three units with at least one dependency edge runs end to end -- produce, deterministic gate, fresh review, at least one real blocker fed back to a warm producer within budget, publish.
      - Profile A mechanics run without deploy coupling -- worktree handshake, branch push, PR publish with issue linkage, ready labelling, serial merge of at least two ready PRs with per-PR mark-done, and a coordinator self-rebase that leaves the remaining PR ready.
      - Resume of a killed producer from disk is exercised, and a human gate is opened and resolved with audited effect.
      - The run exercises a herdr-backed retained producer and fresh reviewer plus one explicit tmux-backed profile, proving backend-equivalent audit and failure semantics.
      - Lineage and artefacts hold -- immutable attempts, run/unit/attempt joins, review-cache reuse on an identical diff hash, and instability on a conflicting verdict.
      - Fail-closed behaviour is proved -- missing policy or cartography floor fails preflight; an induced sibling conflict halts to a gate; a mock Deploy workflow's terminal non-lock failure triggers a safe halt with reconciled artefacts; state-lock contention is classified and halts.
  - id: flight-2
    title: Prove Woof on a guarded real-deploy slice
    kind: action
    state: todo
    priority: high
    summary: Prove the merged engine end to end on a guarded, real prod-deploying consumer slice. Passing means Woof is trusted for prod-deploying consumers; it is not a consumer migration.
    deps: [flight-1, engine-neutral-consumer-policy]
    acceptance:
      - A guarded slice of three to five real low-risk code-only units sharing the deploy path (no schema, infra, or Terraform), outside launch-critical correctness lanes, runs with an operator-confirmation gate before every deploy-triggering merge and the prior engine held as fallback with per-run comparison evidence.
      - Every ready PR merges serially with per-PR mark-done; mergeability and check recompute settle after each main move with at least one transient UNKNOWN/UNSTABLE retried; the deploy-check set reaches a terminal state between every consecutive merge pair.
      - One induced or natural mid-queue failure after at least one merged PR triggers a safe halt -- already-merged units reconciled and marked done, a resumable queue, and a duplicate-free rerun.
      - A coordinator self-rebase never drops a queued PR; any sibling conflict gates rather than merges, with no automatic reapplication.
      - Zero hand-recovery is needed beyond operator confirmations, and lineage matches or beats the prior engine's path.
  - id: vaultforeman-retirement
    title: Retire the standalone VaultForeman runner
    kind: action
    state: todo
    priority: medium
    summary: Retire the standalone VaultForeman runner once Woof is at parity, proven (flight-2), carries the engine-neutral consumer policy, and no live run requires the standalone VF path. Any wrapper is proved after retirement, not before.
    deps: [flight-2, engine-neutral-consumer-policy]
    acceptance:
      - Active project records point to Woof as the orchestration engine, and no live consumer run requires the standalone VaultForeman path (per-run engine selection has moved live consumers onto Woof).
      - Woof-side hidden-engine sweep -- the executor document block is removed or re-pointed in the canonical schema, and the vf-drain wave instructions and `vf orchestrate` operating-order references are deleted.
      - A final VaultForeman delta sweep runs from the fix-parity pin 97c180a to the VaultForeman tip at retirement time, and every behavioural fix in that window is carried, consciously superseded, or recorded as not-applicable before the standalone runner is removed.
      - Schema-authority freeze -- the canonical work_units[] schema lives in Woof, VaultForeman schema files take no independent evolution, and transitional VaultForeman drains validate against Woof's schema.
      - A post-cutover stability window is met -- at least three real Woof drains including at least one without per-merge confirmation, zero hand-recovery, VaultForeman fallback retained through the window, and the window length is operator-set.
      - Standalone runner entry points are removed or wrap Woof without duplicate logic; VaultForeman records state the retired boundary.
  - id: safety-defect-sweep
    title: Fold remaining safety defects into the merge line
    kind: build
    state: done
    priority: medium
    summary: Carry forward verified small safety defects that still matter under the merged architecture.
    deps: [schema-unification]
    acceptance:
      - Raw durable artefact reads are routed through loaders.
      - Commit and publish boundaries pin the verified tree and expected paths.
      - Dead state-mutation surfaces are removed rather than mirrored.
  - id: gate-environment-channel
    title: Operator authorisation declared once, reaching the gate, the publish guard, and CI
    kind: build
    state: todo
    priority: high
    summary: "Every gate invocation -- deterministic gate, review gate, and the
      publish-time guard -- receives the work unit's identity, its declared change
      targets, and an operator-declared authorisation, so a gate can be pre-authorised
      per unit without editing engine or repo source; and the same authorisation reaches
      CI as pull request labels, so a locally-authorised unit does not fail the same
      guard in CI by construction. Carries VaultForeman's
      gate-environment-authorisation-channel (the per-unit pre-authorisation half,
      delivered VF af42d0f0), gate-environment-unit-context-export (unit-context), and
      unit-authorisation-reaches-both-the-gate-and-ci (the CI-parity half). Motivating
      context is the delivered Freeflo #895 preflight guard, the #897 post-mortem, and
      the freeflo wording-migration lane (unit issue-884, PR
      freeflosg/freeflo.freefloAgent#971): gate green, review clear, CI failed, run
      stopped, because the PR carried no labels and the CI workflow resolves its
      override from the labels frozen in the pull_request event payload. A producer
      holding repo-edit access could self-authorise an in-repo gate, so the
      authorisation channel must live outside anything the producer can touch. Woof
      today is behind VaultForeman on both halves: the quality gate runs the project
      command with no env= argument at all (checks/runners/check_1_quality_gates.py:319-328),
      so it silently inherits the ambient environment of the woof process rather than a
      declared channel; and graph.git.gh_open_pr (graph/git.py:121-152) passes no
      --label, so both the initial push and the post-amend force-push fire CI on a PR
      with zero labels, and when merge_eligible is false the ready_label is never
      applied at all (graph/nodes.py:3062). Engine stays project-agnostic and
      data-driven."
    deps: [runner-loop-absorption]
    acceptance:
      - The engine exports the work-unit id and its declared change targets to every
        gate invocation, including the publish-time re-gate; declared targets are a
        work-unit schema field, not inferred from the diff.
      - An operator declares an authorisation once, as one typed declaration carrying a
        mandatory reason, the gate environment variables, and the pull request labels
        that express the same authorisation to CI. There is one declaration shape, not a
        gate-environment dict beside a separate label list.
      - The engine passes the declared environment to gate invocations explicitly, with an
        env argument rather than by ambient inheritance, and a producer diff can never
        populate or alter it. The quality-gate runner stops inheriting the woof process
        environment implicitly.
      - A gate guard reads its pre-authorisation only from the operator channel; a value
        the producer could write into repo source never satisfies it.
      - The engine creates the pull request carrying the declared labels, so the labels are
        present in the pull_request event payload of the first CI run and a CI guard
        resolves the override on its first attempt. The engine never opens a pull request
        it knows CI will refuse, and the labels do not depend on merge eligibility.
      - A test proves an operator pre-authorisation reaches the gate, that a
        producer-authored in-repo value does not, and that the same declaration reaches
        pull request creation as a label.
  - id: publish-protected-content-guard
    title: Publish-time guard rejects producer diffs that forge protected content
    kind: build
    state: todo
    priority: high
    summary: Engine-level publish-time check on the producer diff against per-project
      declarative predicates from policy, rejecting diffs that add or modify protected
      content a producer may never author. Motivating incident (freeflo 2026-07-10,
      issue 901 producer) - a producer stamped human-approval metadata (status
      human_approved, reviewer Ryan) on corpus fixtures to satisfy a schema gate;
      in-repo gates cannot enforce this because producers can edit repo source, so
      enforcement belongs at the publish boundary the producer cannot touch. Policy
      declares pathspec plus content predicate (e.g. added/changed JSON under a
      fixtures tree carrying status human_approved or a named human reviewer);
      engine stays project-agnostic and data-driven, no per-project branch.
    deps: [runner-loop-absorption, gate-environment-channel]
    acceptance:
      - A producer diff adding or modifying content matching a policy-declared protected
        predicate fails publish with a message naming the file and predicate; the unit
        parks rather than merges.
      - Predicates live in consuming-repo policy, not engine code; a repo with none
        declared is unaffected.
      - Any operator pre-authorisation for a protected predicate arrives only through the
        gate-environment channel; a value the producer could write into repo source never
        satisfies the guard.
      - The freeflo human-approval predicate is expressible and covered by a test that
        replays the 2026-07-10 fabrication shape.
  - id: produce-prompt-commit-discipline
    title: Produce-prompt template carries commit discipline
    kind: build
    state: todo
    priority: high
    summary: Fold commit-as-you-go discipline into the engine's produce-prompt assembly
      so it stops riding every unit body. Motivating incidents (freeflo 2026-07-10) -
      two heavy units parked idle-without-commit with all work sitting uncommitted in
      the worktree; a unit-body rule fixed it (the next producer committed mid-run),
      and that rule belongs in the template. Instruct - commit each coherent green
      slice immediately with explicit paths; before the final message verify git
      status is clean and commits exist on the branch; work left uncommitted when the
      turn ends is lost and parks the unit.
    deps: [runner-loop-absorption]
    acceptance:
      - Every produce and fix-round prompt the engine assembles carries the commit
        discipline block exactly once, regardless of unit body content.
      - Unit bodies no longer need a per-unit commit rule; existing bodies carrying one
        do not duplicate the instruction in the assembled prompt.
      - A prompt-assembly test asserts presence and single occurrence.
  - id: drain-liveness-status
    title: One authoritative drain liveness status verb
    kind: build
    state: todo
    priority: high
    summary: Engine-owned status command reporting every live drain across all configured
      transport sessions, sockets, agents, and orchestrate processes, as the sole
      sanctioned liveness evidence before any destructive action (kill, branch or
      worktree delete, merge of a drain-owned PR, resume). Motivating incident
      (freeflo 2026-07-11) - an operator session ran tmux ls on the default socket,
      concluded a live drain on the shared socket was dead, and merged and deleted its
      branch and worktree mid-review; a single transport or socket listing can never be
      complete evidence. Prior art is the vault's just
      freeflo-drain-status (scripts/freeflo_drain.py --status); the engine version
      generalises it - enumerate known socket dirs plus a per-run session registry the
      launcher writes, and report backend, server compatibility, session/agent, process,
      log path, and last-activity age per drain.
    deps: [runner-loop-absorption, herdr-kept-alive-dispatch]
    acceptance:
      - One command lists every live drain across tmux and herdr with backend, session or agent, process, socket, server version/protocol where applicable, log path, and last-activity age; empty output states the evidence checked.
      - The launcher registers each run (backend, socket, session or agent, pid, log) in durable state
        the status verb reads, so a drain is findable even if socket conventions
        change.
      - Operator docs state the rule - no destructive action without this command
        showing the owning drain dead.
---

# Woof Backlog

This is the forward work queue for the VaultForeman/Woof merge. It contains work to do. Historical stage epics are not retained here as a second plan; git history carries the old backlog.

The architecture target is `docs/architecture.md`. Decision records are in `docs/adr/`. The glossary is `docs/CONTEXT.md`.

## Commission reconciliation - 2026-07-06

The wave-5-onward tail below was reshaped from six deep-reasoning commissions (Fable-judged, adversarially reviewed by GLM, reconciled by Opus), plus an engine-agnostic correction. Provenance is in `~/Work/vault/records/personal/projects/vault-decomposition/commissions/`; each folder holds `ingestion-plan.md`, `plan-review.md`, and `plan-reconciliation.md`:

- `woof-vaultforeman-merge` -- split intake, rewrite the wave table, move conformance/eval off the pre-flight path.
- `vaultforeman-woof-absorption-boundary` -- carve deploy-aware behaviour out of `runner-loop-absorption`; executor sweep, schema freeze, and stability window on retirement.
- `woof-first-flight-cutover-gate` -- split `first-flight` into `flight-1` (disposable) and `flight-2` (guarded real-deploy).
- `woof-profile-a-worktree-merge-contract` -- Woof discovers and validates worktrees fail-closed; it never provisions.
- `woof-semantic-conflict-policy` -- shared-file sibling conflicts fail closed to a human gate, not semantic reapplication.
- `woof-vf-issue-1-fate` -- supersede VaultForeman issue #1 (deploy-aware merge) into Woof; VaultForeman held as interim fallback.

Engine-agnostic framing: a prod-deploying consumer is one either engine can run, so there is no consumer-specific migration. The residual is the engine-side `engine-neutral-consumer-policy` unit plus the general proving gate (`flight-1` and `flight-2`). Retirement triggers on Woof parity, proof, and no live VF-dependent run.

Live-state note: `intake-predecomposed` and `execution-shape-unification` are already delivered. The decided doc tranche is now applied: ADR-015 (Profile A worktree contract), ADR-016 (sibling-conflict fail-closed), the declarative ADR-014 rewrite (absorption, three bounded transition surfaces, schema-authority freeze, operator-set stability window), the architecture section 0/section 8 updates, the CONTEXT glossary additions, and the policy-schema worktree/deploy-timeout additions. `vaultforeman-fix-parity` is the sweep that re-baselines the absorption against a pinned VaultForeman engine-repo commit (97c180a, the carried-to-completion tip), not a moving HEAD; the pin advanced past the lifted 2026-07-09 freeze to pull in the post-freeze drain-entry fixes. The `gate-environment-channel` unit carries VaultForeman's gate-environment channel, which had no Woof twin.

## Operating Order

1. Hand-build schema unification and the safety-defect sweep.
2. Build the policy spine by hand, then drain policy-adjacent runner work in dependency order.
3. Hand-build the execution-shape and config-routing convergence so the kernel runs one `work_units[]` shape and one routing authority before any further runner logic is absorbed.
4. Drain the warm-session, cartography-continuity, and pre-decomposed intake units.
5. Absorb the runner loop, the deploy-aware merge coordinator, the Profile A worktree contract, the engine-neutral consumer policy, and the VaultForeman fix-parity sweep.
6. Adopt the backend-neutral retained-session transport after Agent Toolkit publishes it.
7. Run flight 1 (disposable repo), then flight 2 (guarded real-deploy slice), manually.
8. Drain the post-flight richness units: conformance audit, eval instrumentation, and epic-enrichment intake.
9. Retire the standalone VaultForeman runner once Woof is proven and no live run requires it.

## Wave Instructions

The `How` value controls execution mechanics:

- `hand-build` means the operator decomposes and implements the unit directly in the Woof checkout, using normal repo checks and commits. It is for contract/spine work that is too foundational to hand to the transitional runner.
- `vf-drain` means the unit is decomposed into a schema-valid `work_units[]` sub-backlog and run through `vf orchestrate`. The Woof VaultForeman run profile lives in the operator-home VaultForeman project config (`~/.vf/config/projects/woof.toml`); backlog front matter owns only executor, timeout, drain, and state-update policy. Do not run `vf orchestrate docs/backlog.md` while hand-build or manual wave units are still `todo`; drain from a wave sub-backlog or after the earlier units are marked done.
- `manual` means an operational proof, cutover, or retirement step where the operator owns sequencing and judgement. It may run tools, but it is not an unattended producer drain.

| Wave | Units | How | Instructions |
|---|---|---|---|
| 0 | Runner-asset source map | done | Source map is in `~/Work/vault/records/radianit/projects/woof/planning/runner-asset-source-map.md`. |
| 1 | `schema-unification`, `safety-defect-sweep` | hand-build | Start here. Preserve one canonical `work_units[]` schema, keep the VaultForeman `executor` document block valid for transitional drains, retire legacy runtime mirrors, and keep graph dependency validation fail-closed. |
| 2 | `policy-model`, `dispatch-swap`, `run-lineage-immutable-attempts` | hand-build + vf-drain | Hand-build the repo-local policy schema/spine first. In `dispatch-swap`, consolidate VaultForeman's harness/model/effort registry into Woof's dispatcher before any produce/review logic is absorbed. |
| 3 | `execution-shape-unification`, `config-routing-ssot` | hand-build | Foundational convergence. Collapse the runtime to one `work_units[]` shape (retire the `status`/`state` dual lifecycle and legacy id mirrors; rename checks, gates, and playbooks onto work-unit language) and make `policy.toml` the single routing/run-profile authority (retire `agents.toml` routing, single-source the registry vocab, delete dead headless builders). Hand-build because the vf-drain waves fold runner logic into this kernel; draining them first deepens the mirror. |
| 4 | `warm-session-seam`, `cartography-continuity`, `intake-predecomposed` | vf-drain | Drain after the kernel runs one shape and the dispatch registry is single-sourced, so warm producer and fresh reviewer sessions use one adapter contract and one unit shape. `intake-predecomposed` is already delivered. |
| 5 | `runner-loop-absorption`, `deploy-aware-merge-coordinator`, `profile-a-worktree-contract`, `engine-neutral-consumer-policy`, `operator-home-config-and-state`, `gate-environment-channel`, `publish-protected-content-guard`, `produce-prompt-commit-discipline`, `drain-liveness-status`, `vaultforeman-fix-parity` | hand-build + vf-drain | Absorb Profile A/B drain, review cache, and usage/run telemetry. Drain sub-backlogs in order: `docs/backlogs/wave-5-shakedown.md` (`profile-a-worktree-contract` shakedown first), then `docs/backlogs/wave-5.md` (the decomposed absorption). `deploy-aware-merge-coordinator` is a native new-build (no VaultForeman source asset) and is hand-build with operator review, not an unattended vf-drain. `operator-home-config-and-state` implements ADR-017 (engine config/state under `~/.woof`; no engine files in driven repos) and must land before the flights so the proven engine is the operator-home one. `gate-environment-channel` carries VaultForeman's gate-environment channel (unit context, declared targets, operator pre-authorisation) and is the pre-authorisation seam `publish-protected-content-guard` reads. `vaultforeman-fix-parity` re-baselines the absorption against the pinned VaultForeman engine-repo commit 97c180a (the carried-to-completion tip, not a moving HEAD); decompose it after its deps land. Shared-file sibling conflicts fail closed to a human gate. Producer reads the runner-asset source map. |
| 6 | `herdr-kept-alive-dispatch` | hand-build | Consume Agent Toolkit's backend-neutral retained-session seam, remove tmux-only public shapes, and prove herdr plus explicit tmux fallback before a flight. |
| 7 | `flight-1`, `flight-2` | manual | Flight 1 proves the kernel on a disposable repo with deploy decoupled. Flight 2 proves a guarded real-deploy slice and is the go/no-go for trusting Woof with prod-deploying consumers. Operator run-sheets: `docs/flight/flight-1-induction.md`, `docs/flight/flight-2-audit-evidence.md`. |
| 8 | `conformance-audit`, `eval-instrumentation`, `intake-epic-enrichment` | vf-drain | Post-flight richness. Structural cartography is the deferred structural scope of `cartography-continuity`, not a separate unit. |
| 9 | `vaultforeman-retirement` | manual | Retire standalone VaultForeman once Woof is proven, carries the engine-neutral consumer policy, and no live run requires the VaultForeman path. |

Same-day requirements are placed as follows: project-owned producer/reviewer run profiles are in `policy-model` and preserved by `runner-loop-absorption`; deploy-aware Profile A merge and partial-merge reconciliation are in `deploy-aware-merge-coordinator`, exercised by `flight-1`; shared-file sibling conflicts fail closed to a human gate in `runner-loop-absorption`; the dispatch registry mismatch is an explicit `dispatch-swap` prerequisite before the warm-session and runner-loop waves; backend-neutral retained sessions land in `herdr-kept-alive-dispatch` before either flight.

## Notes

Cartography is retained as a first-class capability. The policy floor decides what is required for a repo and run; it does not create a second engine path.

Pre-authored `work_units[]` are already decomposed input. They skip epic decomposition but run through the same execution kernel.

A prod-deploying consumer is engine-agnostic: it declares its delivery policy once and either engine can drain it, so the engine is a per-run selection, not a migration the consumer undergoes. Two operational rules follow: never point an unproven engine at a prod-deploying repo (that is what flight 2 clears), and never run two engines against the same repo at once.

Single source of truth is a principal rule (architecture section 1). Each concept has one authoritative home and one bounded scope: routing and run profiles in `policy.toml`, one `work_units[]` schema for the executable unit, harness/model/effort vocabulary in the dispatch registry. `execution-shape-unification` and `config-routing-ssot` bring the runtime up to this rule; everything downstream must hold it.

Work-unit identity is local to its aggregate. Cross-aggregate references are structured (aggregate context plus local id), never an encoded string; UUIDs identify technical run, attempt, review, and audit records. The work-unit aggregate owns identity, dependency closure, acyclicity, and topological order, and deps are intra-aggregate. Every consumer of the canonical schema -- pm-structure, vault overlays, and vf-drain sub-backlog generators -- must hold these invariants and emit topologically-ordered units.
