# Consumer Checkouts

Woof runs from its own checkout or installed package against a separate consumer repository. The consumer repository owns project-specific declarations under `.woof/`; it does not copy Woof source, schemas, playbooks, tests, or dogfood examples.

`guitar-tone-shootout` is the first external consumer. In that role, GTS remains responsible for its own application source, `just` recipes, Docker topology, GitHub issue scope, quality gates, language choices, and project-specific host or server checks. Woof remains responsible for graph execution, schemas, role dispatch, check runners, gate writing, and transaction verification.

## First-Run Walkthrough

This walkthrough takes a new consumer from a clean machine to a running epic without reading the architecture document. It assumes you are integrating Woof into your own repository (the "consumer repository"); Woof itself is installed separately and is never copied in.

### 1. Install Woof

Woof is published as a Python package. Install it as a standalone tool:

```bash
uv tool install woof
```

Or into an existing environment:

```bash
pip install woof
```

Confirm the CLI is on `PATH`:

```bash
woof --help
```

### 2. Scaffold the consumer config

From the root of the repository you want Woof to manage:

```bash
woof init
```

`woof init` creates `.woof/prerequisites.toml`, `.woof/agents.toml`, `.woof/quality-gates.toml`, and `.woof/test-markers.toml`, each with explicit `<replace>` placeholders, and inserts a fenced `# >>> woof` block into the repository `.gitignore` with the required runtime entries (`.woof/.current-epic`, `.woof/.preflight-*`, `.woof/epics/*/.wf.lock`, `.woof/epics/*/audit/raw/`, and the cartography artefacts). Pass `--with-docs-paths` to also scaffold `.woof/docs-paths.toml` (Stage 5 Check 8 mappings). `woof init` is idempotent: existing TOMLs are preserved unless `--force` is set, and the gitignore block is updated in place rather than duplicated.

Choose the issue tracker at scaffold time. The default scaffolds a GitHub-backed setup. For a repository with no hosted issue tracker, scaffold the local tracker instead:

```bash
woof init --tracker local
```

The `github` tracker keeps each epic's contract in a GitHub issue (`E<N>` is issue `#<N>`) and declares the `gh` CLI as required infrastructure. The `local` tracker keeps every epic under `.woof/epics/E<N>/` with no remote and does not require `gh`. See [ADR-003](adr/003-issue-tracker-abstraction.md).

### 3. Fill in the placeholders

Open each scaffolded TOML and replace every `<replace>` value:

- `prerequisites.toml` - for the `github` tracker, set `[tracker] repo` to `<owner>/<name>`; the `local` tracker has no `repo` line. Adjust the declared `[infra]`, `[commands]`, and `[validators]` versions to what the project actually requires.
- `quality-gates.toml` - set `[gates.test] command` to the project's real verification command, for example `just test` or `pytest`.
- `agents.toml` - the default routes follow [ADR-002](adr/002-graph-led-role-routing.md) (primary `codex`/`gpt-5.5`, reviewer `claude`/`claude-opus-4-7`); edit only if the project routes differ.
- `test-markers.toml` - optional; the shipped Python and TypeScript defaults work unless the project uses a different test layout.

An unedited `<replace>` string fails loud at `woof preflight` or first command resolution, so an un-filled bootstrap cannot run silently.

### 4. Authenticate the model CLIs

Woof dispatches to the public `claude` and `codex` CLIs. Authenticate each once:

```bash
claude /login
codex login
```

