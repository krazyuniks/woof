# E1. Cartography Prerequisite

Active per-epic plan for E1 in `docs/backlog.md`. The backlog defines the open
work; this file sequences it into small, reviewable coding-agent prompts. Delete
this file when E1 is done.

## Goal

Cartography (`.woof/codebase/`) is mandatory, enforced infrastructure rather than
an optional convenience. A consumer that declares the `[cartography]` contract in
`.woof/prerequisites.toml` cannot pass `woof preflight` until the design docs are
authored (not stubs), the mechanical layer is present, and the
`scripts/refresh-cartography` script is present and executable. `woof init`
scaffolds the contract so new consumers are enforced by default. Per-language
refresh templates compose the script, and the post-commit hook keeps the
mechanical layer fresh and fails loud when it cannot. This gives every dispatched
node prompt-ready repo context (ADR-004) without re-exploring the tree per call.

## Stories

| ID | Story | Acceptance criteria |
|---|---|---|
| S1 | Cartography contract + preflight missing/stub enforcement | `[cartography]` schema shape exists (`staleness_floor_hours`, `summary_min_chars`, `languages`, `stub_marker`). When the block is present, `woof preflight` fails closed on a missing/non-executable `scripts/refresh-cartography`, a missing or stub `TARGET-ARCHITECTURE.md` or `PRINCIPLES.md`, and a missing mechanical file (`tags`, `files.txt`, `freshness.json`). When the block is absent, preflight keeps the legacy optional-script behaviour. `woof init` scaffolds the block; gitignore ignores `files.txt` (not `tree.txt`). |
| S2 | Stale freshness as a non-blocking warning | A present `freshness.json` older than `staleness_floor_hours` is reported as a warning with a refresh prompt and does not fail preflight. |
| S3 | Per-language refresh templates + `woof init` composition | Ship `refresh-cartography` fragments for Python, Go, TypeScript, Rust referenced from `languages/<lang>.toml`. `woof init` composes the declared `[cartography].languages` fragments into the consumer's `scripts/refresh-cartography` idempotently, writing `freshness.json` as `{ts, git_ref, age_s, generator_version}`. |
| S4 | Post-commit hook regenerates the mechanical layer, fails loud | The Woof post-commit hook runs `./scripts/refresh-cartography`; a non-zero exit fails the hook loudly rather than leaving stale mechanical state. |
| S5 | Existing-consumer onboarding error | A consumer without cartography gets a clear preflight error pointing at the `/woof` setup and map-codebase references. |

## Prompt sequence

