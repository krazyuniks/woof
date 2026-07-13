"""A transport backend with no transport, and the scaffolding to dispatch onto it.

``woof dispatch`` runs in process against this: the real audit path, the real
transport seam, and a backend that records what the seam asked it to do. No
socket, no terminal, and no worker exist, so a dispatch failure can be scripted
exactly and the durable record it leaves can be read back.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from tests.support import DEFAULT_PROJECT_KEY
from woof import state
from woof.cli import transport
from woof.cli.harness_registry import BACKEND_HERDR
from woof.cli.transport import WorkerIdentity

KEY = DEFAULT_PROJECT_KEY
EPIC = 41
UNIT = "unit-a"


class RecordingBackend:
    """Satisfies the seam's Backend protocol; scripts one turn outcome per round.

    A ``turns`` entry is either the ``(completed_on, latency_ms)`` a turn returned
    or an exception it raised, so a blocked round, a timeout, or a socket fault is
    a value the test writes down.
    """

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
    """A git checkout to dispatch from; the operator home is already isolated."""
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
    """Give the dispatcher this backend instead of opening a real one."""
    monkeypatch.setattr(transport, "open_backend", lambda *args, **kwargs: backend)


def dispatch_events() -> list[dict[str, Any]]:
    path = state.dispatch_events_path(KEY, EPIC)
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def latest_meta() -> dict[str, Any]:
    metas = sorted(state.audit_dir(KEY, EPIC).glob("*.meta"))
    assert metas, "a dispatch must leave an audit record"
    return json.loads(metas[-1].read_text(encoding="utf-8"))
