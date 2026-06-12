# E20. Per-Stage Role Routing

Per-epic plan for E20 in `docs/backlog.md`. Sequences ADR-002's overlay table into
three independently-reviewable, independently-committable producer prompts. Each prompt
is the contract between the operator and the coding agent: tight scope, files touched,
tests added, review checkpoint.

E20 implements the `[routes.<node_group>.<role>]` overlay table and threads `route_key`
through the dispatch pipeline. It does not add a new gate type, a new graph node, or any
new artefact format. The only visible behaviour change is that Woof's own Stage-5 dispatch
flips from Codex→Claude (producer) and Claude→Codex (reviewer), matching ADR-002.

## Goal

After E20, per-stage producer/reviewer policy is data in `.woof/agents.toml`. A consumer
can declare `[routes.execution.primary] adapter = "claude"` and the dispatch pipeline
honours it without touching graph code or prompt text. `route_key` and `resolved_adapter`
appear in every dispatch audit event so post-hoc analysis can attribute tokens by stage.
Preflight validates all four node groups (discovery, definition, planning, execution) resolve
to a dispatchable adapter; a misconfigured consumer fails closed at preflight, not silently
at first dispatch.

## Stories

| ID | Story | Acceptance criteria |
|---|---|---|
| S1 | Schema overlay and route resolver | `agents.schema.json` accepts a `routes` table with per-node-group role entries. `RoleRoute` carries `route_key`. `resolve_agent_route` accepts an optional `route_key`; when provided, it checks `routes.<group>.<role>` before falling back to `roles.<role>`. Model-profile overlays compose with route overrides. `jsonl-events.schema.json` adds optional `route_key` to dispatch events. Dispatch audit events record `route_key` when provided. |
| S2 | Node group threading | `woof dispatch` CLI accepts `--route-key <group>`. `_run_dispatch` in `nodes.py` accepts and forwards `route_key`. Every dispatch call site passes the correct node group: `discovery` (bucket nodes, synthesis), `definition` (definition node), `planning` (breakdown producer and plan critique reviewer), `execution` (executor and critique nodes). |
| S3 | Config correction, preflight, init | `.woof/agents.toml` and `init.py` AGENTS_TEMPLATE add execution-group overrides (Claude producer, Codex reviewer) with model-profile entries. `_check_role_routes` validates all four node groups resolve; each gets its own `agents.<group>.<role>.route` finding ID. `_dispatch_routes_summary` in `observe.py` reports per-group routes alongside base roles. |

## Consumer and event-log checklist

**Consumers of `routes` config in `agents.toml` (all four must be consistent):**

| Consumer | Change required | Prompt |
|---|---|---|
| `dispatcher.py` `resolve_agent_route` | New overlay lookup path | P1 |
| `dispatcher.py` `cmd_dispatch` | Read `args.route_key`; pass to resolver; record in audit | P2 |
| `preflight.py` `_check_role_routes` | Validate all four groups | P3 |
| `observe.py` `_dispatch_routes_summary` | Report per-group routes | P3 |
| `init.py` AGENTS_TEMPLATE | Add execution-group overrides | P3 |
| `.woof/agents.toml` (this repo) | Add execution-group overrides | P3 |

**Consumers of dispatch audit events (jsonl):**

| Consumer | Impact of `route_key` field | Action |
|---|---|---|
| `nodes.py` `_dispatch_outcome_from_events` | Reads `role`/`epic_id`/`story_id` only — additive, no change | None |
| `bench/efficiency.py` | Reads tokens/timing only — additive, no change | None |
| `test_dispatch.py` e2e fixtures | Dry-run and audit jsonl tests must cover `route_key` recording | P1/P2 |
| `test_validate.py` jsonl schema fixtures | Optional field — existing fixtures remain valid | None |

## Resolution legality

- `routes.<group>.<role>` must not declare `adapter = "in-session"`. The same allOf
  constraint from `dispatchable_role` applies. If a group route resolves to in-session,
  preflight and dispatch both reject it.
- An undeclared group falls through to `roles.<role>` defaults — the overlay is additive,
  not required. Discovery/definition/planning have identical policy to the defaults (Codex
  producer, Claude reviewer) and do not need explicit route entries in the shipped config,
  though consumers may declare them for clarity.
- A group route that declares only `primary` leaves `reviewer` falling back to the base
  default, and vice versa. Each role within a group resolves independently.
- Preflight checks all four groups (not just the two base roles), so a consumer that adds
  a fifth custom group is not validated by default — the four canonical groups are the
  preflight contract.

## Prompt sequence

