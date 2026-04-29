---
epic_id: 146
title: Comment editing — contract-fidelity fixture
observable_outcomes:
  - id: O1
    statement: A comment author can edit the body of their own comment.
    verification: automated
  - id: O2
    statement: Every edit produces an immutable audit event.
    verification: automated
contract_decisions:
  - id: CD1
    related_outcomes: [O1]
    title: Edit-comment route
    openapi_ref: tests/fixtures/woof/e146/spec/openapi.yaml#/paths/~1api~1v1~1comments~1{id}/patch
  - id: CD2
    related_outcomes: [O1]
    title: Edit-comment payload
    pydantic_ref: tests/fixtures/woof/e146/webapp/comment_schema.py:CommentEdit
  - id: CD3
    related_outcomes: [O2]
    title: Edit audit event
    json_schema_ref: tests/fixtures/woof/e146/schemas/audit-event.schema.json
acceptance_criteria:
  - All three contract decisions verify under `woof check-cd`.
  - The PATCH route returns 200 with the updated Comment body on success.
  - Audit events validate against the audit-event JSON Schema.
---

This fixture pins the E146 lesson into a regression test: every contract decision
points at a real artefact that woof can verify deterministically. If a CD ever
drifts (route renamed, model removed, schema deleted), `woof check-cd` flags
it before the planner can substitute repo convention.
