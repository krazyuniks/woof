"""The backend-neutral transport seam.

This is the one module that knows a transport backend exists. A caller hands it a
resolved harness profile; the profile declares the backend, so nothing above this
seam branches on a harness name or a transport. What comes back is a worker, a
turn outcome, and one result shape that is the same whichever backend produced it.

Worker identity is backend-neutral and lives on disk. A retained producer keeps
one identity across its fix rounds, so round two lands in round one's context. A
reviewer is given a fresh identity per round, so no review inherits the last one's
conversation. After process loss the identity is what survives: the seam reattaches
to the recorded worker when it is still alive and respawns under the same name when
it is not. Disk is the authority; the live worker is an attached execution resource.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from woof.cli.harness_registry import (
    BACKEND_HERDR,
    BACKEND_TMUX,
    HarnessProfile,
)
from woof.cli.herdr import HerdrSession
from woof.cli.herdr import open_session as open_herdr_session
from woof.cli.transport_errors import (
    PayloadAbsent,
    TransportUnavailable,
    WorkerTimeout,
)

# The operator's own herdr sessions. Woof never runs a worker in one and never
# tears one down: they carry the operator's live drains, and a Woof run that
# started workers in them (or stopped one) would take that work with it. Woof owns
# the named session it dispatches into, which is what lets it reap the session's
# orphaned sockets and kill the session's stray workers.
PROTECTED_SESSIONS = ("default", "drains")

# There is no implicit session. The named session is declared, because the herdr
# server -- not the client -- spawns the worker: a guessed default would silently
# put Woof's workers inside whichever session happened to be serving.
EVIDENCE_LINES = 80
TMUX_POLL_INTERVAL_S = 1.0
_UNSAFE_NAME = re.compile(r"[^A-Za-z0-9_-]+")

# The kickoff is one line by design. A large prompt pasted into an agentic TUI
# collapses into an attachment chip that Enter will not submit, and a bare Enter
# on multiline input inserts a newline rather than submitting. So the task lives
# in the prompt file and only this pointer is pasted.
#
# A worker asked for an answer writes it to a payload file: the answer of record is
# a file the worker wrote, never text scraped back off the terminal. A worker whose
# task already names the artefact it must write (the producer writes its executor
# result) is pointed at the task alone.
TASK_KICKOFF = (
    "Read the full task at {prompt_path} and carry it out. Do not print the answer in chat."
)
ANSWER_KICKOFF = (
    "Read the full task at {prompt_path} and carry it out; then write only your final "
    "answer (no commentary) to {payload_path} and stop. Do not print the answer in chat."
)


def build_kickoff(prompt_path: Path, payload_path: Path | None = None) -> str:
    """The single line pasted into the worker to start its turn."""
    if payload_path is None:
        return TASK_KICKOFF.format(prompt_path=prompt_path)
    return ANSWER_KICKOFF.format(prompt_path=prompt_path, payload_path=payload_path)


def _safe(value: str, *, fallback: str) -> str:
    return _UNSAFE_NAME.sub("-", value).strip("-_") or fallback


def warm_worker_name(run_id: str, work_unit_id: str, role: str) -> str:
    """The retained producer's worker name: stable across every fix round.

    It is derived from durable run state alone, so the same unit resolves to the
    same worker after a client restart and the producer is reattached rather than
    duplicated.
    """
    return "-".join(
        [
            "woof",
            _safe(run_id, fallback="run"),
            _safe(work_unit_id, fallback="unit"),
            _safe(role, fallback="role"),
        ]
    )


def reviewer_worker_name(run_id: str, work_unit_id: str, *, round_id: int) -> str:
    """A reviewer's worker name: a new one every round.

    Each review round is an independent worker reading the full current diff. It
    never inherits a previous round's context, so a review cannot be shaped by the
    verdict it gave before.
    """
    return "-".join(
        [
            "woof",
            _safe(run_id, fallback="run"),
            _safe(work_unit_id, fallback="unit"),
            "reviewer",
            f"r{round_id}",
        ]
    )


@dataclass(frozen=True)
class WorkerIdentity:
    """Backend-neutral session identity, durable across a client restart.

    ``worker_ref`` is whatever the backend uses to address the worker (a herdr pane
    reference, a tmux session name). The engine never interprets it; it hands it
    back to the backend that issued it.
    """

    backend: str
    worker_name: str
    worker_ref: str
    session: str | None = None
    socket: str | None = None
    protocol: int | None = None
    version: str | None = None

    def as_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "backend": self.backend,
            "worker_name": self.worker_name,
            "worker_ref": self.worker_ref,
        }
        for key in ("session", "socket", "protocol", "version"):
            value = getattr(self, key)
            if value is not None:
                payload[key] = value
        return payload

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> WorkerIdentity | None:
        backend = payload.get("backend")
        name = payload.get("worker_name")
        ref = payload.get("worker_ref")
        if not (isinstance(backend, str) and isinstance(name, str) and isinstance(ref, str)):
            return None
        protocol = payload.get("protocol")
        return cls(
            backend=backend,
            worker_name=name,
            worker_ref=ref,
            session=payload.get("session") if isinstance(payload.get("session"), str) else None,
            socket=payload.get("socket") if isinstance(payload.get("socket"), str) else None,
            protocol=protocol if isinstance(protocol, int) else None,
            version=payload.get("version") if isinstance(payload.get("version"), str) else None,
        )


def save_worker_identity(path: Path, identity: WorkerIdentity) -> None:
    from woof import state

    state.atomic_write_json(path, identity.as_payload())


def load_worker_identity(path: Path) -> WorkerIdentity | None:
    """Return the recorded identity, or None for a cold start.

    A missing or unreadable record is a cold start, not a failure: the engine
    respawns from disk authority rather than refusing to run.
    """
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return WorkerIdentity.from_payload(payload)


def clear_worker_identity(path: Path) -> None:
    path.unlink(missing_ok=True)


@dataclass(frozen=True)
class TurnOutcome:
    """One completed turn, in the shape both backends report."""

    identity: WorkerIdentity
    reattached: bool
    respawned: bool
    completed_on: str
    latency_ms: int
    harness: str
    model: str | None = None
    effort: str | None = None

    def metadata(self) -> dict[str, Any]:
        """The durable, backend-neutral result metadata for this turn."""
        return {
            "backend": self.identity.backend,
            "harness": self.harness,
            "model": self.model,
            "effort": self.effort,
            "worker_name": self.identity.worker_name,
            "worker_ref": self.identity.worker_ref,
            "session": self.identity.session,
            "socket": self.identity.socket,
            "protocol": self.identity.protocol,
            "version": self.identity.version,
            "completed_on": self.completed_on,
            "latency_ms": self.latency_ms,
            "reattached": self.reattached,
            "respawned": self.respawned,
        }


class Backend(Protocol):
    """What the seam needs from a transport. Both backends satisfy it identically."""

    name: str
    harness: str

    def worker_alive(self, worker_ref: str) -> bool: ...
    def start_worker(self, *, worker_name: str, cwd: Path, argv: list[str]) -> str: ...
    def deliver(
        self,
        worker_ref: str,
        *,
        prompt_path: Path,
        kickoff: str,
        payload_ready: Callable[[], bool],
        readiness_timeout_s: int,
        completion_timeout_s: int,
    ) -> tuple[str, int]: ...
    def close(self, worker_ref: str) -> None: ...
    def evidence(self, worker_ref: str) -> str: ...
    def identity(self, worker_name: str, worker_ref: str) -> WorkerIdentity: ...
    def session_name(self) -> str | None: ...


def resolve_backend(profile: HarnessProfile) -> str:
    """The backend this profile runs on. The only place a backend is chosen."""
    return profile.backend


class HerdrBackend:
    """Worker mechanics over a preflighted herdr named session."""

    name = BACKEND_HERDR

    def __init__(self, session: HerdrSession, harness: str) -> None:
        self._session = session
        self.harness = harness

    def worker_alive(self, worker_ref: str) -> bool:
        return self._session.worker_alive(worker_ref)

    def start_worker(self, *, worker_name: str, cwd: Path, argv: list[str]) -> str:
        return self._session.start_worker(worker_name=worker_name, cwd=str(cwd), argv=argv)

    def deliver(
        self,
        worker_ref: str,
        *,
        prompt_path: Path,
        kickoff: str,
        payload_ready: Callable[[], bool],
        readiness_timeout_s: int,
        completion_timeout_s: int,
    ) -> tuple[str, int]:
        result = self._session.turn(
            pane_id=worker_ref,
            kickoff=kickoff,
            payload_ready=payload_ready,
            readiness_timeout_s=readiness_timeout_s,
            completion_timeout_s=completion_timeout_s,
        )
        return result.completed_on, result.latency_ms

    def close(self, worker_ref: str) -> None:
        self._session.close_worker(worker_ref)

    def evidence(self, worker_ref: str) -> str:
        return self._session.evidence(worker_ref)

    def identity(self, worker_name: str, worker_ref: str) -> WorkerIdentity:
        preflight = self._session.preflight
        return WorkerIdentity(
            backend=self.name,
            worker_name=worker_name,
            worker_ref=worker_ref,
            session=self._session.session,
            socket=self._session.socket,
            protocol=preflight.protocol,
            version=preflight.version,
        )

    def session_name(self) -> str | None:
        return self._session.session


class TmuxBackend:
    """Worker mechanics over tmux, for profiles whose TUI has no lifecycle integration.

    tmux reports no semantic lifecycle, so completion is the payload appearing on
    disk and the failure classes are derived from what is observable: a worker whose
    session died without a payload wrote nothing (payload absence), and a worker
    still alive at the ceiling is a timeout. The result metadata is the same shape
    the herdr backend reports.
    """

    name = BACKEND_TMUX

    def __init__(self, api: Any, harness: str) -> None:
        self._tmux = api
        self.harness = harness

    def worker_alive(self, worker_ref: str) -> bool:
        return bool(self._tmux.has_session(worker_ref))

    def start_worker(self, *, worker_name: str, cwd: Path, argv: list[str]) -> str:
        self._tmux.launch_session(worker_name, cwd, argv)
        return worker_name

    def deliver(
        self,
        worker_ref: str,
        *,
        prompt_path: Path,
        kickoff: str,
        payload_ready: Callable[[], bool],
        readiness_timeout_s: int,
        completion_timeout_s: int,
    ) -> tuple[str, int]:
        import time

        started = time.perf_counter()
        self._tmux.wait_for_input_ready(worker_ref, readiness_timeout_s=readiness_timeout_s)
        self._tmux.deliver_prompt_file(worker_ref, prompt_path, kickoff)
        deadline = time.monotonic() + completion_timeout_s
        while time.monotonic() < deadline:
            if payload_ready():
                return "payload", int((time.perf_counter() - started) * 1000)
            if not self._tmux.has_session(worker_ref):
                raise PayloadAbsent(
                    "worker exited without writing its payload",
                    evidence=self.evidence(worker_ref),
                    backend=self.name,
                    worker_id=worker_ref,
                )
            time.sleep(TMUX_POLL_INTERVAL_S)
        if payload_ready():
            return "payload", int((time.perf_counter() - started) * 1000)
        raise WorkerTimeout(
            f"worker did not complete within {completion_timeout_s}s",
            evidence=self.evidence(worker_ref),
            backend=self.name,
            worker_id=worker_ref,
        )

    def close(self, worker_ref: str) -> None:
        self._tmux.kill_session(worker_ref)

    def evidence(self, worker_ref: str) -> str:
        try:
            return str(self._tmux.capture_pane_tail(worker_ref, EVIDENCE_LINES))
        except Exception:  # evidence is best-effort; it must never become the failure
            return "<pane unreadable>"

    def identity(self, worker_name: str, worker_ref: str) -> WorkerIdentity:
        return WorkerIdentity(
            backend=self.name,
            worker_name=worker_name,
            worker_ref=worker_ref,
        )

    def session_name(self) -> str | None:
        return None


def open_backend(
    profile: HarnessProfile,
    *,
    session: str | None = None,
    herdr_session: HerdrSession | None = None,
    tmux_api: Any | None = None,
) -> Backend:
    """Open the backend this profile declares, ready to run workers.

    For herdr this ensures the named session is serving, preflights the running
    server, and pins the socket protocol, so a dead, orphaned, or incompatible
    server fails here rather than halfway through a dispatch.

    The named session must be declared. herdr's server spawns the worker, so a
    guessed default would put Woof's workers inside whatever session was serving --
    including an operator session running live drains, which Woof must never touch.
    """
    backend = resolve_backend(profile)
    if backend != BACKEND_HERDR:
        return TmuxBackend(tmux_api if tmux_api is not None else _tmux_api(), harness=profile.name)
    if herdr_session is not None:
        return HerdrBackend(herdr_session, harness=profile.name)
    if not session:
        raise TransportUnavailable(
            f"harness {profile.name!r} runs on the herdr backend, but no herdr named "
            f"session is declared. Set WOOF_HERDR_SESSION to a session Woof owns; the "
            f"herdr server spawns the worker, so the session is never guessed.",
            backend=BACKEND_HERDR,
        )
    require_woof_owned_session(session)
    return HerdrBackend(open_herdr_session(session), harness=profile.name)


def require_woof_owned_session(session: str) -> None:
    """Refuse a session Woof does not own.

    The operator's sessions carry live drains. Woof starts no worker in one, and
    stops neither the workers nor the server of one.
    """
    if session in PROTECTED_SESSIONS:
        raise TransportUnavailable(
            f"refusing to use herdr session {session!r}: it is an operator session with "
            f"live work. Woof dispatches into a session it owns, so it can reap that "
            f"session's orphaned sockets and kill its stray workers.",
            backend=BACKEND_HERDR,
            session=session,
        )


def _tmux_api() -> Any:
    """The tmux transport, imported only when a tmux profile is actually dispatched."""
    from woof.cli.tmux_backend import tmux_transport

    return tmux_transport()


def run_turn(
    backend: Backend,
    *,
    worker_name: str,
    cwd: Path,
    argv: list[str],
    prompt_path: Path,
    payload_ready: Callable[[], bool],
    readiness_timeout_s: int,
    completion_timeout_s: int,
    payload_path: Path | None = None,
    identity: WorkerIdentity | None = None,
    close_after: bool = False,
    model: str | None = None,
    effort: str | None = None,
) -> TurnOutcome:
    """Run one prompt through a worker: reattach if one is recorded and alive, else start one.

    Pass ``identity`` to keep a retained worker (a producer across fix rounds).
    Pass none, and a fresh worker is started (a reviewer round). ``close_after``
    tears the worker down when the turn ends, which is what makes a reviewer round
    independent and what stops a worker outliving the client that launched it.
    """
    reattached = False
    respawned = False
    if identity is not None and identity.worker_ref and backend.worker_alive(identity.worker_ref):
        worker_ref = identity.worker_ref
        reattached = True
    else:
        # Either a cold start, or the recorded worker is gone: respawn from the
        # disk record's name so the identity survives the process that held it.
        respawned = identity is not None
        worker_ref = backend.start_worker(worker_name=worker_name, cwd=cwd, argv=argv)

    try:
        completed_on, latency_ms = backend.deliver(
            worker_ref,
            prompt_path=prompt_path,
            kickoff=build_kickoff(prompt_path, payload_path),
            payload_ready=payload_ready,
            readiness_timeout_s=readiness_timeout_s,
            completion_timeout_s=completion_timeout_s,
        )
    finally:
        if close_after:
            backend.close(worker_ref)

    return TurnOutcome(
        identity=backend.identity(worker_name, worker_ref),
        reattached=reattached,
        respawned=respawned,
        completed_on=completed_on,
        latency_ms=latency_ms,
        harness=backend.harness,
        model=model,
        effort=effort,
    )


def close_worker(backend: Backend, identity: WorkerIdentity) -> None:
    """Terminate the worker this identity addresses.

    A dispatch that stops without closing leaves the worker running and detached;
    the next dispatch then puts a second worker in the same working tree, both
    editing the same files.
    """
    backend.close(identity.worker_ref)


def teardown_session(
    backend: Backend,
    *,
    identities: list[WorkerIdentity],
    stop_server: Callable[[str], None],
) -> None:
    """Close every worker in a disposable named session, then stop its server.

    Refuses a protected session outright. The operator's live sessions carry
    running drains, and a smoke that stopped one would take them down with it.
    """
    session = backend.session_name()
    if session is None:
        for identity in identities:
            backend.close(identity.worker_ref)
        return
    require_woof_owned_session(session)
    for identity in identities:
        backend.close(identity.worker_ref)
    stop_server(session)


__all__ = [
    "ANSWER_KICKOFF",
    "PROTECTED_SESSIONS",
    "TASK_KICKOFF",
    "Backend",
    "HerdrBackend",
    "TmuxBackend",
    "TurnOutcome",
    "WorkerIdentity",
    "build_kickoff",
    "clear_worker_identity",
    "close_worker",
    "load_worker_identity",
    "open_backend",
    "require_woof_owned_session",
    "resolve_backend",
    "reviewer_worker_name",
    "run_turn",
    "save_worker_identity",
    "teardown_session",
    "warm_worker_name",
]
