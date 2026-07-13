"""A dispatch that dies must still leave a durable record.

The engine classifies an attempt's outcome from the dispatch record. A transport
fault that killed the CLI before the record was written left the attempt invisible
to it, so the audit trail lied by omission.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.unit.dispatch_backend import (
    RecordingBackend,
    dispatch_args,
    dispatch_events,
    install,
    latest_meta,
    project,  # noqa: F401  (fixture)
)
from woof.cli.dispatcher import cmd_dispatch
from woof.cli.herdr import HerdrError

pytestmark = pytest.mark.host_only


def test_a_socket_fault_mid_turn_still_records_the_one_shot_dispatch(
    project: Path,  # noqa: F811
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A herdr fault is not a typed worker failure, so it used to escape the audit.

    Every one-shot role -- reviewer, mapper, enrichment -- delivers its prompt over
    a fresh socket connection. A server that dies mid-turn raises HerdrError, a
    RuntimeError: it crashed the CLI before the dispatch record was written, and the
    engine had nothing to classify the attempt from.
    """
    backend = RecordingBackend(harness="claude", turns=[HerdrError("io", "socket closed")])
    install(monkeypatch, backend)

    code = cmd_dispatch(dispatch_args(tmp_path, role="reviewer", session_mode="one-shot"))

    assert code == 1
    meta = latest_meta()
    assert meta["exit_type"] == "nonzero"
    assert meta["exit_code"] == 1
    assert meta["timed_out"] is False
    returned = [event for event in dispatch_events() if event["event"] == "subprocess_returned"]
    assert len(returned) == 1, "the dispatch record is what the engine classifies the outcome from"
    assert returned[0]["exit_type"] == "nonzero"
    stderr = Path(json.loads(Path(meta["attempt_path"]).read_text())["audit_paths"]["stderr"])
    assert "socket closed" in stderr.read_text(encoding="utf-8"), (
        "the fault that ended the dispatch must be readable from the record"
    )
