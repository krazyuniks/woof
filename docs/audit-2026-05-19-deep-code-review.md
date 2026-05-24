# Archived Deep Code Review - 2026-05-19

> **Archive status:** This file is retained as historical engineering evidence.
> It is not current product guidance, not a release checklist, and not a live
> backlog. Current behaviour is specified by `docs/architecture.md`, the ADRs,
> schemas, source code, and tests.

This is a read-only audit. No source file was modified. It reads end-to-end the
eight load-bearing files the 2026-05-19 second-pass audit (see
`docs/implementation-plan.md`, "Audit Reconciliation - 2026-05-19 Second Pass")
read only by spot-check, checks them against `docs/architecture.md` and the
three ADRs, and records gaps and refactor opportunities.

## Scope note

The audit prompt listed file 5 as `src/woof/cli/github.py` (~1192 lines). That
file no longer exists: RC-B2 (issue-tracker abstraction, ADR-003) removed
`cli/github.py` and split it into the `src/woof/trackers/` package. This audit
reads its successor `src/woof/trackers/github.py` (725 lines) end-to-end and
reads `trackers/base.py`, `trackers/local.py`, `trackers/__init__.py`,
`graph/git.py`, `graph/manifest.py`, and `graph/lock.py` as supporting context.

## Quantitative summary

- Files audited end-to-end: 8 (`graph/nodes.py`, `cli/dispatcher.py`,
  `graph/dispositions.py`, `gate/write.py`, `trackers/github.py`,
  `graph/transitions.py`, `graph/runner.py`, `graph/state.py`).
- Total LOC of audited files: 4371 (nodes 1901, dispatcher 667, github 725,
  transitions 364, write 306, dispositions 237, state 119, runner 52).
- Supporting files read for cross-reference: 6 (base 168, epic_body via size
  only, local 143, trackers/__init__ 85, git 81, manifest 67, lock 143).
- Hidden gaps: 12 - critical 0, high 1, medium 4, low 7.
- Refactor opportunities (not gaps): 10.
- Cross-cutting analyses: 3 (dispatch lifecycle, gate semantics, tracker
  coupling depth).

Headline: the deterministic-graph architecture (ADR-001), semantic role routing
(ADR-002), and the tracker protocol (ADR-003) are implemented faithfully. No
finding contradicts an accepted ADR invariant. The highest-risk gap is DRH-001:
the Stage-5 story-executor prompt still embeds a Claude Code slash-command
invocation, the same non-portability class that RC-B1 fixed for Stage 1 but left
unfixed for Stage 5.

---

## File 1: src/woof/graph/runner.py (52 LOC)

### a) What it does

`run_graph(repo_root, epic_id, *, once, registry)` is the graph driver. It
acquires the per-epic lock (`epic_workflow_lock`), then loops: call
`transitions.next_node()` to derive the next `(NodeType, story_id)` from
filesystem state; if `None`, append a synthetic `EPIC_COMPLETE` `NodeOutput` and
return; otherwise look up the handler in the registry, call it with a
`NodeInput`, append the `NodeOutput`; stop when `once` is set or the node status
is `GATE_OPENED`, `HALTED`, or `EPIC_COMPLETE` (`runner.py:25-52`).

### b) Architecture conformance

Conforms to ADR-001: orchestration is a deterministic Python loop; nodes are
typed handlers; successor selection is `next_node()`, not a node or an LLM
(`runner.py:26`). The lock wraps the whole run (`runner.py:22`), matching
`docs/architecture.md:480` "Concurrency lockfile".

### c) Internal consistency

The epic-complete `NodeOutput` reuses `NodeType.HUMAN_REVIEW` as its
`node_type` (`runner.py:30`) because there is no `EPIC_COMPLETE` node type; the
`status` field carries the real meaning. Harmless but slightly misleading.

### d) Hidden gaps

- **DRH-005 (medium): an uncaught `StageStateError` from `next_node()` crashes
  `run_graph` with a traceback instead of opening a gate.** `runner.py:26` calls
  `next_node()` with no `try`. `next_node()` calls `load_plan()`
  (`transitions.py:297`), which raises `StageStateError` on a missing or
  malformed `plan.json` (`transitions.py:88-95`). `next_node()` also raises
  `StageStateError` directly at `transitions.py:292` and `:323`. Any of these
  propagates out of `run_graph` as a Python traceback. This is inconsistent with
  how the graph treats malformed *story* artefacts: `gate_open_node` catches
  malformed `executor_result.json` / `check-result.json` and writes an
  `incomplete_stage_state` gate (`nodes.py:1775-1796`). A malformed `plan.json`
  deserves the same gated treatment. `docs/architecture.md:474` states that
  missing or malformed graph-owned handoff artefacts should use
  `triggered_by: ["incomplete_stage_state"]`; the `plan.json` path does not.
  Pointer: new workstream.

- **DRH (folded into refactor REF-6): `NodeOutput.next_node` is never read.**
  Every producer node sets `next_node=NodeType.X` on its output, but `run_graph`
  never reads `out.next_node` - it only inspects `out.status` (`runner.py:47`).
  See REF-6.

### e) Refactor opportunities

- The `registry` parameter is typed `dict | None` and `handlers` is typed bare
  `dict` (`runner.py:18,23`); both should be `dict[NodeType, NodeHandler]`.
- `handlers[node_type]` (`runner.py:37`) will `KeyError` for any `NodeType` not
  in a caller-supplied partial registry. Low impact (the default registry is
  complete for every `NodeType` `next_node` can return), but a `.get()` with an
  explicit error would fail more legibly.

---

## File 2: src/woof/graph/state.py (119 LOC)

### a) What it does

Typed graph contracts: the `NodeType` StrEnum (17 members), `NodeStatus`
StrEnum (4 members), the `GateDecision` `Literal` (10 values), and the Pydantic
boundary models `StorySpec`, `Plan`, `NodeInput`, `ValidationSummary`,
`NodeOutput`, `TransactionManifest`, `ManifestVerification`.

