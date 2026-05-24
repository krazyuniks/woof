---
type: adr
status: accepted
date: 2026-05-20
---

# ADR-003: Issue-Tracker Abstraction

## Context

Woof keeps the epic-level contract in an issue tracker and the runtime workflow
state in `.woof/epics/E<N>/`. Requiring every consumer to use GitHub would make
the graph less portable than the rest of the architecture. A repository with no
hosted tracker must still be able to run Woof.

## Decision

Woof depends on a `Tracker` protocol, not on a specific provider. The graph,
CLI, and gate code depend on the protocol; provider-specific behaviour stays in
adapter implementations.

`src/woof/trackers/` owns the abstraction:

- `base.py` - `Tracker`, `TrackerError`, result records, conflict constants,
  and shared filesystem/hash helpers.
- `epic_body.py` - tracker-agnostic transforms between `EPIC.md` front-matter
  and the managed tracker body.
- `github.py` - GitHub issue adapter.
- `local.py` - filesystem-only adapter.
- `__init__.py` - `resolve_tracker(repo_root)`, the factory that reads
  `[tracker]` from `.woof/prerequisites.toml`.

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

- `github`: one GitHub issue per epic. `E<N>` is the GitHub issue number. Push
  is conflict-detected against `.woof/epics/E<N>/.last-sync`, and every graph
  invocation verifies runtime reachability.
- `local`: filesystem-only. `.woof/epics/E<N>/` is the authority for an epic.
  Epic IDs are locally allocated integers. There is no remote, no `.last-sync`,
  and no sync-conflict gate.

Lifecycle push methods on both adapters render the same managed body shape from
local `EPIC.md` and `plan.json`. The `local` adapter still rejects epic
completion until every planned story is `done`.

## Epic Identifiers

An epic ID is a tracker-assigned integer. For `github`, it is the GitHub issue
number. For `local`, it is the next integer allocated under `.woof/epics/`.

String epic IDs are not supported by the current schemas. A string-ID tracker
requires a schema change across the epic, plan, planning-node input, node input,
and node output contracts.

## Conflict Handling

Hosted trackers use conflict-detected push. A push that finds remote divergence
writes a `tracker_sync_conflict` gate and raises `TrackerError`. The operator
resolves the gate with one of:

- `keep_local`;
- `accept_remote`;
- `hand_merge`.

The legacy event names `github_synced` and `github_sync_conflict` remain schema
aliases so old audit logs keep validating. New events use
`tracker_synced` and `tracker_sync_conflict`.

## Consequences

- Any repository can run Woof with `kind = "local"` and no hosted tracker.
- A new hosted tracker is a new adapter plus a new `[tracker].kind` value.
- Graph topology, role routing, reviewer blocker handling, gate resolution, and
  transaction manifests are unchanged by tracker choice.
- GitHub-specific behaviour stays in `GitHubTracker`.
- Local epics have no remote authority check by design; the local epic directory
  is self-authoritative.
