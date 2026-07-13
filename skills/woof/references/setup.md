# Onboarding a repo to Woof

Bring a consumer repository under Woof from the `/woof` umbrella.

## Steps

1. Write the project config:

   ```bash
   woof init --project <key> --tracker github --language python   # epics are GitHub issues (needs `gh` + a repo)
   woof init --project <key> --tracker local --language python    # epics live on disk only, no remote
   ```

   `woof init` writes one config file, `~/.woof/config/projects/<key>.toml` (ADR-017). No config
   and no engine state is written into the repository being driven: a delivery repo carries no
   trace of the engine that builds it. The only file init writes into the repo is
   `scripts/refresh-cartography`, the project's own cartography generator (see below).
   The project key is explicit at every entry point and is never derived from the
   checkout's directory name, because worktree containers routinely hold directories called
   `main`. Set `WOOF_PROJECT` to avoid repeating `--project` on every command.

   The config carries every section the engine reads: delivery profile, verification command, run
   profiles, check floor, cartography (ADR-004), drain semantics, dispatch timeouts and audit,
   quality gates, prerequisites, and the tracker. Add `--with-docs-paths` to also scaffold the
   Stage-5 docs-drift mappings. Init refuses to overwrite an existing config; pass `--force` to
   replace it.

   Each quality gate declares its own `mode`, and an undeclared mode is `strict`. There is no
   file-level default mode: a gate that should not fail the run says `mode = "baseline"` on the
   gate itself. When migrating a project that relied on a file-level default of `baseline`, set
   `mode = "baseline"` on each gate that needs it, or those gates start failing the run.

   With `--tracker` omitted, `woof init` infers the tracker from the project's git remote: a
   github `origin`/`upstream` remote scaffolds the github tracker with `repo` pre-filled from its
   `owner/name`, otherwise it scaffolds the local tracker. Pass `--tracker github` or
   `--tracker local` to choose explicitly.

   Pass `--language <lang>` (repeatable; `python`, `go`, `typescript`, `rust`) to record the
   cartography languages in `[cartography].languages` and compose the project-owned
   `scripts/refresh-cartography` from the per-language fragments. That script is the one file
   init still writes into the repo, because it is the project's own generator, run by the
   project's post-commit hook. With no declared language the script is not composed - re-run with
   `--language` (or author `scripts/refresh-cartography` by hand).

2. Replace the `<replace>` placeholders in `~/.woof/config/projects/<key>.toml` - in particular
   the verification command and the test gate command. For the GitHub tracker the `repo` is
   pre-filled from the git remote when one is reachable; set it by hand only if it still reads
   `<replace>/<replace>`.

3. Authenticate the model CLIs once: `claude /login` and `codex login`.

4. Author cartography under `~/.woof/state/projects/<key>/codebase/` when the config requires it.
   It is engine state in the operator home, not repo content, so it is not under version control in
   the repo being delivered (see `map-codebase.md`). `woof preflight` enforces the declared
   cartography floor:

   - `design`, `lexical`, and `structural` require the two human-authored design docs,
     `TARGET-ARCHITECTURE.md` and `PRINCIPLES.md`, with
     real content. A doc that still carries the stub marker (`<!-- woof:stub -->` by default),
     or whose body is shorter than `summary_min_chars`, fails as a stub unless its front matter
     marks it complete (`status: complete`);
   - `lexical` and `structural` require the consumer-owned, executable `scripts/refresh-cartography` and the mechanical layer it
     generates (`tags`, `files.txt`, `freshness.json`).
   - mapper-authored AS-IS docs are loaded when present and required at dispatch when the selected node requests them.
   - `none` requires no cartography artefacts.

   A project whose config selects a non-none floor but declares no `[cartography]` section fails
   preflight; re-run `woof init --project <key> --language <lang>` and then complete this setup and
   map-codebase path.

5. Install the post-commit cartography hook (see `map-codebase.md`):

   ```bash
   woof hooks install
   ```

   The hook runs `./scripts/refresh-cartography` on every commit to keep the mechanical layer
   fresh. The script writes into the project's cartography directory in the operator home, not into
   the repo. Run it once by hand (or make a commit) so `tags`, `files.txt`, and `freshness.json`
   exist before preflight.

6. Verify prerequisites and resolve any failures:

   ```bash
   woof preflight
   ```

   Preflight reports cartography failures only for the floor selected in the project config.

7. Start the first epic:

   ```bash
   woof wf new "<spark>"
   ```

   then drive it with the `woof wf --epic N` command that `woof wf new` prints, or hand off to
   `/woof:brainstorm` to lead the design first.

## Tracker choice

- `github`: epics are GitHub issues. Woof creates, hydrates, and syncs them; this is Woof's only
  external integration. Needs `gh` authenticated and `repo` set; `woof init` pre-fills `repo` from
  the git remote when one is reachable.
- `local`: epics live under `~/.woof/state/projects/<key>/epics/E<N>/` with no remote. A Kanban
  board is `local` from Woof's point of view - it lives a layer out and drives `woof wf new`; Woof
  never knows about it.

See `docs/consumers.md` in the Woof repo for the full first-run walkthrough.