### b) Architecture conformance

`GateDecision` (`state.py:39-50`) carries the seven ADR-001 decisions
(`approve`, `revise_epic_contract`, `revise_plan`, `revise_story_scope`,
`split_story`, `abandon_story`, `abandon_epic`) plus the three ADR-003 conflict
decisions (`keep_local`, `accept_remote`, `hand_merge`). Conformant with both
ADRs. `epic_id` is `int` across `Plan`, `NodeInput`, `TransactionManifest`
- consistent with ADR-003's decision to keep integer IDs and defer string IDs.

### c) Internal consistency

`NodeType` declares 17 members but `nodes.default_registry()` maps only 15. See
DRH-007.

### d) Hidden gaps

- **DRH-007 (low): `NodeType.PLAN_GATE_RESOLVE` and `NodeType.GATE_RESOLVE` are
  orphan enum members.** They are declared (`state.py:21,28`) but absent from
  `default_registry()` (`nodes.py:1884-1901`) and never returned by
  `next_node()` (`transitions.py:270-364`). Gate resolution is handled by the
  CLI `woof wf --resolve` path, not as a graph node, so these two `NodeType`
  values are dead. If a future change ever routes to one, `runner.py:37` raises
  `KeyError`. Either implement them as real nodes or remove them from the enum.
  Pointer: refactor or a new low-priority workstream.

### e) Refactor opportunities

- `NodeInput` carries `reason` and `decision` fields (`state.py:78-79`) that
  `run_graph` never populates (`runner.py:38-45`). `decision` has no reader at
  all in the audited set; `reason` is read only by `gate_open_node` (see
  DRH-008). Both look like a richer node-input contract that was designed and
  not wired. Either wire them or drop them.
- `StorySpec.empty_diff` (`state.py:64`) is not consulted in any audited file;
  `docs/architecture.md:460` says Stage-5 Check 7 honours it, so the reader is
  in `checks/` (out of scope). Noted only so a future reader does not assume it
  is dead.

---

## File 3: src/woof/graph/transitions.py (364 LOC)

### a) What it does

The deterministic transition table. `next_node(repo_root, epic_id)` maps
filesystem state to `(NodeType | None, story_id | None)` (`transitions.py:270-364`).
Also: epic.jsonl helpers (`append_epic_event`, `epic_event_exists`,
`iter_epic_events`, `append_epic_event_once`), plan helpers (`load_plan`,
`write_plan`, `story_by_id`, `next_ready_story`, `mark_story_status`),
gate-resolution state readers (`plan_gate_resolved`,
`definition_revision_requested`), and crash-resume detection
(`_resumable_commit_story`, `_has_uncommitted_commit_work`).

### b) Architecture conformance

Conforms to ADR-001: `next_node` is a pure function of filesystem state. It
checks `gate.md` first and returns `HUMAN_REVIEW` while a gate is open
(`transitions.py:274-275`), matching `docs/architecture.md:98`. The mandatory
plan gate is enforced: while `plan_gate_resolved` is false the function keeps
returning `PLAN_GATE_OPEN` (`transitions.py:314-318`), matching
`docs/architecture.md:144`. Crash-resume via `_resumable_commit_story` matches
`docs/architecture.md:454`.

### c) Internal consistency

`plan_gate_resolved` (`transitions.py:176-195`) handles two event shapes: a
`plan_gate_resolved` event and a `gate_resolved` event with
`gate_type == "plan_gate"`. `write_gate` only ever emits `*_gate_opened` events
(`gate/write.py:209-214`), never `plan_gate_resolved`, so the
`plan_gate_resolved`-event branch (`transitions.py:181-183`) reads an event
spelling no audited writer produces. It is most likely a legacy spelling kept
for old logs; flagged as possible dead branch.

The discovery-bucket order is encoded here as both `DISCOVERY_BUCKETS`
(`transitions.py:51`) and `_DISCOVERY_BUCKET_NODES` (`transitions.py:53-57`),
and again in `nodes.py` (see REF-4).

### d) Hidden gaps

- **DRH-002 (medium): `_has_uncommitted_commit_work` swallows git errors and
  can cause a silent lost commit.** `_has_uncommitted_commit_work`
  (`transitions.py:228-236`) wraps `build_story_manifest`, `changed_paths`, and
  `staged_paths` in `except (subprocess.CalledProcessError, ValueError): return
  False`. `_resumable_commit_story` then treats `False` as "no work to resume",
  deletes `executor_result.json` and `check-result.json`, and returns `None`
  (`transitions.py:263-266`). Sequence of harm: `commit_node` marks the story
  `done` and emits `story_completed` before the git commit (`nodes.py:1673-1685`,
  by design for crash-resume per `docs/architecture.md:454`); if the process
  dies in that window and the *next* run hits a transient git failure during
  `_has_uncommitted_commit_work`, the resume artefacts are deleted, the story
  stays `done`, the commit never happens, and `next_node` proceeds (or returns
  epic-complete) as if the story committed. Likelihood is low - it needs a git
  command to fail in a narrow window - but the failure is silent and the lost
  commit is unrecoverable from graph state. A transient git failure during
  resume detection should fail loud (or open a gate), never discard resume
  artefacts. Pointer: new workstream; relates to RC-3 silent-failure findings.

- **DRH-005 (medium):** `load_plan` raising `StageStateError` is the root of the
  `run_graph` traceback path described under File 1. `load_plan`
  (`transitions.py:88-95`) is also called un-guarded by `commit_node`
  (`nodes.py:1601`) and the tracker (`github.py:466-471`, which does convert it
  to `TrackerError`). Pointer: new workstream.

### e) Refactor opportunities

