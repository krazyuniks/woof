# Onboarding a repo to Woof

Bring a consumer repository under Woof. This is the former `/woof:setup` flow, now a reference under
the `/woof` umbrella.

## Steps

1. Scaffold the config:

   ```bash
   woof init --tracker github   # epics are GitHub issues (needs `gh` + a repo)
   woof init --tracker local    # epics live on disk only, no remote
   ```

   `woof init` writes `.woof/prerequisites.toml`, `agents.toml`, `quality-gates.toml`, and
   `test-markers.toml`, and adds the Woof block to `.gitignore`. Add `--with-docs-paths` to also
   scaffold the Stage-5 docs-drift mappings.

2. Replace every `<replace>` placeholder in `.woof/*.toml`. In particular set the project test
   command in `quality-gates.toml` and, for the GitHub tracker, the `repo` in
   `prerequisites.toml`.

3. Authenticate the model CLIs once: `claude /login` and `codex login`.

4. Verify prerequisites and resolve any failures:

   ```bash
   woof preflight
   ```

5. Install the post-commit cartography hook (see `map-codebase.md`):

   ```bash
   woof hooks install
   ```

6. Start the first epic:

   ```bash
   woof wf new "<spark>"
   ```

   then drive it with the `woof wf --epic N` command that `woof wf new` prints, or hand off to
   `/woof:brainstorm` to lead the design first.

## Tracker choice

- `github`: epics are GitHub issues. Woof creates, hydrates, and syncs them; this is Woof's only
  external integration. Needs `gh` authenticated and `repo` set.
- `local`: epics live under `.woof/epics/E<N>/` with no remote. A Kanban board is `local` from
  Woof's point of view - it lives a layer out and drives `woof wf new`; Woof never knows about it.

See `docs/consumers.md` in the Woof repo for the full first-run walkthrough.
