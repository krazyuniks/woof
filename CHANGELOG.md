# Changelog

## 0.1.3 - 2026-05-24

- Allowed documentation/manual stories with no automated test work to skip
  Stage 5 outcome-marker enforcement instead of requiring fake test markers.

## 0.1.2 - 2026-05-24

- Fixed reviewer-blocker gate approval so corrected story work re-enters
  reviewer critique instead of reopening the stale blocker gate.

## 0.1.1 - 2026-05-24

- Removed the obsolete `codex exec -a never` approval flag so the Codex-backed
  primary route works with the current public Codex CLI.

## 0.1.0 - 2026-05-24

Initial public release.

- Ships the `woof` CLI and `python -m woof` module entry point.
- Supports local and GitHub tracker-backed consumer setup with `woof init`.
- Runs the deterministic workflow graph from discovery through definition,
  breakdown, plan gate, story execution, verification, and manifest-checked
  commit.
- Includes JSON Schema contracts, language registries, and producer/reviewer
  playbooks in the installed package.
- Covers installed-package operation with release smoke tests and CI package
  builds.
