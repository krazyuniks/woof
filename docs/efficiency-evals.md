# Efficiency Eval Workflow

This runbook is the operator path for EF-007 small-valid-epic evals. It exists
so efficiency work starts from repeatable measurement before prompt, model, or
graph changes.

## What The Eval Measures

The default scenario starts from
`examples/efficiency/small-valid-epic/EPIC.md`, a schema-valid epic contract.
It deliberately does not measure brainstorm or discovery quality. It measures
how Woof handles an already-valid epic through deterministic Definition
validation, Breakdown, plan critique, the plan gate, story execution, checks,
commit, and manifest collection.

Each variant runs in a fresh consumer worktree and branch from the same
`consumer_base_sha`. The harness writes a redacted JSON manifest per variant
and can write a Markdown comparison table.

## Pre-Run Checklist

Do these before live model evals:

- Commit or intentionally label the Woof harness state. A clean Woof checkout
  gives meaningful `woof_sha` and `woof_dirty: false` fields.
- Pick the consumer repo and confirm it is clean.
- Freeze the consumer base SHA with `git -C "$CONSUMER_REPO" rev-parse HEAD`.
- Use clean Woof checkouts/worktrees for every variant being compared.
- Decide whether the eval uses consumer-owned `.woof` config or a fixed
  `--config-dir`. Do not hand-edit config between variants.
- Run one stubbed rehearsal before live spend.
- Decide the quality rubric before looking at token numbers.

Minimum quality pass:

- final state is `epic_complete`, or an expected gate opens;
- quality checks pass, or the expected gate/check failure is recorded;
- result diff stays inside declared story scope;
- reviewer severity is not `blocker` unless that is the expected outcome;
- `consumer_result_sha` is present when the story is expected to commit.

Token or command-count reductions only count after the quality pass.

## Variables

Use these variables in a shell session from the Woof checkout:

```zsh
export WOOF_REPO=/home/ryan/Work/woof
export CONSUMER_REPO=/path/to/consumer
export RUN_ID=$(date -u +%Y%m%dT%H%M%SZ)
export CONSUMER_BASE_SHA=$(git -C "$CONSUMER_REPO" rev-parse HEAD)
export OUT_DIR="$WOOF_REPO/docs/efficiency-runs/$RUN_ID-small-valid-epic"
export WORKTREE_PARENT="/tmp/woof-efficiency-worktrees/$RUN_ID"
```

For real comparisons, set one clean Woof checkout per variant:

```zsh
export BASELINE_WOOF=/path/to/baseline-woof
export CANDIDATE_WOOF=/path/to/candidate-woof
```

Model choices come from `.woof/agents.toml`, not prompt text. A config can keep
routes stable and put switchable choices in profiles:

```toml
model_profile = "default"

[roles.primary]
adapter = "codex"

[roles.reviewer]
adapter = "claude"
mcp = []

[model_profiles.default.roles.primary]
model = "gpt-5.5"
effort = "xhigh"

[model_profiles.default.roles.reviewer]
model = "claude-opus-4-7"
effort = "max"

[model_profiles.smoke.roles.primary]
model = "<replace-codex-model>"
effort = "low"

[model_profiles.smoke.roles.reviewer]
model = "<replace-claude-model>"
effort = "low"
```

Use `--model-profile <name>` to force one profile for all variants, or
`--variant-model-profile baseline=default --variant-model-profile candidate=smoke`
to compare profiles in one run. Only pass a profile that exists in the copied or
consumer-owned `.woof/agents.toml`; old direct-role configs continue to work
without any profile flag. The selected profile is also available to normal Woof
commands as `WOOF_MODEL_PROFILE=<name>`.

## Stubbed Rehearsal

Run this first. It proves worktree isolation, seeding, manifest aggregation, and
comparison rendering without live model spend.

```zsh
cd "$WOOF_REPO" && mkdir -p "$OUT_DIR" "$WORKTREE_PARENT" && just efficiency-bench run --consumer-repo "$CONSUMER_REPO" --consumer-base "$CONSUMER_BASE_SHA" --epic-fixture examples/efficiency/small-valid-epic/EPIC.md --variant baseline="$WOOF_REPO/bin/woof" --variant candidate="$WOOF_REPO/bin/woof" --variant-repo baseline="$WOOF_REPO" --variant-repo candidate="$WOOF_REPO" --output-dir "$OUT_DIR/stub" --worktree-parent "$WORKTREE_PARENT/stub" --run-id "$RUN_ID-stub" --stub-models --compare
```