- `mark_story_status` (`transitions.py:122-132`) round-trips every story through
  `model_dump()` then `StorySpec.model_validate()` to change one field; a direct
  copy with `model_copy(update=...)` is clearer and cheaper.
- `append_epic_event` (`transitions.py:135-139`) is one of four near-identical
  JSONL append helpers (REF-1).
- `epic_dir` (`transitions.py:26`) duplicates `trackers/base.epic_directory`
  (REF-3).

---

## File 4: src/woof/graph/dispositions.py (237 LOC)

### a) What it does

Reviewer-disposition helpers for Stage 5. Markdown front-matter parsing
(`read_markdown_front_matter`), severity and finding extraction
(`critique_severity`, `critique_findings`), `validate_story_disposition` and
`validate_disposition_front_matter` (assert the primary's disposition file
covers every non-blocking critique finding exactly once), and
`reviewer_blocker_gate_body` (composes the gate body for a blocker critique).

### b) Architecture conformance

Conforms to ADR-002: `info` / `minor` findings require a primary disposition;
`blocker` opens a human gate and must not carry a primary disposition
(`dispositions.py:91-97`). `DISPOSITION_DECISIONS = {accepted, rejected,
deferred}` (`dispositions.py:13`) matches `docs/architecture.md:444`. The
blocker gate body explicitly states "Woof does not start a model-to-model debate
loop" (`dispositions.py:230`), matching ADR-002's no-debate-loop invariant.

### c) Internal consistency

Consistent. `validate_disposition_front_matter` correctly cross-checks
`target`, `target_id`, `critique_path`, `severity`, `timestamp`, `harness`, and
that `dispositions[]` covers exactly the set of non-blocking finding IDs with no
duplicates and no unknown IDs (`dispositions.py:144-197`). The four-section
gate body in `reviewer_blocker_gate_body` matches the gate.md section contract.

### d) Hidden gaps

None. This is the cleanest of the eight files.

### e) Refactor opportunities

- `validate_disposition_front_matter` requires a `harness` field on the
  disposition front-matter (`dispositions.py:158-159`). ADR-002 renamed
  `harness` to `adapter` everywhere else; the disposition artefact (and its
  schema) still uses the pre-ADR-002 term. Naming drift, not a bug; align when
  the disposition schema is next revised.

---

## File 5: src/woof/trackers/github.py (725 LOC)

### a) What it does

`GitHubTracker`, the GitHub-issue-backed `Tracker` adapter: one issue per epic,
`E<N>` is issue `#<N>`. `assert_runtime_reachable` (`gh api /rate_limit` with a
rate-remaining safety margin), `create_epic`, `fetch_epic`,
`assert_epic_authority`, `has_sync_state`, `push_epic_definition`,
`push_plan_summary`, `complete_epic`, `resolve_conflict`. Conflict detection is
intrinsic to push: a push that finds the remote diverged from the `.last-sync`
baseline writes a `tracker_sync_conflict` gate and raises `TrackerError`
(`github.py:542-584`).

### b) Architecture conformance

Conforms to ADR-003 and `docs/architecture.md:178-254`. Conflict detection is
intrinsic to push, not a standalone `detect_conflict` (ADR-003 "Protocol").
`.last-sync` records `issue_number`, `updated_at`, `body_sha256`, and the full
`body` (`github.py:197-205`); conflict compares `updated_at` and `body_sha256`
(`github.py:555-563`); the gate body shows last-pushed, remote, and local
renders with two unified diffs (`github.py:641-691`), satisfying the
"three-way diff" requirement of `docs/architecture.md:215`. Emits the canonical
`tracker_synced` / `tracker_sync_conflict` event spellings (`github.py:209,
288, 525, 625`). `complete_epic` refuses to close until all stories are `done`
(`github.py:239-240`), matching `docs/architecture.md:212`.

### c) Internal consistency

Consistent. `_raise_if_sync_conflict` and the no-change short-circuit in
`push_epic_definition` (`github.py:181-192`) and `_sync_lifecycle_body`
(`github.py:496-501`) agree on the `updated_at` + `body_sha256` comparison.
`resolve_conflict` rejects unknown decisions (`github.py:254`) consistently with
the `CONFLICT_DECISIONS` constant in `trackers/base.py:27`.

### d) Hidden gaps

- **DRH-009 (low): `gh` subprocess calls have no timeout except
  `assert_runtime_reachable`.** `assert_runtime_reachable` passes `timeout=20`
  (`github.py:79`), but `_fetch_issue`, `_create_issue`, `_edit_issue_body`, and
  `_close_issue` (`github.py:305-393`) call `subprocess.run` with no `timeout`.
  `docs/architecture.md:250` promises fail-loud on push-sync network failure; a
  stalled (not failed) `gh` call hangs the workflow indefinitely with no
  fail-loud and no gate. Add a timeout to every `gh` invocation. Pointer: new
  workstream.

- **DRH-012 (low): `create_epic` creates the remote issue before the local-init
  guard, so a local-init failure orphans the GitHub issue.** `create_epic`
  calls `_create_issue` (`github.py:105`) first, then
  `_initialise_epic_from_payload`, which raises `TrackerError` if `epic_dir`
  already exists (`github.py:412-414`). On that raise the GitHub issue has
  already been created and is now an orphan with no local epic. `create_epic` is
  the new-epic path so `epic_dir` should not pre-exist, but the failure mode is
  unguarded. Pointer: new workstream or refactor.

### e) Refactor opportunities

- `append_jsonl`, `iso_utc`, and `epic_directory` are imported from
  `trackers/base.py`, which re-implements helpers that also exist in
  `gate/write.py` and `graph/transitions.py` (REF-1, REF-2, REF-3).
- `trackers/github.py` imports `write_gate` from `gate/write.py` and `Plan`
  from `graph/state.py` (`github.py:21-22`). The tracker layer therefore is not
  a leaf - it reaches into gate authoring and graph state. This is a deliberate
  consequence of "conflict detection is intrinsic to push" (the push writes its
  own gate), but it means `trackers/` cannot be reasoned about in isolation.
  Recorded as an architecture observation, not a defect.

