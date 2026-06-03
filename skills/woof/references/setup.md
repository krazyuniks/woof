# Onboarding a repo to Woof

Bring a consumer repository under Woof from the `/woof` umbrella.

## Steps

1. Scaffold the config:

   ```bash
   woof init --tracker github --language python   # epics are GitHub issues (needs `gh` + a repo)
   woof init --tracker local --language python    # epics live on disk only, no remote
   ```

   `woof init` writes `.woof/prerequisites.toml`, `agents.toml`, `quality-gates.toml`, and
   `test-markers.toml`, and adds the Woof block to `.gitignore`. The scaffolded
   `prerequisites.toml` carries a `[cartography]` block (ADR-004) that turns on cartography
   enforcement; see step 4. Add `--with-docs-paths` to also scaffold the Stage-5 docs-drift
   mappings.

   Pass `--language <lang>` (repeatable; `python`, `go`, `typescript`, `rust`) to record the
   cartography languages in `[cartography].languages` and compose the consumer-owned
   `scripts/refresh-cartography` from the per-language fragments. Re-running `woof init` is
   idempotent and re-composes the script when the language set changes; with no `--language` it
   falls back to the languages already in `prerequisites.toml`. With no declared language the
   script is not composed - re-run with `--language` (or author `scripts/refresh-cartography` by
   hand).

2. Replace every `<replace>` placeholder in `.woof/*.toml`. In particular set the project test
   command in `quality-gates.toml` and, for the GitHub tracker, the `repo` in
   `prerequisites.toml`.

3. Authenticate the model CLIs once: `claude /login` and `codex login`.

4. Author cartography under `.woof/codebase/`. While the `[cartography]` block is present,
   `woof preflight` fails closed until all of the following exist (see `map-codebase.md`):

   - the two human-authored design docs, `TARGET-ARCHITECTURE.md` and `PRINCIPLES.md`, with
     real content. A doc that still carries the stub marker (`<!-- woof:stub -->` by default),
     or whose body is shorter than `summary_min_chars`, fails as a stub unless its front matter
     marks it complete (`status: complete`);
   - the seven mapper-authored AS-IS docs (run the map-codebase flow);
   - the consumer-owned, executable `scripts/refresh-cartography` and the mechanical layer it
     generates (`tags`, `files.txt`, `freshness.json`).

   To opt a repository out of cartography enforcement entirely, delete the `[cartography]`
   block from `prerequisites.toml`.

5. Install the post-commit cartography hook (see `map-codebase.md`):

   ```bash
   woof hooks install
   ```

   The hook runs `./scripts/refresh-cartography` on every commit to keep the mechanical layer
   fresh. Run it once by hand (or make a commit) so `tags`, `files.txt`, and `freshness.json`
   exist before preflight.

6. Verify prerequisites and resolve any failures:

   ```bash
   woof preflight
   ```

   With `[cartography]` declared, preflight reports a missing or stub design doc, a missing
   mechanical-layer file, or a missing/non-executable `scripts/refresh-cartography` as hard
   failures.

7. Start the first epic:

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