`woof preflight` accepts either CLI-managed credential files (`~/.claude/.credentials.json` for the reviewer route, `~/.codex/auth.json` for the primary route) or the matching API-key environment variables (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`).

### 5. Verify the setup

```bash
woof preflight
```

Preflight validates the public CLIs, role routes, generated MCP config, tracker reachability, credential markers, language tooling, quality-gate command resolution, and the `.woof/` config schemas. Resolve every reported failure before running the graph.

### 6. Install the post-commit hook (optional)

```bash
woof hooks install
```

This adds an idempotent Woof-managed block to the repository `post-commit` hook. The block invokes `./scripts/refresh-cartography` when that script exists and is a no-op otherwise. Cartography substance is consumer-owned because the artefacts depend on the project's language stack; provide `./scripts/refresh-cartography` only if you want code-map artefacts regenerated on commit.

### 7. Start the first epic

```bash
woof wf new "<spark>"
```

`woof wf new` creates a tracker-backed epic from the one-line spark, initialises `.woof/epics/E<N>/`, and selects it as the current epic. With the `github` tracker the epic id is the new GitHub issue number; with the `local` tracker it is the next integer allocated under `.woof/epics/`.

### 8. Run the graph

```bash
woof wf --epic <N>
```

The deterministic graph advances the epic through discovery, definition, breakdown, execution, and Stage 5 verification. When the graph needs a human decision it writes a gate and stops; resolve it with a structured decision and re-run:

```bash
woof wf --epic <N> --resolve <decision>
```

See [`architecture.md`](architecture.md) for stage and gate semantics once you are past first run.

## Consumer-Owned Files

Consumer checkouts may keep these files in their own `.woof/` directory:

| File | Consumer responsibility |
|---|---|
| `.woof/agents.toml` | Declare semantic role routes, timeouts, review-valve cadence, audit policy, and optional Claude MCP servers. Use public `adapter = "codex"` or `adapter = "claude"` routes for dispatchable roles. |
| `.woof/prerequisites.toml` | Declare required public CLIs, validators, the `[tracker]` issue tracker, languages, indexing tools, and project-specific host or server readiness checks. |
| `.woof/quality-gates.toml` | Declare the project-owned verification commands that Stage 5 Check 1 runs from the consumer repository root. |
| `.woof/test-markers.toml` | Optional. Override outcome-marker detection when the default language conventions are not enough. |
| `.woof/docs-paths.toml` | Optional. Map code paths to documentation paths for Stage 5 Check 8. |

Generated epic state belongs under `.woof/epics/E<N>/` only when Woof creates it for that consumer repository. Do not seed a consumer checkout by copying `.woof/epics/` content, audit output, locks, codebase maps, or dogfood examples from the Woof repository.

## Policy Generalisation

Consumer policy starts in the consumer repository. Woof only absorbs a policy
when it can be expressed as a reusable declaration under `.woof/`, validated by
a Woof schema, enforced by a checker or preflight path, and covered by tests.
Until those conditions are true, the policy stays in the consumer's own `just`
recipes, CI, docs, or application code.

Current reusable policy surfaces are:

| Policy need | Woof surface | Enforcement |
|---|---|---|
| Project verification commands | `.woof/quality-gates.toml` | Stage 5 Check 1 runs each declared command from the consumer root. |
| Outcome marker conventions | `.woof/test-markers.toml` | Stage 5 Check 2 scans staged test diffs using configured marker rules. |
| Code-to-doc drift requirements | `.woof/docs-paths.toml` | Stage 5 Check 8 requires mapped docs changes in the same transaction. |
| Public role routes, review cadence, and audit policy | `.woof/agents.toml` | Dispatch, review-valve, and audit code read the declared route and policy settings. |
| Host, server, issue-tracker, language, and tool prerequisites | `.woof/prerequisites.toml` | `woof preflight` validates declared infrastructure before graph execution. |

Do not hard-code GTS paths, servers, Docker service names, issue labels, no-mock
rules, or framework-specific conventions into Woof. If a second consumer needs
the same rule, first design the smallest portable config shape, add or extend
the schema, implement the checker/preflight behaviour, and document the failure
mode. Otherwise, call the consumer's existing command from
`.woof/quality-gates.toml` and let that repository own the rule.

## Tool-Owned Assets

These stay in Woof, not in GTS or another consumer repository:

- `src/woof/` graph, CLI, dispatch, check-runner, and gate code.
- `schemas/` JSON Schema contracts.
- `languages/` tool-side language registry files.
- `playbooks/` producer and reviewer prompt templates.
- `examples/dogfood/` curated Woof evidence.
- Woof's own tests, hooks, and implementation ledger.

Consumers reference those assets through the `woof` command. They should not vendor-copy them to make local workflow edits.

## GTS Boundary

For GTS, Woof integration should follow these rules:

- Run `woof preflight` and `woof wf --epic <N>` from the GTS checkout, with the public `woof` command on `PATH` or invoked from an external Woof install.
- Keep `[tracker]` in `.woof/prerequisites.toml` set to `kind = "github"` with `repo = "krazyuniks/guitar-tone-shootout"`.
- Route GTS verification through the GTS `just` surface, for example a quality gate command such as `just check`.
- Express project readiness through `.woof/prerequisites.toml` host and server checks instead of adding Woof-specific scripts to GTS.
- Use public role routes in `.woof/agents.toml`: `primary` should resolve to the public `codex` adapter and `reviewer` should resolve to the public `claude` adapter unless a later ADR changes the policy.
- Do not declare or depend on Ryan-local wrappers such as `cld`, `cod`, `agent-sync`, shell aliases, dotfiles, or host-specific absolute paths in executable config.

Legacy route names such as `planner`, `story-executor`, and `critiquer`, and legacy harness values such as `cld` and `cod`, are accepted only as migration input by the schema and dispatcher. New or refreshed consumer config should use semantic roles and public adapters directly.