---

## File 6: src/woof/gate/write.py (306 LOC)

### a) What it does

Mechanical `gate.md` authoring. `write_gate` derives the YAML front-matter
(`type`, `stage`, `story_id`, `triggered_by`, `timestamp`, optional
`exit_code`), composes the four-section body, validates the front-matter via
`ajv`, writes `gate.md`, and appends the `*_gate_opened` event to `epic.jsonl`
(`gate/write.py:68-129`). `write_gate_from_check_result` and
`write_gate_for_trigger` are the two convenience wrappers. Helper functions map
triggers to gate type, stage, and opened-event name, and synthesise position
prose when none is supplied.

### b) Architecture conformance

Conforms to `docs/architecture.md:100-115`: the docstring states "No LLM authors
any YAML field" and every front-matter field is derived deterministically
(`gate/write.py:1-5, 88-98`). The four required sections (`## Context`,
`## Findings`, `## Primary position`, `## Reviewer position`) are enforced by
`_ensure_gate_sections` (`gate/write.py:217-247`).

### c) Internal consistency

On schema-validation failure `write_gate` unlinks the just-written `gate.md` and
raises `ValueError` *before* appending the `gate_opened` event
(`gate/write.py:110-127`); there is therefore no state where a failed gate
leaves an orphan `gate_opened` event. Good.

`_gate_type_for_triggers` (`gate/write.py:191-198`) hardcodes the literals
`"tracker_sync_conflict"` and `"github_sync_conflict"`, and
`_auto_position_for_trigger` hardcodes the same pair (`gate/write.py:291`).
`graph/transitions.py:19` imports the `CONFLICT_TRIGGERS` constant from
`trackers/base.py` for exactly this purpose; `gate/write.py` does not. See REF-5.

### d) Hidden gaps

- **DRH-008 (low): `write_gate`'s `ValueError` on schema failure is uncaught by
  node callers.** `plan_gate_open_node` calls `write_gate` directly
  (`nodes.py:1309`); the trigger wrappers are called un-guarded by
  `executor_dispatch_node`, `critique_dispatch_node`, `commit_node`, and others.
  A schema-invalid gate therefore propagates a `ValueError` as a `run_graph`
  traceback. Because gate authoring is fully deterministic, a schema failure is
  a Woof bug rather than operator-recoverable state, so a loud crash is
  defensible - but it is the same un-gated-exception class as DRH-005 and should
  be resolved consistently. Pointer: fold into the DRH-005 workstream.

The epic-id reconstruction `int(epic_id_str.lstrip("E"))` (`gate/write.py:117`)
falls back to `epic_id = 0` when `epic_dir.name` does not start with `E`, which
would write a `gate_opened` event with `epic_id: 0`. In practice `epic_dir` is
always `E<N>`, so this is noted as latent fragility, not a live gap.

### e) Refactor opportunities

- REF-5: import `CONFLICT_TRIGGERS` from `trackers/base.py` instead of
  hardcoding the conflict-trigger literals twice.
- `_append_jsonl` (`gate/write.py:22-25`) and `iso_utc` (`gate/write.py:18-19`)
  are duplicates of helpers elsewhere (REF-1, REF-2).
- `_auto_position_for_trigger`'s final branch `elif trigger != "subprocess_crash"`
  (`gate/write.py:294`) is always true when reached (the `subprocess_crash` case
  is handled by the opening `if`); it should be a plain `else`.

---

## File 7: src/woof/cli/dispatcher.py (667 LOC)

### a) What it does

The dispatch adapter boundary. Role-route resolution
(`resolve_role_route` with legacy alias and fallback handling), per-invocation
effort validation, Claude MCP JSON rendering with portability guards, public
`claude` / `codex` argv construction (`build_argv`), the `woof dispatch` command
(`cmd_dispatch`), token-usage parsing for both CLIs, atomic audit-stem
reservation, and `subprocess_spawned` / `subprocess_returned` /
`subprocess_killed` event emission to `dispatch.jsonl`.

### b) Architecture conformance

Conforms to ADR-002. `build_argv` constructs the exact `claude` command shape
documented in ADR-002 lines 58-67: `--dangerously-skip-permissions
--strict-mcp-config --mcp-config <json> -p --output-format json --model ...
--effort ...` (`dispatcher.py:264-279`). `_normalise_mcp_server` rejects the
private local commands `cld`, `cod`, `agent-sync` and `/.dotfiles`-style host paths
(`dispatcher.py:193-211`), matching ADR-002's portability rule. `EFFORTS`
includes `max` and `CODEX_EFFORTS` excludes it (`dispatcher.py:27-28`), matching
ADR-002 "max is Claude-only". Legacy `cld`/`cod`/`planner`/`story-executor`/
`critiquer` aliases are accepted as migration input only (`dispatcher.py:29-38`).

### c) Internal consistency

Timeout and SIGINT handling are asymmetric. A timed-out dispatch
(`returncode == 124`) emits both `subprocess_killed` and `subprocess_returned`
(`dispatcher.py:592-635`); a `KeyboardInterrupt` emits `subprocess_killed` and
then returns `130` early (`dispatcher.py:561-578`), so it emits no
`subprocess_returned` and writes no `.meta` file. The audit pair
`subprocess_spawned` / `subprocess_returned` is therefore broken for a
SIGINT-cancelled dispatch. Defensible (a cancelled dispatch is terminal) but
worth a deliberate decision.

### d) Hidden gaps

