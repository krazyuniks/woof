---
type: adr
status: accepted
date: 2026-05-17
---

# ADR-002: Graph-Led Role Routing And Model Policy

## Context

ADR-001 moved orchestration authority out of LLM prompts and into the Python
graph. The first extracted Woof implementation still carries the original
provider-shaped role names and local wrapper assumptions: Claude plans and
executes, Codex critiques, and dispatch shells through Ryan's private `cld` /
`cod` convenience wrappers.

The model landscape has changed. The intended operating profile is now:

- GPT-5.5 as the primary producer for planning, design, coding, and artefact
  generation.
- Claude Opus 4.7 as the reviewer, checker, and second-opinion model, run with
  explicit `max` effort.

Hard-coding either provider as "the orchestrator" would reintroduce the same
failure class ADR-001 removed. It would also conflict with Woof's "one way to do
anything" rule: workflow startup, graph execution, model dispatch, human gates,
and gate resolution must all go through one operator surface.

## Decision

Woof remains graph-led. Neither Codex nor Claude drives the workflow. The Python
graph is the orchestrator; model invocations are typed producer or reviewer
nodes; humans resolve gates.

Roles are semantic, not provider-owned:

| Role | Responsibility | Current preferred route |
|---|---|---|
| `primary` | Produce plans, design artefacts, story diffs, dispositions, and other graph-declared outputs. | `codex` CLI + `gpt-5.5` + `xhigh` reasoning |
| `reviewer` | Critique plans and story outputs; classify findings as `info`, `minor`, or `blocker`. | `claude` CLI + `claude-opus-4-7` + `max` effort |
| `gate-resolver` | Surface open gates and record structured human decisions. | In-session human/operator |

The graph continues after reviewer `info` or `minor` findings. For `minor`
findings, the primary must record a disposition: accepted, rejected, or deferred
with concise reasoning. Reviewer `blocker` findings open a human gate. There is
no model-to-model debate loop and no automatic stalemate resolver.

Woof must own the full public command construction. It must not require Ryan's
private shell wrappers, dotfiles, aliases, or absolute paths. Role routes must
record the resolved command, model, effort, flags, MCP set, timeout, and
audit/session reference in dispatch events. Effort is part of the route contract.
The reviewer route deliberately uses Claude `max` effort because the reviewer is
the explicit second-opinion path.

For Claude Code subprocesses, Woof builds the raw command itself, including MCP
isolation:

```text
timeout <minutes>m claude \
  --dangerously-skip-permissions \
  --strict-mcp-config \
  --mcp-config '<generated {"mcpServers": {...}} JSON>' \
  -p --output-format json \
  --model claude-opus-4-7 \
  --effort max \
  < prompt
```

The generated MCP JSON is empty by default (`{"mcpServers":{}}`). When a role
declares MCP servers, Woof resolves them from project-owned `.woof/` config or
standard Claude settings paths using portable home-relative paths only. No
generated command may refer to `/home/ryan`, `~/.dotfiles`, `cld`, or other local
operator aliases. Prompt payloads are sent on stdin rather than as one argv
element, so bundled playbook prompts do not hit per-argument size ceilings.

For Codex subprocesses, Woof calls the public `codex` CLI directly and injects
any project context it needs into the prompt or explicit input files. It must not
depend on Ryan's `cod` wrapper or on `agent-sync` side effects.

`woof wf` remains the only workflow entry point. Human gates are resolved through
`woof wf --epic <N> --resolve <decision>`. `woof dispatch` remains an internal
node primitive and should dispatch by role, not by a provider target chosen at
the operator surface.

## Consequences

- Stage 1-4 graph migration must wait until the role-routing pivot is complete;
  otherwise the new planning nodes will encode obsolete Claude/Codex
  assumptions.
- `.woof/agents.toml` needs an effort-aware role schema and migration path from
  the legacy `planner`, `story-executor`, and `critiquer` names.
- Preflight becomes the startup infrastructure check for Woof itself, the
  consumer `.woof/` files, public CLIs, generated MCP config, GitHub access,
  configured quality gates, and project-specific host/server prerequisites.
- Prompts and documentation must use primary/reviewer terminology. Provider names
  may appear in the default route examples, not in orchestration semantics.
- Dispatch audit paths and any generated transcript references must use portable
  locations such as `~/.claude/projects/<project-slug>/...` or repo-relative
  `.woof/epics/E<N>/audit/...`; never host-specific absolute paths.
- The old safety rule still holds: a reviewer can force human attention only by
  producing a schema-valid `severity: blocker`; a non-blocking review may inform
  the primary but cannot trap the workflow in an agent disagreement loop.
