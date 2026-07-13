"""A retained producer must be recoverable: by its record, or by its stable name.

These drive ``woof dispatch`` in process over a recording backend, so the real
audit path and the real seam run while no socket, no terminal, and no worker
exist. The failure they guard is the 2026-07-12 incident: a worker outliving the
dispatch that launched it, and a replacement putting two workers in one working
tree.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from tests.support import DEFAULT_PROJECT_KEY
from woof import state
from woof.cli import transport
from woof.cli.dispatcher import close_retained_worker, cmd_dispatch, ensure_run_metadata
from woof.cli.harness_registry import BACKEND_HERDR
from woof.cli.transport import WorkerIdentity
from woof.cli.transport_errors import WorkerBlocked

pytestmark = pytest.mark.host_only

KEY = DEFAULT_PROJECT_KEY
EPIC = 41
UNIT = "unit-a"


class RecordingBackend:
    """A transport backend with no transport: it records what the seam asked for."""

    name = BACKEND_HERDR

    def __init__(self, *, harness: str = "codex", turns: list[Any] | None = None) -> None:
        self.harness = harness
        self.turns = list(turns or [])
        self.started: list[str] = []
        self.closed: list[str] = []
        self.refs: dict[str, str] = {}
        self.live: set[str] = set()

    def worker_alive(self, worker_ref: str) -> bool:
        return worker_ref in self.live

    def start_worker(self, *, worker_name: str, cwd: Path, argv: list[str]) -> str:
        worker_ref = f"%{len(self.started) + 1}"
        self.started.append(worker_name)
        self.refs[worker_name] = worker_ref
        self.live.add(worker_ref)
        return worker_ref

    def find_worker(self, worker_name: str) -> str | None:
        worker_ref = self.refs.get(worker_name)
        return worker_ref if worker_ref is not None and worker_ref in self.live else None

    def deliver(self, worker_ref: str, **kwargs: Any) -> tuple[str, int]:
        turn = self.turns.pop(0) if self.turns else ("idle", 10)
        if isinstance(turn, BaseException):
            raise turn
        return turn

    def close(self, worker_ref: str) -> None:
        self.closed.append(worker_ref)
        self.live.discard(worker_ref)

    def evidence(self, worker_ref: str) -> str:
        return "PANE TAIL: the worker was here"

    def identity(self, worker_name: str, worker_ref: str) -> WorkerIdentity:
        return WorkerIdentity(
            backend=self.name,
            worker_name=worker_name,
            worker_ref=worker_ref,
            session="woof-test",
        )

    def session_name(self) -> str | None:
        return "woof-test"


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    checkout = tmp_path / "proj"
    checkout.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=checkout, check=True)
    monkeypatch.chdir(checkout)
    return checkout


def dispatch_args(tmp_path: Path, *, role: str, session_mode: str) -> argparse.Namespace:
    prompt = tmp_path / "task.prompt"
    prompt.write_text("do the thing\n", encoding="utf-8")
    return argparse.Namespace(
        project=None,
        target=None,
        role=role,
        epic=EPIC,
        work_unit=UNIT,
        route_key=None,
        session_mode=session_mode,
        close_worker=False,
        prompt_file=str(prompt),
        artefacts_loaded=[],
        dry_run=False,
    )


def install(monkeypatch: pytest.MonkeyPatch, backend: RecordingBackend) -> None:
    monkeypatch.setattr(transport, "open_backend", lambda *a, **kw: backend)


def dispatch_events() -> list[dict[str, Any]]:
    path = state.dispatch_events_path(KEY, EPIC)
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def latest_meta() -> dict[str, Any]:
    metas = sorted(state.audit_dir(KEY, EPIC).glob("*.meta"))
    assert metas, "a dispatch must leave an audit record"
    return json.loads(metas[-1].read_text(encoding="utf-8"))


# --- a failed round must not orphan the producer ---


def test_a_blocked_first_round_records_the_producer_so_the_next_round_reattaches(
    project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The B3 defect: the identity was saved only after a turn that succeeded.

    A produce round that ends blocked, timed out, or payload-absent leaves the
    worker alive (a retained producer is not closed), but the record was never
    written, so the next round cold-started a second worker into the same working
    tree -- the 2026-07-12 incident this unit exists to prevent.
    """
    backend = RecordingBackend(turns=[WorkerBlocked("a permission prompt wants input")])
    install(monkeypatch, backend)
    args = dispatch_args(tmp_path, role="primary", session_mode="warm-producer")

    first = cmd_dispatch(args)

    assert first != 0, "a blocked round is a failed round"
    run_id = ensure_run_metadata(KEY, EPIC, datetime.now(UTC))
    worker_name = transport.warm_worker_name(run_id, UNIT, "primary")
    record = state.worker_identity_path(KEY, EPIC, worker_name)
    identity = transport.load_worker_identity(record)
    assert identity is not None, "the started producer must be recorded even when the round fails"
    assert identity.worker_ref == "%1"
    assert backend.closed == [], "a retained producer is not closed by a failed round"

    backend.turns = [("idle", 12)]
    second = cmd_dispatch(args)

    assert second == 0
    assert backend.started == [worker_name], (
        "the next round must reattach to the recorded producer, not start a second worker"
    )
    assert latest_meta()["producer_worker_reattached"] is True


def test_a_producer_whose_record_was_lost_is_recovered_by_its_stable_name(
    project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR-018's recovery: a record-less worker is found by name, not duplicated.

    The record can be absent for reasons the engine does not control (a wiped
    workers directory, a client killed between start and save). Reattachment by
    recorded reference alone leaves that worker orphaned and puts its replacement
    in the same working tree.
    """
    backend = RecordingBackend(turns=[WorkerBlocked("blocked")])
    install(monkeypatch, backend)
    args = dispatch_args(tmp_path, role="primary", session_mode="warm-producer")
    cmd_dispatch(args)

    run_id = ensure_run_metadata(KEY, EPIC, datetime.now(UTC))
    worker_name = transport.warm_worker_name(run_id, UNIT, "primary")
    record = state.worker_identity_path(KEY, EPIC, worker_name)
    record.unlink()  # the record is gone; the worker is still running

    backend.turns = [("idle", 12)]
    assert cmd_dispatch(args) == 0

    assert backend.started == [worker_name], (
        "a live worker under the stable name must be adopted, not duplicated"
    )
    assert transport.load_worker_identity(record) is not None, "recovery re-records the worker"


def test_close_worker_finds_a_record_less_worker_by_its_stable_name(
    project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The explicit close must reach the worker the ADR says it can reach by name."""
    backend = RecordingBackend()
    worker_name = "woof-run1-unit-a-primary"
    worker_ref = backend.start_worker(worker_name=worker_name, cwd=tmp_path, argv=["cld"])
    record = state.worker_identity_path(KEY, EPIC, worker_name)

    closed = close_retained_worker(record, backend=backend, worker_name=worker_name)

    assert closed is True
    assert backend.closed == [worker_ref]


def test_closing_an_absent_worker_is_still_a_no_op(
    project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = RecordingBackend()
    record = state.worker_identity_path(KEY, EPIC, "woof-absent")
    assert close_retained_worker(record, backend=backend, worker_name="woof-absent") is False
    assert backend.closed == []