- **DRH-004 (medium): the dispatched prompt is passed as a single `claude` /
  `codex` argv element and can exceed the Linux `MAX_ARG_STRLEN` ceiling.**
  `build_argv` appends the full prompt as the last argv element
  (`dispatcher.py:303`) and `cmd_dispatch` spawns it with
  `subprocess.Popen(wrapped_argv, ...)` (`dispatcher.py:535`). Linux caps a
  single argv string at `MAX_ARG_STRLEN` (128 KiB), independent of the larger
  total `ARG_MAX`. RC-B1 made the Stage-1 producer prompts embed their full
  building-block playbook set inline (8 research playbooks for
  `discovery_research`, 12 for `discovery_thinking`); as those playbooks grow,
  the rendered prompt approaches the 128 KiB per-arg ceiling and the dispatch
  fails with `OSError: [Errno 7] Argument list too long` at `Popen`. The failure
  is loud, not silent, but it is a hard scaling cliff with no headroom check.
  Woof already writes the prompt to a file for its own internal hop
  (`nodes.py:_run_dispatch` uses `--prompt-file`), but the final `claude` /
  `codex` exec still takes the prompt on argv. Fix: pass the prompt to
  `claude` / `codex` on stdin (both support it) rather than as an argument.
  Pointer: new workstream.

### e) Refactor opportunities

- `_role_effort` is called by both `cmd_dispatch` (`dispatcher.py:492`) and,
  internally, by `build_argv` (`dispatcher.py:262`); `_claude_mcp_config` is
  computed up to three times per dispatch (`dispatcher.py:270, 518, 659`).
  Resolve the route once into a fully-populated record.
- Every dispatch event writes both `harness` and `adapter` with the same value
  (`dispatcher.py:542-543, 611-612, 638-639`). `harness` is the pre-ADR-002
  spelling kept for old-log compatibility; document it as intentional or drop it
  from new events.
- `append_jsonl` and `iso_utc` (`dispatcher.py:65-72`) duplicate helpers
  elsewhere (REF-1, REF-2).

---

## File 8: src/woof/graph/nodes.py (1901 LOC)

### a) What it does

The node registry and all 15 node handlers, plus their prompt builders, payload
builders, artefact collectors, the `_run_dispatch` wrapper, the install-safe
re-entry helpers (`_woof_subprocess_argv`, `_woof_subprocess_env`), and the
`woof validate` subprocess wrappers. `default_registry()` (`nodes.py:1884-1901`)
maps `NodeType` to handler. The handlers cover Stage 1 (three discovery bucket
producers plus synthesis), Stage 2 definition, Stage 3 breakdown and plan
critique, Stage 4 plan gate, and Stage 5 (executor dispatch, critique dispatch,
review disposition, verification, commit, gate-open, human-review).

### b) Architecture conformance

Conforms to ADR-001: every LLM node is a pure producer - it builds a structured
payload, dispatches via `_run_dispatch`, and validates the declared output
against a schema or a contract checker; it never selects its own successor. The
`commit_node` transaction (`nodes.py:1598-1744`) computes a manifest, stages the
exact expected set, verifies the index, and only then commits - matching
ADR-001 point 6 and `docs/architecture.md:447`. The install-safe re-entry
helpers (`nodes.py:64-109`) implement RC-6 / `docs/architecture.md:472`
correctly.

### c) Internal consistency

`default_registry()` omits `PLAN_GATE_RESOLVE` and `GATE_RESOLVE` (see DRH-007).
The discovery-bucket maps `_DISCOVERY_BUCKET_NODE_TYPE` and
`_DISCOVERY_BUCKET_NEXT_NODE` (`nodes.py:235-244`) duplicate the ordering held
in `transitions.py` (REF-4). Prompt construction is inconsistent: most builders
use the brace-safe `_prompt_template` (`nodes.py:172-176`) but `_disposition_prompt`
uses `str.format` (see DRH-006), and `_plan_critique_prompt` injects a
`Graph-owned input` JSON block while `critique_dispatch_node` injects nothing
(see DRH-010).

### d) Hidden gaps

- **DRH-001 (high for portability): the Stage-5 story-executor prompt embeds a
  Claude Code slash-command invocation.** `_story_prompt` (`nodes.py:577-589`)
  produces a prompt that instructs the model to `Invoke /wf:execute-story with
  arguments "E{epic_id} {story_id}"` (`nodes.py:586`). `/wf:execute-story` is a
  Claude Code skill. Two problems. First, ADR-002 makes `codex` / `gpt-5.5` the
  *preferred* primary route (`docs/adr/002...md:39`), and `executor_dispatch_node`
  dispatches the story with `role="primary"` (`nodes.py:1346-1352`); Codex has
  no Claude Code slash commands, so the preferred primary route cannot execute
  this prompt as written. Second, even with a `claude` primary, a consumer
  repository does not carry Woof's `.claude/commands/` skills, so the slash
  command does not resolve there either. This is precisely the non-portability
  class that BHID-001 / RC-B1 fixed for Stage 1 by embedding playbook content
  inline - the fix was not applied to the Stage-5 executor prompt.
  `tests/integration/test_release_smoke.py` only probes the Stage-1 discovery
  prompts (`_discovery_bucket_prompt`, `_discovery_synthesis_prompt`); it never
  renders `_story_prompt`, so the portability gate does not catch this. Note:
  `playbooks/critique/story.md` and `playbooks/disposition/story.md` (read by
  `critique_dispatch_node` and `_disposition_prompt`) were not in audit scope
  and may carry the same assumption. Pointer: new workstream, sibling to
  BHID-001.

- **DRH-003 (medium for portability): `_commit_message` hardcodes the `feat(woof)`
  conventional-commit scope.** `_commit_message` returns
  `f"feat(woof): E{epic_id} {story_id} - {story_title}"` (`nodes.py:1594-1595`),
  used by `commit_node` (`nodes.py:1730`). In a consumer repository the story
  changes the consumer's code, not Woof; every story commit is mis-scoped as
  `feat(woof)`. The type `feat` is also fixed regardless of whether the story is
  a fix, refactor, or docs change. The scope should be derived from the consumer
  project or be configurable; the type should at least be overridable from
  `executor_result.json`. Pointer: new workstream.

