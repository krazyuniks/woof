# Consumer Checkouts

Woof runs from its own checkout or installed package against a separate consumer repository. The consumer repository owns project-specific declarations under `.woof/`; it does not copy Woof source, schemas, playbooks, tests, or dogfood examples.

`guitar-tone-shootout` is the first external consumer. In that role, GTS remains responsible for its own application source, `just` recipes, Docker topology, GitHub issue scope, quality gates, language choices, and project-specific host or server checks. Woof remains responsible for graph execution, schemas, role dispatch, check runners, gate writing, and transaction verification.

## First-Run Bootstrap

From the consumer repository root:

```bash
woof init
```

`woof init` scaffolds `.woof/prerequisites.toml`, `.woof/agents.toml`, `.woof/quality-gates.toml`, and `.woof/test-markers.toml` with explicit `<replace>` placeholders for project-specific values, and inserts a fenced `# >>> woof` block into the repository `.gitignore` containing the required runtime entries (`.woof/.current-epic`, `.woof/.preflight-*`, `.woof/epics/*/.wf.lock`, `.woof/epics/*/audit/raw/`, and the cartography artefacts). Pass `--with-docs-paths` to also scaffold `.woof/docs-paths.toml`. The command is idempotent; existing TOMLs are preserved unless `--force` is set, and the gitignore block is updated in place rather than duplicated.

After `woof init`:

1. Replace every `<replace>` placeholder in `.woof/*.toml`.
2. Authenticate the model CLIs once: `claude /login` for the reviewer route and `codex login` for the primary route. `woof preflight` accepts either credential files (`~/.claude/.credentials.json`, `~/.codex/auth.json`) or the matching API-key environment variables (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`).
3. Optionally provide `./scripts/refresh-cartography` if the project wants the post-commit hook to regenerate cartography artefacts. Cartography substance is consumer-owned because the artefacts depend on the project's language stack; the Woof hook block is a no-op when the script is absent.
4. Run `woof preflight` and resolve any remaining failures.
5. Run `woof hooks install` to enable the post-commit cartography hook block.

## Consumer-Owned Files

Consumer checkouts may keep these files in their own `.woof/` directory:

| File | Consumer responsibility |
|---|---|
| `.woof/agents.toml` | Declare semantic role routes, timeouts, review-valve cadence, audit policy, and optional Claude MCP servers. Use public `adapter = "codex"` or `adapter = "claude"` routes for dispatchable roles. |
| `.woof/prerequisites.toml` | Declare required public CLIs, validators, GitHub repository, languages, indexing tools, and project-specific host or server readiness checks. |
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
| Host, server, GitHub, language, and tool prerequisites | `.woof/prerequisites.toml` | `woof preflight` validates declared infrastructure before graph execution. |

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
- Keep the GitHub scope in `.woof/prerequisites.toml` pointed at `krazyuniks/guitar-tone-shootout`.
- Route GTS verification through the GTS `just` surface, for example a quality gate command such as `just check`.
- Express project readiness through `.woof/prerequisites.toml` host and server checks instead of adding Woof-specific scripts to GTS.
- Use public role routes in `.woof/agents.toml`: `primary` should resolve to the public `codex` adapter and `reviewer` should resolve to the public `claude` adapter unless a later ADR changes the policy.
- Do not declare or depend on Ryan-local wrappers such as `cld`, `cod`, `agent-sync`, shell aliases, dotfiles, or host-specific absolute paths in executable config.

Legacy route names such as `planner`, `story-executor`, and `critiquer`, and legacy harness values such as `cld` and `cod`, are accepted only as migration input by the schema and dispatcher. New or refreshed consumer config should use semantic roles and public adapters directly.
