---
target: story
target_id: S2
severity: blocker
timestamp: "2026-04-27T05:46:49Z"
harness: codex-gpt-5.3-codex
session_ref: ".woof/epics/E181/audit/cod-critiquer-20260427T054309Z"
findings:
  - id: F1
    severity: blocker
    category: test_quality
    summary: "apply_size_cap uses byte-level slicing that can corrupt UTF-8 multi-byte sequences at chunk boundaries"
    evidence: "The implementation slices at byte boundaries (content[:max_bytes]) without checking for multi-byte character boundaries, violating the UTF-8 invariant that multi-byte sequences must not be split."
    suggestion: "Encode to UTF-8 bytes first, slice, then decode with errors='replace' or find the last valid boundary."
  - id: F2
    severity: blocker
    category: test_quality
    summary: "Tests use only ASCII content and do not exercise the UTF-8 boundary violation described in F1"
    evidence: "All test fixtures pass simple ASCII strings (e.g. 'x' * 1000); no test uses multi-byte UTF-8 characters (e.g. '日本語' or emoji) near the cap boundary."
    suggestion: "Add parametrised tests with multi-byte UTF-8 content that straddles the 262144-byte boundary."
---

## Findings

### F1 — UTF-8 byte-boundary violation in `apply_size_cap` (blocker)

The `apply_size_cap` function slices `content[:max_bytes]` on a Python `str` object that has already been encoded, or alternatively slices raw bytes without considering that UTF-8 encodes non-ASCII characters as 2–4 bytes. Either way, truncating at a byte boundary that falls inside a multi-byte sequence produces an invalid UTF-8 string. Downstream consumers (git, JSON serialisers, the audit writer) will either raise a `UnicodeDecodeError` or silently corrupt the character.

### F2 — Test coverage does not exercise the failure mode (blocker)

The unit tests for `apply_size_cap` use only ASCII input (`'x' * n`). A suite that passes on ASCII but fails on real-world audit content (which contains markdown, code, non-ASCII identifiers, emoji) does not constitute adequate regression coverage for a byte-cap function.

## Position

Both findings are blockers. The story should not land until `apply_size_cap` correctly handles UTF-8 boundaries and the tests demonstrate this with multi-byte fixtures.
