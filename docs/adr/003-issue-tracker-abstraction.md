---
type: adr
status: accepted
date: 2026-05-20
---

# ADR-003: Issue-Tracker Abstraction

## Context

ADR-001 made the Python graph the orchestrator. ADR-002 made model roles
semantic rather than provider-owned. Both decisions removed a hard-coded
provider from a layer that should not name one.

The issue tracker was still hard-coded. GitHub coupling sat at
architecture-principle level:

- `docs/architecture.md` stated "Epic IDs. Always the gh issue number.
  `E<N>` == gh issue `#<N>`. No local-only epics; every epic has a gh issue."
- `src/woof/cli/github.py` was imported directly by `wf`, `render-epic`, and
  `preflight`.
- `schemas/prerequisites.schema.json` required a `[github]` table.
- `schemas/jsonl-events.schema.json` carried `github_synced` and
  `github_sync_conflict` as first-class event kinds; `schemas/gate.schema.json`
  carried `github_sync_conflict` in `triggered_by`.
- Five schemas described `epic_id` as a GitHub issue number.

Woof's stated target is "any project, anyone's, anywhere." A consumer using
Linear, Jira, Plane, Forgejo, or no hosted tracker at all could not run Woof.
This is Phase B gap BHID-002.

## Decision

Woof depends on an issue-tracker **protocol**, not on a provider. A `Tracker`
adapter owns every interaction with the external system; the graph, CLI, and
gate code depend only on the protocol.

### Package

`src/woof/trackers/` holds the abstraction:

- `base.py` - the `Tracker` protocol, `TrackerError`, the frozen result
  records, the conflict trigger/decision constants, and shared filesystem and
  hashing helpers.
- `epic_body.py` - tracker-agnostic transforms between `EPIC.md` front-matter
  and the managed issue body (rendering and cold-start parsing).
- `github.py` - `GitHubTracker`, the GitHub-issue adapter (the prior
  `cli/github.py` behaviour).
- `local.py` - `LocalTracker`, a filesystem-only adapter.
- `__init__.py` - `resolve_tracker(repo_root)`, the factory that reads
  `[tracker]` from `.woof/prerequisites.toml`.

### Protocol

`Tracker` covers `assert_runtime_reachable`, `create_epic`, `fetch_epic`,
`assert_epic_authority`, `has_sync_state`, `push_epic_definition`,
`push_plan_summary`, `complete_epic`, and `resolve_conflict`.

Conflict detection is intrinsic to the push operations: a push that finds the
tracker has diverged from the `.last-sync` baseline writes a
`tracker_sync_conflict` gate and raises `TrackerError`. `resolve_conflict`
applies the structured operator decision (`keep_local`, `accept_remote`,
`hand_merge`).

This is a deliberate refinement of the original RC-B2 sketch, which listed a
standalone `detect_conflict` protocol method. A standalone form would have to
re-fetch the tracker remote that the push already fetched, doubling network
calls per push under a rate limit. `create_epic` and `assert_epic_authority`
are likewise not in the sketch but are required by `woof wf new` and the
GAP-005 local-epic authority check.

### Configuration

`.woof/prerequisites.toml` `[github]` becomes `[tracker]`:

```toml
[tracker]
kind = "github"          # or "local"
repo = "<owner>/<name>"  # required when kind = "github"
```

### Adapters

Two adapters ship:

- `github` - the existing behaviour. One GitHub issue per epic, `E<N>` is the
  issue number, push is conflict-detected against `.last-sync`.
- `local` - filesystem-only. `.woof/epics/E<N>/` is the sole authority for an
  epic. Epic IDs are integers allocated locally as one more than the highest
  existing `E<N>` directory. Push operations write no remote state and no
  `.last-sync` because there is no second copy of the contract to keep in sync;
  a sync conflict can never arise, so a `local` epic never opens a
  `tracker_sync_conflict` gate. Lifecycle push methods still load local
  `EPIC.md` and `plan.json`, render the shared managed body shape, and reject
  epic completion until every planned story is `done`.

### Epic identifiers

The "Epic IDs. Always the gh issue number" principle is withdrawn. An epic ID
is a **tracker-assigned integer**: for `github` it is the issue number; for
`local` it is a locally allocated counter. Epic IDs remain integers in all
schemas. String identifiers (Jira, Linear, Plane) are deferred until a real
string-ID adapter lands; that work must widen five schemas' `epic_id` type.

### Event and gate renames

`github_synced` and `github_sync_conflict` become `tracker_synced` and
`tracker_sync_conflict` in code and as the canonical schema enum values. The
legacy spellings are retained as schema enum aliases, and gate-resolution and
transition code accept both, so `epic.jsonl` logs and in-flight `gate.md`
files written before this ADR still validate and resolve.

The `[github]` config table, by contrast, is a clean rename with no alias.
Audit logs are append-only and immutable, so they must keep validating;
configuration is live and edited, so a clean rename is correct.

## Consequences

- Any repository can run Woof with `kind = "local"` and no hosted tracker.
- A third-party adapter (Linear, Jira, Plane, Forgejo) is a new file
  implementing the `Tracker` protocol plus a `kind` enum value. No graph,
  CLI, or gate change is required.
- ADR-001 and ADR-002 invariants are unchanged: Woof stays graph-led, role
  routing stays semantic, reviewer blockers still open human gates.
- String epic IDs remain unsupported until a follow-up widens the `epic_id`
  type across `epic`, `plan`, `planning-node-input`, `node-input`, and
  `node-output` schemas.
- `local` epics have no remote authority check and no conflict detection by
  design; the local epic directory is self-authoritative.

## Alternatives considered

- **Keep GitHub hard-coded, ship GitHub-only.** Rejected: it permanently
  excludes every consumer not on GitHub and contradicts the stated target.
- **A standalone `detect_conflict` protocol method.** Rejected: it doubles the
  tracker fetch per push. Conflict detection stays intrinsic to push.
- **String epic IDs in this workstream.** Deferred: it widens five schemas and
  needs a real string-ID adapter to exercise the change. Integer IDs are
  retained until then.