- **DRH-006 (low/medium): `_disposition_prompt` uses `str.format` on playbook
  text.** `_disposition_prompt` does
  `template.format(epic_id=epic_id, story_id=story_id)` on the contents of
  `playbooks/disposition/story.md` (`nodes.py:618-620`). If that playbook ever
  contains a literal `{` or `}` (a JSON example, a code block), `str.format`
  raises `KeyError` or `ValueError` and the disposition dispatch crashes. Every
  other prompt builder uses `_prompt_template`, which does brace-safe
  `str.replace`. Switch `_disposition_prompt` to `_prompt_template`. Pointer:
  refactor or fold into the DRH-001 workstream.

- **DRH-008 (low):** see File 6 - `plan_gate_open_node` calls `write_gate`
  un-guarded (`nodes.py:1309`).

- **DRH-010 (low): `critique_dispatch_node` dispatches a context-free story
  critique prompt.** It reads `playbooks/critique/story.md` raw with no
  templating and no graph-injected story identity (`nodes.py:1383-1390`), unlike
  `plan_critique_node` which prepends a `Graph-owned input` JSON block
  (`nodes.py:471-480`). The reviewer must infer the target story from the
  `in_progress` status in `plan.json`. It works, but the inconsistency is a
  latent defect: a second concurrently `in_progress` story (which the graph
  should never create, but a corrupted `plan.json` could) would make the target
  ambiguous. Pointer: refactor.

- **DRH-011 (low): `commit_node` records completion before the commit exists.**
  `commit_node` calls `mark_story_status(... "done")` and emits `story_completed`
  (`nodes.py:1673-1685`) before `verify_staged_manifest` and `git commit`
  (`nodes.py:1688, 1735`). If `verify_staged_manifest` fails, the story is left
  `done` with a `story_completed` event and no commit until the operator
  resolves the gate and `commit` resumes. This is recoverable by design (the
  crash-resume contract depends on done-before-commit, per
  `docs/architecture.md:454`), but the audit trail briefly asserts a completion
  that has not happened. Noted for visibility; no change strictly required.

- **DRH-008 / `gate_open_node` `inp.reason` (low):** `gate_open_node` reads
  `inp.reason` for its trigger (`nodes.py:1750, 1760, 1766`), but `run_graph`
  constructs `NodeInput` without `reason` (`runner.py:38-45`), so `inp.reason`
  is always `None`. The `if not inp.story_id` branch (`nodes.py:1748`) is
  unreachable through the runner because `next_node` only returns `GATE_OPEN`
  with a story id, and the `trigger = "manual"` fallthrough at `nodes.py:1825`
  is likewise unreachable for the staged-but-no-check-result state because
  `next_node` routes that state to `VERIFICATION`. Dead branches; recorded as
  low-risk DRH-008-adjacent.

### e) Refactor opportunities

- REF-7: the per-stage `_X_payload` / `_X_artefacts` / `_X_prompt` triplets and
  the dispatch-then-validate-then-event body of each node are heavily
  repetitive across ~1900 lines. A small per-stage descriptor table plus a
  shared "dispatch producer, validate output, emit event, return" helper would
  remove most of the duplication.
- `_now` (`nodes.py:60-61`) is another timestamp helper duplicate (REF-2).
- REF-6: every producer node sets `NodeOutput.next_node`, which `run_graph`
  never reads.

---

## Cross-cutting analysis 1: dispatch lifecycle

Tracing one Stage-5 story from `run_graph` through commit:

1. `run_graph` acquires `epic_workflow_lock` - an `O_CREAT | O_EXCL` lockfile at
   `.woof/epics/E<N>/.wf.lock` (`lock.py:78-92`). A same-host stale lock (dead
   pid) is removed and a `wf_lock_stale_removed` event is appended to
   `epic.jsonl` (`lock.py:121-140`). A cross-host lock cannot be auto-cleared
   and raises `WorkflowLockError`.
2. `next_node` returns `(EXECUTOR_DISPATCH, story_id)`. `executor_dispatch_node`
   calls `mark_story_status("in_progress")` - **filesystem write 1**: atomic
   `plan.json` rewrite (`transitions.py:98-102`).
3. `_run_dispatch` writes the prompt to a temp file and runs
   `python -m woof dispatch ...` (`nodes.py:623-655`). Inside `cmd_dispatch`:
   reserve the audit stem via `O_CREAT | O_EXCL` `.prompt` file
   (`dispatcher.py:397-404`); **jsonl append 1**: `subprocess_spawned` to
   `dispatch.jsonl`; `Popen([timeout Nm claude|codex ...])`; on return write
   `.output` / `.stderr` / `.meta`; **jsonl append 2**: `subprocess_returned`
   (and `subprocess_killed` first if it timed out).
4. `next_node` re-derives `CRITIQUE_DISPATCH`, then `REVIEW_DISPOSITION`, then
   `VERIFICATION` - each producer dispatch repeats the spawn/return pair, so a
   normal story produces three `subprocess_spawned` / `subprocess_returned`
   pairs in `dispatch.jsonl`.
5. `verification_node` runs `python -m woof check stage-5 ...` as a subprocess
   (`nodes.py:1532-1548`) and writes `check-result.json` from its stdout. Note:
   `woof check` is not a `woof dispatch`, so it appends nothing to
   `dispatch.jsonl`.
6. `commit_node`: `prepare_commit_audit`, `build_story_manifest`,
   `mark_story_status("done")` - **plan.json write**; **jsonl append**:
   `story_completed` to `epic.jsonl`; `git add` the expected paths;
   `verify_staged_manifest`; **jsonl append**: `transaction_manifest_verified`;
   `git add epic.jsonl`; `git commit`; unlink the transient result files.