| # | Prompt summary | Files touched | Review checkpoint |
|---|---|---|---|
| 1 | **(done, S1)** Add the `[cartography]` schema shape; make `woof preflight` report missing/stub cartography clearly (docs + mechanical + script) when the block is present; scaffold the block in `woof init` and fix the `tree.txt` -> `files.txt` gitignore entry; update the `/woof` setup/map-codebase references; add focused preflight tests. | `schemas/prerequisites.schema.json`, `src/woof/cli/preflight.py`, `src/woof/cli/init.py`, `skills/woof/references/{setup,map-codebase}.md`, `tests/unit/test_preflight.py`, this plan | Declared cartography fails on each missing/stub case and passes when fully authored; legacy projects without the block are unchanged; `just check` green. |
| 2 | **(done, S2)** S2: stale `freshness.json` -> non-blocking warning keyed on `staleness_floor_hours`. Added a `cartography.freshness` floor check and a `warn` severity on `PreflightFinding` (an `ok=True` finding printed as `WARN`, excluded from `failed`/exit code). Age preferred numeric `age_s`, falling back to `ts` vs `_utc_now()` (prompt 3 inverts this per D4: `ts` is authoritative, `age_s` is the test fallback). Missing stamp = no finding (presence stays the mechanical check's blocking concern); malformed stamp = non-blocking warn. Kept in the floor tier: the 24h cache TTL is far below the 168h default floor, so under-report is bounded and benign. | `src/woof/cli/preflight.py`, `tests/unit/test_preflight.py`, this plan | Stale stamp warns but preflight stays green; fresh stamp is silent; the warning carries the `./scripts/refresh-cartography` prompt. |
| 3 | **(done, S3)** S3: shipped per-language `refresh-cartography` fragments (`languages/refresh-cartography/{python,go,typescript,rust}.sh`) referenced from each `languages/<lang>.toml` via `[cartography].refresh_fragment` (registry schema amended to allow it). `woof init` grows a repeatable `--language` flag that writes `[cartography].languages` and composes `scripts/refresh-cartography` (mode 0o755) from a shared scaffold (git ls-files -> files.txt; one ctags pass -> tags; freshness.json) plus the fragments, idempotently via a managed block, falling back to existing `prerequisites.toml` on re-run and skipping-with-note when no language is known. Added `schemas/freshness.schema.json` (`{ts, git_ref, age_s, generator_version}`), registered in `main.py` (SCHEMAS + `freshness.json` detection + `load_payload`) and the architecture catalogue, and the composed script emits a conforming stamp. Reconciled the prompt-2 reader (see D4). | `languages/refresh-cartography/*.sh`, `languages/*.toml`, `schemas/{language-registry,freshness}.schema.json`, `src/woof/cli/{init,main,preflight}.py`, `tests/unit/{test_refresh_cartography,test_preflight,test_validate}.py`, skill references, this plan | `woof init` composes a runnable script idempotently for declared languages; produced `freshness.json` matches the schema; the four registries validate. `just check` green. |
| 4 | S4: post-commit hook regenerates the mechanical layer and fails loud on non-zero exit. | `src/woof/cli/` hooks, tests | Hook regenerates `tags`/`files.txt`/`freshness.json`; a failing refresh script fails the hook. |
| 5 | S5: clear onboarding error for existing consumers without cartography, pointing at the setup/map-codebase references. | `src/woof/cli/preflight.py`, docs, tests | Cartography-less consumer gets an actionable preflight error. |

## Risk register

- Cartography becomes ceremony instead of useful context: keep the required docs to two design files plus the mechanical layer; fail only on missing/stub, warn on stale.
- Breaking every existing preflight consumer at once: enforcement is keyed on the presence of the `[cartography]` block, matching how `[lsp]`/`[indexing]`/`[host]`/`[servers]` are already conditionally checked. `woof init` scaffolds the block so new consumers are enforced by default; blanket enforcement for legacy consumers is S5, not S1.
- Stub detection false positives: a short-but-intentional doc can opt out with `status: complete` front matter; the explicit stub marker is the unambiguous "still boilerplate" signal.

## Decisions resolved during the epic

| ID | Decision | Resolution |
|---|---|---|
| D1 | How does preflight decide cartography is in force without breaking every consumer? | The presence of `[cartography]` in `prerequisites.toml` opts a repository in. `woof init` scaffolds it, so new consumers are enforced by default. |
| D2 | What counts as a stub design doc? | Body shorter than `summary_min_chars` (front matter excluded), OR still containing `stub_marker`. A short doc with front-matter `status: complete` (or `complete: true`) is accepted. |
| D3 | Is stale `freshness.json` blocking? | No. Missing mechanical files block (S1); staleness is a non-blocking warning (S2). |
| D4 | `ts` vs `age_s` for staleness, given the post-commit hook rewrites the stamp every commit? | `ts` is authoritative for production (a stamp only ages when commits stop, exactly when the static `age_s` written at generation, always 0, stays frozen). The prompt-3 reader prefers `ts`; `age_s` is the deterministic test fallback used only when `ts` is absent/unparseable. This inverts the prompt-2 preference order so a frozen `age_s` can no longer mask production staleness. |

## Out of scope

- A new skill or a `woof graph` command (withdrawn directions).
- Cross-repo edits to any consumer; this epic only changes Woof itself.
- Mid-epic auto-remap of cartography (ADR-004: captured once per epic).

## Done definition

- All stories' acceptance criteria met.
- All review checkpoints passed.
- All decisions in the table resolved.
