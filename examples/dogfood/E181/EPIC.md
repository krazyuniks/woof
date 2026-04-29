---
epic_id: 181
title: Audit redaction + deterministic summary (woof dispatch)
observable_outcomes:
  - id: O1
    statement: A dispatched subprocess whose audit output contains a known secret pattern lands on disk with the secret replaced by a [REDACTED:<reason>] marker, and the post-redaction file passes a no-secrets grep.
    verification: automated
  - id: O2
    statement: A dispatched subprocess whose stdout exceeds the configured per-file cap (default 256 KB) lands on disk truncated to the cap with a "... [truncated, full output at .woof/epics/E<N>/audit/raw/]" footer; the raw file is present at the gitignored raw path.
    verification: automated
    deprecated: true
    replaced_by: O4
  - id: O3
    statement: An operator can disable redaction or raise the cap via .woof/agents.toml without re-deploying the woof CLI.
    verification: automated
  - id: O4
    statement: A dispatched subprocess writes its raw stdout to a gitignored .woof/epics/E<N>/audit/raw/<role>-<ts>.output (full fidelity), then a deterministic post-processor produces the committed .woof/epics/E<N>/audit/<role>-<ts>.output by applying redaction and dropping the bulk of harness-emitted command-execution output (keeping the command, exit_code, and output length while removing the inlined aggregated_output) so that agent_message and harness result content survive intact; if the post-processed file still exceeds the configured per-file cap, it is tail-truncated with a "... [head truncated, full output at .woof/epics/E<N>/audit/raw/]" footer.
    verification: automated
contract_decisions:
  - id: CD1
    related_outcomes: [O1, O3, O4]
    title: Audit redaction policy schema
    json_schema_ref: woof/schemas/agents.schema.json
    notes: |
      The redaction patterns and per-file cap live under a new
      [audit] block in agents.toml; the agents schema gains an
      optional `audit` property documenting both knobs. CD points
      at the existing schema once that block is added.
acceptance_criteria:
  - "Every dispatched subprocess has a redacted, post-processed .output (committed) and a raw .output (gitignored under audit/raw/)."
  - "The redaction filter is conservative (false positives leave a [REDACTED] marker; false negatives are an audit failure)."
  - "The deterministic post-processor extracts the harness's final result text into the .meta file's result_text field and removes inlined command-execution aggregated_output content from the committed .output, preserving agent_message and harness result events."
  - "Per-file cap is configurable via .woof/agents.toml [audit].max_bytes; default 262144. Cap acts as a safety net for pathological cases; under typical operation the post-processor keeps committed .output well under the cap."
  - "When the cap engages, the strategy is tail-truncate (keep the last max_bytes bytes); the head-truncated overflow is recoverable via the gitignored audit/raw/ path."
  - "A unit test feeds a known JWT/AWS-key/bearer-token pattern through the redactor and asserts the output is scrubbed."
  - "A unit test feeds a recorded codex JSONL fixture through the post-processor and asserts (a) command_execution aggregated_output is dropped, (b) agent_message and turn.completed events survive, (c) byte size is meaningfully smaller than the input."
  - "A unit test dispatches a stub harness whose post-processed stdout still exceeds the cap, and asserts tail-truncation + raw-file landing + footer text."
  - "just test-woof is green on completion."
---

# Audit redaction + deterministic summary

The first dogfood epic for woof. Task 2 (`woof dispatch`) shipped without
the redaction filter and size cap that Workflow.md §"Audit redaction and
retention" calls non-negotiable for committed audit files. Without it,
codex prompt/output transcripts that land in the repo can leak the
contents of `env.local.sh`, JWT bearer tokens captured during a flow,
and any `.gts-auth.json` token blobs read by a tool the subprocess
invoked.

The fix has three halves: **conservative redaction** of known secret
shapes before the file lands on disk, a **deterministic post-processor**
that produces a small committed transcript by dropping the bulk of
codex-emitted `command_execution.aggregated_output` content (where most
of the bloat lives — codex inlines every shell command's full stdout),
and a **per-file size cap** as a safety net for pathological cases with
overflow to a gitignored `audit/raw/` path so nothing is lost.

The original design (O2, deprecated) capped raw output at 256 KB with
a head-keep strategy. That stripped the most diagnostically useful part
of any audit (the harness's terminal `result` event and the model's
final `agent_message` both live at the tail). E182 Discovery surfaced
the misframing; O4 replaces O2 with the deterministic-summary approach.
Implementation hasn't shipped yet (S2 was reverted in `e5d42c37`), so
the revision costs nothing beyond the EPIC + plan edits.

This is exactly the kind of work woof itself should help build: small
scope, clear contract, deterministic verification. Driving it through
the full Discovery → Definition → Plan → Execute pipeline is the first
real proof that the orchestrator + dispatch + check-cd + driver chain
works end-to-end.
