# Consumer Checkouts

Woof runs from its own checkout or installed package against a separate consumer repository. The consumer repository owns project-specific declarations under `.woof/`; it does not copy Woof source, schemas, playbooks, tests, or dogfood examples.

`guitar-tone-shootout` is the first external consumer. In that role, GTS remains responsible for its own application source, `just` recipes, Docker topology, GitHub issue scope, quality gates, language choices, and project-specific host or server checks. Woof remains responsible for graph execution, schemas, role dispatch, check runners, gate writing, and transaction verification.

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
