---
schema_version: 1
type: vault_foreman_project
default_run_profile: woof-codex-produce-opus-review
run_profiles:
  woof-codex-produce-opus-review:
    producer:
      harness: codex
      model: gpt-5.5
      effort: high
    reviewer:
      harness: claude
      model: opus
      effort: xhigh
---

# VaultForeman

Woof declares its transitional VaultForeman run profile here for any `vf-drain` wave. VaultForeman owns the harness adapters; this repo owns which harness/model/effort fills the producer and reviewer slots.

Backlog `executor` blocks carry drain, timeout, and state-update policy. They do not duplicate the project run-profile fields.
