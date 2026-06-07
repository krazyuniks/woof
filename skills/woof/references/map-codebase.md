# Mapping the codebase (cartography)

Every Woof consumer repo carries a cartography artefact group at `.woof/codebase/` so dispatched
nodes get prompt-ready repo context cheaply (ADR-004). The `/woof` umbrella owns the map-codebase
flow. It runs in three layers.

## Design layer (human-authored, durable)

Authored during setup, refreshed only when architectural strategy changes:

- `TARGET-ARCHITECTURE.md` - the shape the project is designed to be (greenfield: written before
  code; brownfield: pragmatic acceptance, aspirational refactor, or mixed).
- `PRINCIPLES.md` - cross-cutting design principles.

Help the operator author or update these by hand; they are not regenerated.

`woof preflight` requires `prerequisites.toml` to declare `[cartography]` and treats both design
docs as mandatory and non-stub. A doc fails preflight as a stub if it still contains the stub
marker (`stub_marker`, default `<!-- woof:stub -->`) or if its body (front matter excluded) is
shorter than `summary_min_chars` (default 200). A short-but-intentional doc can mark itself
complete in front matter (`status: complete`, or `complete: true`). Author real content and remove
the stub marker before preflight passes.

## AS-IS layer (mapper-authored, refreshed on demand)

Seven themed documents describing the repo as it is. Regenerate them by dispatching parallel mapper
subagents (one per theme) via the `Task` tool; each explores the codebase and writes its document
directly to `.woof/codebase/`:

- `CURRENT-ARCHITECTURE.md` - observed patterns, layers, data flow, abstractions, entry points.
- `STACK.md` - languages, frameworks, runtime, dependencies.
- `INTEGRATIONS.md` - external APIs, databases, auth, monitoring, CI/CD.
- `STRUCTURE.md` - directory layout, where to add new code, naming.
- `CONVENTIONS.md` - naming, formatting, imports, comments, function design.
- `TESTING.md` - framework, file organisation, mocking, fixtures, coverage.
- `CONCERNS.md` - tech debt, known bugs, security risks, fragile areas.

Run the mappers in parallel for a full refresh, then update the freshness stamp.

### Mapper dispatch constraints (hygiene)

Each mapper subagent writes a document that is committed as planning state, so every
dispatch prompt MUST carry these constraints.

**Never read or quote the contents of secret-bearing files** (note their existence only):

- `.env`, `.env.*`, `*.env` - environment files
- `credentials.*`, `secrets.*`, `*secret*`, `*credential*` - credential files
- `*.pem`, `*.key`, `*.p12`, `*.pfx`, `*.jks`, `*.keystore`, `*.truststore` - keys and keystores
- `id_rsa*`, `id_ed25519*`, `id_dsa*` - SSH private keys
- `.npmrc`, `.pypirc`, `.netrc` - package-manager auth tokens
- `serviceAccountKey.json`, `*-credentials.json` - cloud service credentials
- `config/secrets/*`, `.secrets/*`, `secrets/` - secret directories
- any `.gitignore`d file that looks like it holds secrets

If such a file is relevant, record only that it exists (e.g. "`.env` present - holds runtime
configuration"). Never emit a value such as `API_KEY=...`, `sk-...`, or a token. The mapper
output is committed to git; a leaked secret is a security incident.

`woof preflight` enforces this as a backstop: it scans the committed cartography docs
(`.woof/codebase/*.md`) for high-signal secret tokens on every run and fails closed on a
match, reporting the file and line (never the value). The instruction above keeps secrets
out of the docs; the preflight gate catches anything that slips through before it is committed.

## Mechanical layer (post-commit refreshed)

Regenerated automatically by the Woof-managed post-commit hook, which runs the consumer-owned
`scripts/refresh-cartography`:

- `tags` - a ctags index.
- `files.txt` - `git ls-files` output.
- `freshness.json` - the freshness stamp, `{ts, git_ref, age_s, generator_version}`. `ts` is the
  authoritative staleness signal (the hook rewrites it every commit, so it only ages when commits
  stop); `age_s` is written as 0 at generation.

ADR-009 adds the next planned mechanical artefact: `.woof/codebase/structural/index.sqlite`,
a local symbols-and-edges index consumed through `woof cartography`. It is not mandatory until the
generator and preflight support land. When that work starts, first check for any concurrent
tree-sitter/parser implementation and reuse it rather than creating a second extraction path.

`scripts/refresh-cartography` is composed by `woof init --language <lang>` from the per-language
fragments in `languages/<lang>.toml` (a shared scaffold plus one fragment per declared language).
It is re-composable: re-run `woof init` to refresh it after changing the language set.

Install the hook once during setup:

```bash
woof hooks install
```

A manual mechanical refresh is just running `./scripts/refresh-cartography` (or making a commit).
`woof preflight` fails closed on a missing `[cartography]` block, a missing mechanical file
(`tags`, `files.txt`, `freshness.json`), and a missing or non-executable
`scripts/refresh-cartography`.

See ADR-004 (`docs/adr/004-cartography-prerequisite.md`) for the full rationale.
