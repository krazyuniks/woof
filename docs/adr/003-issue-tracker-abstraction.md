---
type: adr
status: superseded by ADR-011
date: 2026-05-27
---

# ADR-003: Issue-Tracker Abstraction

Superseded by ADR-011 for execution-shape projection. Tracker integration remains relevant, but the active execution contract is `work_units[]`, not `plan.json` stories.

## Context

Woof keeps the epic-level contract in an issue tracker and the runtime workflow state in `.woof/epics/E<N>/`. Requiring every consumer to use GitHub would tie the graph to a hosted provider; a repository with no hosted tracker must still be able to run Woof.

## Decision

Woof depends on a `Tracker` protocol, not on a specific provider. The engine, operator surface, and gate code depend on the protocol; provider-specific behaviour stays in adapter implementations.

`src/woof/trackers/` owns the abstraction:

- `base.py` — `Tracker`, `TrackerError`, result records, conflict constants, shared filesystem and hash helpers.
- `epic_body.py` — tracker-agnostic transforms between `EPIC.md` front-matter and the managed tracker body.
- `github.py` — GitHub issue adapter.
- `local.py` — filesystem-only adapter.
- `__init__.py` — `resolve_tracker(repo_root)`, the factory that reads `[tracker]` from `.woof/prerequisites.toml`.

The protocol covers:

- `assert_runtime_reachable`;
- `create_epic`;
- `fetch_epic`;
- `assert_epic_authority`;
- `has_sync_state`;
- `push_epic_definition`;
- `push_plan_summary`;
- `complete_epic`;
- `resolve_conflict`.

Configuration lives in `.woof/prerequisites.toml`:

```toml
[tracker]
kind = "github"          # github | local
repo = "<owner>/<name>"  # required when kind = "github"
```

## Adapters

Two adapters ship:

- `github`: one GitHub issue per epic. `E<N>` is the GitHub issue number. Push is conflict-detected against `.woof/epics/E<N>/.last-sync`; every graph invocation verifies runtime reachability.
- `local`: filesystem-only. `.woof/epics/E<N>/` is the authority for an epic. Epic IDs are locally allocated integers. There is no remote, no `.last-sync`, and no sync-conflict gate.

Lifecycle push methods on both adapters render the same managed body shape from local `EPIC.md` and `plan.json`. The adapters reject epic completion until every planned work unit is terminal.

## Epic identifiers

An epic ID is a tracker-assigned integer. For `github` it is the GitHub issue number; for `local` it is the next integer allocated under `.woof/epics/`. Schemas across `EPIC.md`, `plan.json`, planning node I/O, and node I/O require integer epic IDs.

## Conflict handling

Hosted trackers use conflict-detected push. A push that finds remote divergence writes a `tracker_sync_conflict` gate and raises `TrackerError`. The `/woof` operator surface presents the gate in the operator session. The operator resolves with one of:

- `keep_local`;
- `accept_remote`;
- `hand_merge`.

Resolution events use `tracker_synced` and `tracker_sync_conflict`.

## Consequences

- Any repository can run Woof with `kind = "local"` and no hosted tracker.
- A new hosted tracker is a new adapter plus a new `[tracker].kind` value.
- Graph topology, role routing, reviewer blocker handling, gate resolution, and transaction manifests are unchanged by tracker choice.
- GitHub-specific behaviour stays in `GitHubTracker`.
- Local epics have no remote authority check by design; the local epic directory is self-authoritative.
- The `/woof` operator surface presents tracker-sync conflicts conversationally; the operator resolves via the same structured verdicts the graph expects.
