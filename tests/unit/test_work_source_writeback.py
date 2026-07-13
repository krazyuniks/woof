"""Backlog unit-state writeback to the work-source PM document (ADR-017).

The engine is the only writer of a work unit's ``state:`` in the document the
drain was given, and that document is the only thing it writes outside the
operator home.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

import pytest

from tests.support import DEFAULT_PROJECT_KEY
from woof import state
from woof.graph.state import Plan
from woof.graph.transitions import mark_work_unit_state, write_plan
from woof.graph.work_source import (
    WorkSourceConflictError,
    WorkSourceError,
    content_digest,
    publish_unit_state,
    resolve_work_source,
    unit_states,
    writeback_unit_state,
)

KEY = DEFAULT_PROJECT_KEY
SET_ID = "wave-5"

# A human-authored PM document: comments, quoting styles, blank lines, prose,
# and a unit whose state key is not the last key of its block.
RICH_BACKLOG = """\
---
schema_version: 1
type: backlog
project_ref: woof   # the project this backlog belongs to
status: active
work_units:
  # The first wave. Do not reorder: the drain reads this top-down.
  - id: alpha
    title: "Quote me, and keep the quotes"
    kind: build
    state: todo
    priority: high
    summary: First unit.

  - id: 'beta'
    title: Second unit
    kind: build
    state: "todo"  # trailing comment survives
    priority: medium
    deps: [alpha]
    summary: |
      A block scalar mentioning state: done, which is prose, not a key.
  - id: gamma
    title: Third unit
    kind: build
    priority: low
    state: in_progress
    summary: Third unit.
---

# Wave 5

Prose the operator wrote. It mentions `state: done` in a sentence, and it must
survive a writeback byte-for-byte.