Accept the rehearsal only if both manifests report `quality_outcome.status` as
`passed`, `final_state.last_status` as `epic_complete`, and a non-null
`git.consumer_result_sha`.

## Live Baseline Smoke

Run a single live baseline before comparing variants. This catches login,
model-route, quality-gate, and consumer-base problems before spending on
multiple variants.

```zsh
cd "$WOOF_REPO" && mkdir -p "$OUT_DIR/live-baseline" "$WORKTREE_PARENT/live-baseline" && just efficiency-bench run --consumer-repo "$CONSUMER_REPO" --consumer-base "$CONSUMER_BASE_SHA" --epic-fixture examples/efficiency/small-valid-epic/EPIC.md --variant baseline="$BASELINE_WOOF/bin/woof" --variant-repo baseline="$BASELINE_WOOF" --output-dir "$OUT_DIR/live-baseline" --worktree-parent "$WORKTREE_PARENT/live-baseline" --run-id "$RUN_ID-live-baseline" --compare
```

Review the manifest before running candidate comparisons:

```zsh
python -m json.tool "$OUT_DIR"/live-baseline/*.json | sed -n '1,220p'
```

## Live Variant Comparison

Run this after the live baseline smoke is acceptable.

```zsh
cd "$WOOF_REPO" && mkdir -p "$OUT_DIR/live" "$WORKTREE_PARENT/live" && just efficiency-bench run --consumer-repo "$CONSUMER_REPO" --consumer-base "$CONSUMER_BASE_SHA" --epic-fixture examples/efficiency/small-valid-epic/EPIC.md --variant baseline="$BASELINE_WOOF/bin/woof" --variant candidate="$CANDIDATE_WOOF/bin/woof" --variant-repo baseline="$BASELINE_WOOF" --variant-repo candidate="$CANDIDATE_WOOF" --output-dir "$OUT_DIR/live" --worktree-parent "$WORKTREE_PARENT/live" --run-id "$RUN_ID-live" --compare
```

The comparison table is a fast scan only. The JSON manifests are the durable
record.

## Manifest Review

Check these fields in every manifest:

- `variant.woof_sha`, `variant.woof_dirty`, and `variant.model_profile`;
- `git.consumer_base_sha` and `git.consumer_result_sha`;
- `route_policy.dispatch_routes`;
- `node_sequence`;
- `final_state`;
- `gates`;
- `checks`;
- `story_statuses`;
- `dispatch.tokens`;
- `dispatch.events` and `dispatch.by_route`;
- `dispatch.telemetry`;
- `timing`;
- `diff.pathscope`;
- `quality_outcome`.

Do not commit raw `.woof/epics/E*/audit/` output from consumer worktrees.
Commit only redacted manifests and comparison tables when the run is part of
the evidence trail.

## Execution Prompt

Use this prompt when starting an eval execution session:

```text
We are in /home/ryan/Work/woof. Read AGENTS.md first.

Goal:
Run the EF-007 small-valid-epic efficiency eval workflow without changing Woof
code, prompts, model policy, or consumer source outside throwaway worktrees.

Inputs:
- Consumer repo: <absolute path>
- Consumer base ref/SHA: <ref or SHA>
- Baseline Woof checkout: <absolute path>
- Candidate Woof checkout: <absolute path, optional for baseline smoke>
- Model profile: <profile name, optional>
- Output directory: docs/efficiency-runs/<run-id>-small-valid-epic
- Worktree parent: /tmp/woof-efficiency-worktrees/<run-id>

Procedure:
1. Confirm all Woof and consumer checkouts are clean, or stop and report the
   dirty paths.
2. Resolve and record `consumer_base_sha` and each variant's `woof_sha`.
3. Run the stubbed rehearsal with `--stub-models --compare`.
4. Inspect manifests for `passed`, `epic_complete`, non-null
   `consumer_result_sha`, expected route/model profile policy, no pathscope
   failures, and sane dispatch totals.
5. Run one live baseline smoke.
6. If the live baseline passes the quality rubric, run the live baseline versus
   candidate comparison.
7. Summarise manifest paths, comparison table path, quality outcome, token and
   command-count totals, and any gates/check failures. Do not judge efficiency
   wins unless quality passed.

Validation:
- Do not leave benchmark worktrees running or dirty.
- Do not commit raw audit output.
- Run `git status --short` in the Woof checkout before final response.
```
