---
type: adr
status: accepted
date: 2026-05-17
---

# ADR-002: Graph-Led Role Routing And Model Policy

## Context

ADR-001 places orchestration authority in the Python graph. Woof therefore
needs semantic roles instead of provider-shaped role names, shell aliases, or
private wrapper assumptions.

## Decision

Woof remains graph-led. Neither Codex nor Claude drives the workflow. The Python
graph is the orchestrator; model invocations are typed producer or reviewer
nodes; humans resolve gates.

Roles are semantic:

| Role | Responsibility | Default scaffolded route |
|---|---|---|
| `primary` | Produce discovery artefacts, definitions, plans, story diffs, and other graph-declared outputs. | `codex` CLI, `gpt-5.5`, `xhigh` reasoning |
| `reviewer` | Critique plans and story outputs; classify findings as `info`, `minor`, or `blocker`. | `claude` CLI, `claude-opus-4-7`, `max` effort |
| `gate-resolver` | Surface open gates and record structured human decisions. | In-session human/operator |

The graph continues after reviewer `info` or `minor` findings. For non-blocking
story findings, the graph records a deterministic covering disposition rather
than dispatching another primary model turn. Reviewer `blocker` findings open a
human gate. There is no model-to-model debate loop. If the human fixes the story
output and approves the gate, the graph invalidates the stale blocker critique
and re-runs reviewer critique against the corrected staged diff before
verification.

Woof owns public command construction. Role routes and optional model-profile
overrides are declared in `.woof/agents.toml`; dispatch records the resolved
command, adapter, model, effort, selected profile, flags, MCP set, timeout,
runtime policy, and audit/session reference. Commands must not require private
shell wrappers, dotfiles, aliases, host absolute paths, or external sync side
effects.

For Claude Code subprocesses, Woof builds the raw command itself, including MCP
isolation:

```text
timeout <minutes>m claude \
  --dangerously-skip-permissions \
  --strict-mcp-config \
  --mcp-config '<generated {"mcpServers": {...}} JSON>' \
  -p --output-format json \
  --model <model> \
  --effort <effort> \
  < prompt
```

For Codex subprocesses, Woof calls the public `codex` CLI directly:

```text
timeout <minutes>m codex exec \
  --json \
  --skip-git-repo-check \
  --dangerously-bypass-approvals-and-sandbox \
  -s danger-full-access \
  --model <model> \
  -c model_reasoning_effort="<effort>" \
  < prompt
```

Prompt payloads are sent on stdin so bundled playbook prompts do not hit argv
size limits. `woof dispatch --role <role-name>` is the internal graph primitive;
the role route selects the adapter.

## Consequences

- Documentation, prompts, schemas, and CLI output use `primary` and `reviewer`
  terminology for workflow semantics.
- Provider names appear in route examples and audit details, not as
  orchestration concepts.
- Preflight validates public CLI availability, configured model/effort, MCP JSON
  construction, trusted-local runtime disclosure, credentials, tracker access,
  quality-gate commands, and configured host/server prerequisites.
- Dispatch audit references use portable home-relative or repo-relative paths.
- Model/profile selection stays in `.woof/agents.toml` and `WOOF_MODEL_PROFILE`;
  prompt text and graph orchestration do not carry model IDs.
- Legacy route names (`planner`, `story-executor`, `critiquer`) and legacy
  harness values (`cld`, `cod`) are accepted only as migration input.
