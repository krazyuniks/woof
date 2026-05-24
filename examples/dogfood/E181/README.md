# E181 Dogfood Evidence

> **Archive status:** This is retained as historical workflow evidence. It is
> not current operator guidance.

E181 covered audit redaction and deterministic audit summaries for `woof dispatch`. The useful evidence is not the original operator prompt; it is the contract, plan, critiques, and event trail that show how the workflow behaved.

## Retained artefacts

| File | Why it is retained |
|---|---|
| `EPIC.md` | Contract for redaction, audit output caps, and the later O2 to O4 contract revision. |
| `plan.json` | Story breakdown showing the config/schema story and the deferred runtime post-processor story. |
| `critique/plan.md` | Reviewer evidence that the original plan was structurally acceptable. |
| `critique/story-S1.md` | Story-level reviewer evidence for the config/schema story. |
| `epic.jsonl` | Audit summary showing plan approval, story completion, the later `story_gate_opened` event, and the revision after the missed blocker. |
| `dispatch.jsonl` | Compact subprocess summary with legacy role and harness names from the pre-ADR-002 implementation. |

## Lesson

The important failure mode is in `epic.jsonl`: story `S2` was later gated and revised because the reviewer had produced a blocker, but the old prompt-owned Stage-5 flow did not halt. That became the concrete evidence for moving critique blocker enforcement into deterministic graph checks.

No raw `audit/` files are retained. They were bulky subprocess transcripts from the consumer checkout and are not needed to inspect the contract or failure mode.