| id | note |
|---|---|
| alpha | first |
"""

# The same document as a Windows-authored file: every line ends CRLF.
CRLF_BACKLOG = RICH_BACKLOG.replace("\n", "\r\n")


def _seed_intake(document: Path, *, set_id: str = SET_ID) -> None:
    directory = state.work_unit_set_dir(KEY, set_id)
    directory.mkdir(parents=True, exist_ok=True)
    state.atomic_write_json(
        directory / "intake.json",
        {
            "schema_version": 1,
            "kind": "pre_decomposed_work_units",
            "source": {"path": document.as_posix(), "source_ref": document.as_posix()},
            "context": {
                "kind": "work_unit_set",
                "project_ref": KEY,
                "set_id": set_id,
                "source_ref": document.as_posix(),
            },
        },
    )


def _write_document(tmp_path: Path, text: str = RICH_BACKLOG) -> Path:
    document = tmp_path / "pm" / "backlog.md"
    document.parent.mkdir(parents=True, exist_ok=True)
    document.write_text(text, encoding="utf-8")
    return document


def _set_context(set_id: str = SET_ID) -> dict[str, str]:
    return {"kind": "work_unit_set", "project_ref": KEY, "set_id": set_id}


def _plan(context: Mapping[str, object], *, unit_state: str = "pending") -> Plan:
    return Plan.model_validate(
        {
            "epic_id": 7,
            "context": context,
            "goal": "Drain wave 5.",
            "work_units": [
                {
                    "id": "alpha",
                    "title": "First unit",
                    "summary": "First unit.",
                    "paths": ["src/**"],
                    "tests": {"count": 1, "types": ["unit"]},
                    "state": unit_state,
                }
            ],
        }
    )


def test_resolve_reads_the_document_the_drain_was_invoked_with(tmp_path: Path) -> None:
    document = _write_document(tmp_path)
    _seed_intake(document)

    assert resolve_work_source(KEY, _set_context()) == document


def test_an_epic_aggregate_has_no_work_source_document(tmp_path: Path) -> None:
    _write_document(tmp_path)

    context = {"kind": "epic", "project_ref": KEY, "epic_id": 7}
    assert resolve_work_source(KEY, context) is None
    assert resolve_work_source(KEY, None) is None


def test_a_run_without_a_work_source_document_writes_back_nothing(tmp_path: Path) -> None:
    """No intake record for the set: writeback is a no-op, not an error."""

    _write_document(tmp_path)

    plan = _plan(_set_context("never-intaked"))
    assert publish_unit_state(KEY, plan, "alpha", "done") is None


def test_writeback_flips_one_state_line_and_preserves_every_other_byte(tmp_path: Path) -> None:
    document = _write_document(tmp_path)

    result = writeback_unit_state(document, "alpha", "done")

    assert result.previous_state == "todo"
    assert result.state == "done"
    assert result.changed is True
    after = document.read_text(encoding="utf-8")
    expected = RICH_BACKLOG.replace("    state: todo\n", "    state: done\n", 1)
    assert after == expected
    before_lines = RICH_BACKLOG.splitlines(keepends=True)
    after_lines = after.splitlines(keepends=True)
    differing = [
        index
        for index, (old, new) in enumerate(zip(before_lines, after_lines, strict=True))
        if old != new
    ]
    assert len(differing) == 1


def test_writeback_preserves_crlf_line_endings_byte_for_byte(tmp_path: Path) -> None:
    """A Windows-authored document keeps every CRLF: the edit is one line, not a reformat."""

    document = tmp_path / "pm" / "backlog.md"
    document.parent.mkdir(parents=True, exist_ok=True)
    document.write_bytes(CRLF_BACKLOG.encode("utf-8"))

    result = writeback_unit_state(document, "alpha", "done")

    assert result.previous_state == "todo"
    assert result.changed is True
    expected = CRLF_BACKLOG.replace("    state: todo\r\n", "    state: done\r\n", 1)
    assert document.read_bytes() == expected.encode("utf-8")
    assert document.read_bytes().count(b"\r\n") == CRLF_BACKLOG.count("\r\n")
    assert b"\n" not in document.read_bytes().replace(b"\r\n", b"")


def test_writeback_accepts_a_document_that_ends_at_the_closing_fence(tmp_path: Path) -> None:
    """Front matter and nothing else: the closing fence is the last byte, with no newline."""

    text = RICH_BACKLOG.split("\n---\n", 1)[0] + "\n---"
    document = _write_document(tmp_path, text)

    result = writeback_unit_state(document, "alpha", "done")

    assert result.changed is True
    assert document.read_text(encoding="utf-8") == text.replace(
        "    state: todo\n", "    state: done\n", 1
    )
    assert unit_states(document.read_text(encoding="utf-8"))["gamma"] == "in_progress"


def test_writeback_preserves_the_quoting_style_and_trailing_comment(tmp_path: Path) -> None:
    document = _write_document(tmp_path)

    writeback_unit_state(document, "beta", "abandoned")

    after = document.read_text(encoding="utf-8")
    assert '    state: "cancelled"  # trailing comment survives\n' in after
    assert after == RICH_BACKLOG.replace(
        '    state: "todo"  # trailing comment survives\n',
        '    state: "cancelled"  # trailing comment survives\n',
        1,
    )


def test_engine_states_map_onto_the_backlog_vocabulary(tmp_path: Path) -> None:
    document = _write_document(tmp_path)

    writeback_unit_state(document, "alpha", "in_progress")
    assert unit_states(document.read_text(encoding="utf-8"))["alpha"] == "in_progress"
    writeback_unit_state(document, "alpha", "done")
    assert unit_states(document.read_text(encoding="utf-8"))["alpha"] == "done"
    writeback_unit_state(document, "alpha", "pending")
    assert unit_states(document.read_text(encoding="utf-8"))["alpha"] == "todo"
    writeback_unit_state(document, "gamma", "abandoned")
    assert unit_states(document.read_text(encoding="utf-8"))["gamma"] == "cancelled"


def test_writeback_of_the_state_already_recorded_changes_nothing(tmp_path: Path) -> None:
    document = _write_document(tmp_path)

    result = writeback_unit_state(document, "gamma", "in_progress")

    assert result.changed is False
    assert document.read_text(encoding="utf-8") == RICH_BACKLOG


def test_writeback_writes_no_sidecar_into_the_documents_repository(tmp_path: Path) -> None:
    document = _write_document(tmp_path)

    writeback_unit_state(document, "alpha", "done")

    assert [path.name for path in sorted(document.parent.iterdir())] == ["backlog.md"]


def test_writeback_fails_closed_when_the_document_changed_since_it_was_read(
    tmp_path: Path,
) -> None:
    document = _write_document(tmp_path)
    stale_digest = content_digest(RICH_BACKLOG)
    document.write_text(
        RICH_BACKLOG.replace("status: active", "status: archived"), encoding="utf-8"
    )
    current = document.read_text(encoding="utf-8")

    with pytest.raises(WorkSourceConflictError):
        writeback_unit_state(document, "alpha", "done", expected_digest=stale_digest)

    assert document.read_text(encoding="utf-8") == current


def test_writeback_fails_closed_when_the_unit_is_not_in_the_document(tmp_path: Path) -> None:
    document = _write_document(tmp_path)

    with pytest.raises(WorkSourceError):
        writeback_unit_state(document, "delta", "done")

    assert document.read_text(encoding="utf-8") == RICH_BACKLOG


def test_concurrent_writebacks_to_one_document_both_land(tmp_path: Path) -> None:
    document = _write_document(tmp_path)

    first = writeback_unit_state(document, "alpha", "done")
    second = writeback_unit_state(
        document, "beta", "done", expected_digest=content_digest(first.text)
    )

    states = unit_states(second.text)
    assert states["alpha"] == "done"
    assert states["beta"] == "done"
    assert states["gamma"] == "in_progress"
    assert document.read_text(encoding="utf-8") == second.text


def test_the_document_lock_lives_in_the_operator_home(tmp_path: Path, woof_home: Path) -> None:
    document = _write_document(tmp_path)

    writeback_unit_state(document, "alpha", "done")

    locks = sorted((woof_home / "locks" / "work-source").glob("*.lock"))
    assert len(locks) == 1
    assert [path.name for path in sorted(document.parent.iterdir())] == ["backlog.md"]


def test_the_engine_state_writer_writes_back_to_the_work_source_document(tmp_path: Path) -> None:
    """The one engine writer of unit state flips the document the drain was given."""

    document = _write_document(tmp_path)
    _seed_intake(document)
    state.epic_dir(KEY, 7).mkdir(parents=True, exist_ok=True)
    write_plan(KEY, _plan(_set_context()))

    mark_work_unit_state(KEY, 7, "alpha", "done")

    assert unit_states(document.read_text(encoding="utf-8"))["alpha"] == "done"
    plan = json.loads(state.plan_path(KEY, 7).read_text(encoding="utf-8"))
    assert plan["work_units"][0]["state"] == "done"


def test_the_engine_state_writer_is_a_no_op_without_a_work_source_document(tmp_path: Path) -> None:
    _write_document(tmp_path)
    state.epic_dir(KEY, 7).mkdir(parents=True, exist_ok=True)
    write_plan(KEY, _plan({"kind": "epic", "project_ref": KEY, "epic_id": 7}))

    mark_work_unit_state(KEY, 7, "alpha", "done")

    plan = json.loads(state.plan_path(KEY, 7).read_text(encoding="utf-8"))
    assert plan["work_units"][0]["state"] == "done"