7. `epic_workflow_lock` releases the lockfile in its `finally`
   (`lock.py:142-143`).

Consistency observations:

- **Single-writer holds.** `dispatch.jsonl` is appended only by the in-process
  `cmd_dispatch` adapter; the spawned `claude` / `codex` child writes neither
  log. `epic.jsonl` inside a graph run is appended only by the lock-holding
  parent. This matches the RC-4 narrowing recorded in
  `docs/implementation-plan.md:409`.
- **Audit pair can break on cancel.** A SIGINT during dispatch emits
  `subprocess_killed` with no matching `subprocess_returned` (DRH, dispatcher
  section c). Audit-trail reconstruction must treat a lone
  `subprocess_spawned` + `subprocess_killed` as a complete (cancelled) record.
- **Completion precedes the commit.** `story_completed` and
  `transaction_manifest_verified` are appended before `git commit`; both are
  then committed in the same transaction because `epic.jsonl` is in the manifest
  `required_paths` (`manifest.py:34`) and is `git add`-ed at `nodes.py:1728`. A
  crash between the events and the commit is handled by `_resumable_commit_story`
  - except for the DRH-002 git-error window, which can discard the resume
  artefacts silently.
- **Tracker writes are outside this trace.** No audited graph file calls a
  tracker push; see cross-cutting analysis 3.

## Cross-cutting analysis 2: gate semantics

`gate.md` writers. The single physical writer is `gate/write.write_gate`. It is
reached directly by `plan_gate_open_node` (`nodes.py:1309`) and
`github._write_sync_conflict_gate` (`github.py:614`), and through the wrappers
`write_gate_for_trigger` and `write_gate_from_check_result` by
`executor_dispatch_node`, `critique_dispatch_node`, `review_disposition_node`,
`verification_node`, `commit_node`, `gate_open_node`, and the helpers
`_write_position_gate` / `_write_incomplete_stage_gate` /
`_write_disposition_incomplete_gate`.

`gate.md` deleters. Within the eight audited files the only `unlink` of
`gate.md` is `write_gate`'s own rollback on schema-validation failure
(`gate/write.py:113`), which also raises and emits no event. Gate *resolution*
deletion lives entirely in the un-audited `woof wf --resolve` CLI path.

Coexistence check. `write_gate` emits a `*_gate_opened` event only after a
successful write and validation (`gate/write.py:118-127`); it never emits
`gate_resolved`. The audited code therefore never produces a state where
`gate.md` and a `gate_resolved` event coexist. `next_node` checks `gate.md`
first and yields `HUMAN_REVIEW` while it exists (`transitions.py:274-275`), so
no other node runs against an open gate. If the external resolver were to delete
`gate.md` without emitting `gate_resolved`, `next_node` would re-derive
`PLAN_GATE_OPEN` (because `plan_gate_resolved` stays false) and
`plan_gate_open_node` would re-open the plan gate (`nodes.py:1307-1322`) - safe,
idempotent re-open rather than a bypass.

Plan gate is mandatory. While `plan_gate_resolved` is false, `next_node` returns
`PLAN_GATE_OPEN` and never reaches story execution (`transitions.py:311-326`).
`plan_gate_resolved` only becomes true on a `gate_resolved` event with
`gate_type == "plan_gate"`, `decision == "approve"`, and no conflict trigger
(`transitions.py:184-194`). The plan gate cannot be bypassed.

Structured decisions. The audited graph consumes only the plan-stage decisions:
`approve` (resolves), `revise_plan` and `revise_epic_contract` (un-resolve;
`revise_epic_contract` additionally re-enters Stage 2 via
`definition_revision_requested`, `transitions.py:198-210`). The tracker-conflict
decisions `keep_local` / `accept_remote` / `hand_merge` are consumed by
`GitHubTracker.resolve_conflict` (`github.py:253-301`). The remaining story-gate
decisions - `revise_story_scope`, `split_story`, `abandon_story`,
`abandon_epic` - are **not consumed in any audited file**; their deterministic
effects live in the un-audited `cli/` resolve path. This is a coverage boundary:
the audit can confirm plan-gate and conflict-gate decisions drive deterministic
state, but cannot confirm the story-gate decisions do.

One audit-fidelity edge: a crash between `gate.md` write (`gate/write.py:108`)
and the `gate_opened` append (`gate/write.py:127`) leaves an open gate with no
`gate_opened` event. `next_node` still halts at `HUMAN_REVIEW` on the file's
presence, so resolution is unaffected; only the event log is missing one entry.

## Cross-cutting analysis 3: tracker coupling depth

Purpose: inventory what GitHub-specific behaviour remains, to inform any future
third-tracker work.

State of the abstraction. RC-B2 / ADR-003 is implemented well. Of the eight
audited files, only `trackers/github.py` is GitHub-specific, and it is already
the adapter behind the `Tracker` protocol (`trackers/base.py:68-117`).
`graph/nodes.py`, `graph/runner.py`, `graph/state.py`, and
`graph/dispositions.py` contain no GitHub or tracker references at all.

Residual coupling that a non-GitHub adapter still meets:

- **Integer epic IDs are pervasive.** `NodeInput.epic_id`, `Plan.epic_id`,
  `TransactionManifest.epic_id` are `int` (`state.py:68, 75, 106`), and the
  epic directory is built as `f"E{epic_id}"` in at least three places
  (`transitions.py:27`, `trackers/base.py:129`, manifest path strings in
  `manifest.py:31-38`). A string-keyed tracker (Jira, Linear, Plane) cannot plug
  in without the schema-widening ADR-003 explicitly deferred. This is known and
  documented, not a new finding.