| # | Prompt summary | Files touched | Tests | Review checkpoint |
|---|---|---|---|---|
| 1 | **(S1)** Schema overlay + route resolver core | `schemas/agents.schema.json`, `schemas/jsonl-events.schema.json`, `src/woof/cli/dispatcher.py` | `tests/unit/test_dispatch.py`: per-group resolution, overlay lookup, profile composition with route override, route_key in dry-run output and audit jsonl | Route resolution override-then-default works; execution group flips adapters correctly; profile overlay composes; `just check` green. |
| 2 | **(S2)** Node group threading | `src/woof/cli/main.py` (or CLI arg parser in dispatcher), `src/woof/graph/nodes.py` | `tests/unit/test_dispatch.py`: `--route-key` accepted by CLI; `tests/unit/test_graph.py` or new: each dispatch call site passes the correct node group string | All four node groups threaded; dispatch argv includes `--route-key <group>`; audit event records the group; `just check` green. |
| 3 | **(S3)** Config correction + preflight + init | `.woof/agents.toml`, `src/woof/cli/init.py`, `src/woof/cli/preflight.py`, `src/woof/cli/commands/observe.py` | `tests/unit/test_preflight.py`: preflight fails for unresolvable node group (missing execution override when base role also absent); preflight passes when all four groups resolve; per-group finding IDs present in JSON output; update `test_preflight_passes_with_mocked_prerequisites` fixture to include route overlay block | All groups pass preflight; inverted Stage-5 config is corrected; existing preflight tests green; `just check` green. |

## Risk register

- Route override silently ignored if lookup key is misspelled: schema `additionalProperties: false` on the `routes` block catches unknown group keys at schema validation time; `ajv validate` rejects the config before dispatch.
- Model-profile overlay applied to wrong adapter after route override: profile overlay must read the resolved base adapter (from route or fallback), not the base-role adapter, to reject incompatible effort values (e.g. Codex rejects `effort: max`). Test this case explicitly in P1.
- Node group strings diverge from schema-declared groups: use a `NODE_GROUPS` constant in dispatcher.py shared with preflight validation; nodes.py imports it rather than duplicating string literals.
- Preflight validates only base roles, misses broken group routes: P3 adds `agents.<group>.<role>.route` finding IDs for all four groups so a broken overlay surfaces before the first `woof wf` invocation.
- `_dispatch_routes_summary` in `observe.py` iterates only `("primary", "reviewer")` hardcoded: P3 extends it to iterate all four groups, keeping the existing base-role display for backward compat and adding a `routes` key in the summary dict.
- Model profile for execution group uses wrong adapter model/effort: the execution override declares Claude as producer (which supports `effort: max`) and Codex as reviewer (which supports only `low`/`medium`/`high`/`xhigh`). Profile entries must match the declared adapter or `_role_effort` will raise. P3 must supply valid effort values for each adapter.
- Existing `test_preflight_passes_with_mocked_prerequisites` snapshot breaks on new finding IDs: P3 must update the snapshot; the four group-route findings are expected to appear in a correctly-configured project.

## Decisions to resolve during the epic

| ID | Decision | Resolution |
|---|---|---|
| D-RG | Do `discovery`/`definition`/`planning` need explicit route entries in the shipped config, or just `execution`? | Only `execution` needs an explicit override (ADR-002 says all others match the defaults). Agents.toml and init template add `execution` only; other groups fall through to base defaults. |
| D-PM | How does a model-profile overlay compose with a route override? | Profile lookup: check `model_profiles.<profile>.routes.<group>.<role>` first, then fall back to `model_profiles.<profile>.roles.<role>`. The merged config uses route override as the base, then applies the most specific profile entry on top. |
| D-NK | Is `NODE_GROUPS` a constant in dispatcher.py or a shared module? | Constant in dispatcher.py; preflight and observe import it from there. No new module needed. |

## Out of scope

- Any new gate type, graph node, or artefact format.
- Per-story or per-bucket route overrides finer than node group.
- A `woof dispatch --dry-run` change to display the route source (overlay vs fallback) in human-readable form — the structured `dry_run` JSON already includes `route_key` and `config_role`.
- Changing how `mapper` or `gate-resolver` roles work.
- Cross-repo consumer edits: E20 changes Woof itself; consumers update their own `agents.toml` independently.

## Done definition

- All three stories' acceptance criteria met.
- All review checkpoints passed.
- `agents.schema.json` validates a config with execution-group overrides via `ajv`.
- `_check_role_routes` in preflight produces `agents.<group>.<role>.route` findings for all four groups.
- `_dispatch_routes_summary` in observe.py reports per-group routes.
- Woof's own `.woof/agents.toml` uses Claude as Stage-5 producer and Codex as Stage-5 reviewer.
- `init.py` AGENTS_TEMPLATE produces a correct ADR-002-compliant scaffold.
- Decisions D-RG, D-PM, and D-NK resolved and encoded in ADR-002 or this plan.
- `just check` green after each prompt.