- **`gate/write.py` hardcodes tracker trigger literals.** `_gate_type_for_triggers`
  and `_auto_position_for_trigger` embed `"tracker_sync_conflict"` and
  `"github_sync_conflict"` as literals (`gate/write.py:196, 291`) instead of
  importing `CONFLICT_TRIGGERS` from `trackers/base.py` the way
  `transitions.py:19` does. A new adapter that introduced its own conflict
  trigger spelling would be invisible to `gate/write.py`. See REF-5.
- **`.last-sync` is a GitHub-only artefact in practice.** It is written and read
  only by `trackers/github.py` via the `trackers/base.py` helpers
  (`read_last_sync`, `write_last_sync`). `LocalTracker` never creates it:
  `has_sync_state` returns `True` unconditionally and `assert_epic_authority` is
  a no-op (`local.py:91-95`). The `.last-sync` schema in code is
  `{issue_number, updated_at, body_sha256, body}` (`github.py:197-205`); the
  `issue_number` key is GitHub-shaped and `assert_epic_authority` checks it
  (`github.py:149-153`). A future tracker would need a tracker-neutral
  `.last-sync` shape.
- **Tracker sync is invoked entirely outside the graph.** No audited graph file
  calls `push_epic_definition`, `push_plan_summary`, or `complete_epic`. The
  graph nodes emit lifecycle events (`definition_closed`, `breakdown_planned`,
  `story_completed`) but never push. The pushes must be driven by the un-audited
  `cli/wf.py`. Two consequences: (a) the audit cannot verify push timing or
  whether pushes run inside `epic_workflow_lock` - if a push appends
  `tracker_synced` to `epic.jsonl` outside the lock it is an unsynchronised
  writer, the residual surface RC-4 flagged; (b) there is no graph-side
  invariant that a `definition_closed` event is followed by a `tracker_synced`,
  so a CLI path that closed definition but skipped the push would leave the
  GitHub issue silently stale.

Event and schema names embedding `github`. The canonical spellings
`tracker_synced` and `tracker_sync_conflict` are emitted everywhere in the
audited code (`github.py:209, 288, 525, 625`). The legacy `github_sync_conflict`
spelling survives only as a backward-compatibility literal in `gate/write.py`
and inside the `CONFLICT_TRIGGERS` tuple in `trackers/base.py:26`; ADR-003
records this as intentional so pre-RC-B2 logs still validate. No audited file
emits a `github_*` event.

Summary for a future adapter: implement the `Tracker` protocol plus a `kind`
value and a `resolve_tracker` branch; the real friction is the deferred
string-ID schema widening and a tracker-neutral `.last-sync` shape, not the
graph or gate code.

---

## Refactor opportunities (not gaps)

- **REF-1:** four near-identical JSONL append helpers - `gate/write._append_jsonl`
  (`write.py:22`), `dispatcher.append_jsonl` (`dispatcher.py:69`),
  `transitions.append_epic_event` (`transitions.py:135`),
  `trackers/base.append_jsonl` (`base.py:132`). Consolidate into one.
- **REF-2:** five timestamp helpers - `gate/write.iso_utc` (`write.py:18`),
  `dispatcher.iso_utc` (`dispatcher.py:65`), `trackers/base.iso_utc`
  (`base.py:120`), `nodes._now` (`nodes.py:60`), `lock._now` (`lock.py:31`) -
  with three different signatures. Consolidate.
- **REF-3:** two epic-directory helpers - `transitions.epic_dir`
  (`transitions.py:26`) and `trackers/base.epic_directory` (`base.py:128`) -
  plus ad-hoc reconstruction in `gate/write.write_gate` (`write.py:116-117`).
- **REF-4:** the discovery-bucket order and node mapping are encoded three
  times: `transitions.DISCOVERY_BUCKETS` / `_DISCOVERY_BUCKET_NODES`
  (`transitions.py:51-57`) and `nodes._DISCOVERY_BUCKET_NODE_TYPE` /
  `_DISCOVERY_BUCKET_NEXT_NODE` (`nodes.py:235-244`).
- **REF-5:** `gate/write.py` should import `CONFLICT_TRIGGERS` from
  `trackers/base.py` instead of hardcoding the conflict-trigger literals
  (`write.py:196, 291`).
- **REF-6:** `NodeOutput.next_node` is set by every producer node and never read
  by `run_graph`. Drop the field or document it as advisory-only; as written it
  misleads a reader into thinking it drives routing.
- **REF-7:** `graph/nodes.py` is 1901 lines of largely repetitive per-stage
  `_X_payload` / `_X_artefacts` / `_X_prompt` triplets and dispatch-validate-emit
  bodies. A per-stage descriptor table plus one shared producer helper would cut
  it substantially.
- **REF-8:** `cmd_dispatch` recomputes `_role_effort` and `_claude_mcp_config`
  two to three times per invocation (`dispatcher.py:262, 270, 492, 518, 659`).
- **REF-9:** dispatch events dual-write `harness` and `adapter` with identical
  values (`dispatcher.py:542-543, 611-612`).
- **REF-10:** `mark_story_status` round-trips every story through
  `model_dump()` / `model_validate()` to set one field (`transitions.py:122-132`).

---

## Closing assessment

The graph topology, role routing, and tracker abstraction are implemented
faithfully to ADR-001, ADR-002, and ADR-003; no finding overturns an accepted
decision. The single high-priority gap is DRH-001 - Stage 5's `_story_prompt`
carries the Claude-Code-skill dependency that RC-B1 removed from Stage 1, and
the existing release smoke test does not exercise it. The four medium gaps
(DRH-002 silent resume-artefact loss, DRH-003 hardcoded commit scope, DRH-004
argv-length ceiling, DRH-005 un-gated `plan.json` crash) are each a bounded,
well-localised fix. The seven low gaps and ten refactors are quality-of-life.
A reasonable next step is one workstream covering DRH-001 and DRH-003 (Stage-5
prompt portability), one covering DRH-002 and DRH-005 (graph failure handling),
and one covering DRH-004 (dispatch via stdin).
