"""The herdr backend: preflight, lifecycle observation, worker identity, close.

Every test drives the transport through an injected fake client, so the socket
wire is exercised only by the liveness tests below (which bind a real Unix socket
in a temporary directory and never touch an operator session).
"""

from __future__ import annotations

import socket
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from woof.cli.herdr import (
    HERDR_PROTOCOL,
    HerdrError,
    HerdrSession,
    ServerPreflight,
    ensure_session,
    preflight_server,
    reap_session_socket,
    socket_alive,
)
from woof.cli.transport_errors import (
    PayloadAbsent,
    ProtocolMismatch,
    TransportUnavailable,
    WorkerBlocked,
    WorkerTimeout,
)

PANE = "%7"


class FakeStream:
    """A scripted ``pane.agent_status_changed`` stream.

    An entry is either a status string or a callable returning one, so a test can
    write the payload file at the exact moment a status is delivered. An exhausted
    script returns ``None``, which is how the transport sees a timeout.
    """

    def __init__(self, script: list[str | Callable[[], str]]) -> None:
        self.script = list(script)
        self.closed = False

    def next_event(self, timeout_s: float) -> dict[str, Any] | None:
        if not self.script:
            return None
        entry = self.script.pop(0)
        status = entry() if callable(entry) else entry
        return {"event": "pane.agent_status_changed", "data": {"agent_status": status}}

    def close(self) -> None:
        self.closed = True


class FakeClient:
    """A herdr socket-API double that records the call order."""

    def __init__(
        self,
        *,
        script: list[str | Callable[[], str]] | None = None,
        protocol: int = HERDR_PROTOCOL,
        version: str = "0.7.3",
        baseline: str = "idle",
        panes: list[str] | None = None,
    ) -> None:
        self.script = script or []
        self.protocol = protocol
        self.version = version
        self.baseline = baseline
        self.panes = panes or [PANE]
        self.calls: list[tuple[str, Any]] = []
        self.streams: list[FakeStream] = []
        self.live_panes: set[str] = set()
        self.names: dict[str, str] = {}

    def ping(self) -> dict[str, Any]:
        self.calls.append(("ping", None))
        return {"type": "pong", "version": self.version, "protocol": self.protocol}

    def start_agent(self, *, name: str, cwd: str, argv: list[str]) -> dict[str, Any]:
        pane = self.panes[min(len(self.calls_of("start_agent")), len(self.panes) - 1)]
        self.calls.append(("start_agent", {"name": name, "cwd": cwd, "argv": argv}))
        self.live_panes.add(pane)
        self.names[name] = pane
        return {"pane_id": pane, "name": name}

    def _resolve(self, target: str) -> str:
        """herdr resolves a target that is a worker name as readily as a pane id."""
        return self.names.get(target, target)

    def get_agent(self, target: str) -> dict[str, Any]:
        self.calls.append(("get_agent", target))
        pane = self._resolve(target)
        if pane not in self.live_panes:
            raise HerdrError("not_found", f"no agent for target {target!r}")
        return {"pane_id": pane, "agent_status": self.baseline}

    def get_status(self, target: str) -> str:
        self.calls.append(("get_status", target))
        if self._resolve(target) not in self.live_panes:
            raise HerdrError("not_found", f"no agent for target {target!r}")
        return self.baseline

    def send_text(self, target: str, text: str) -> None:
        self.calls.append(("send_text", {"target": target, "text": text}))

    def send_keys(self, pane_id: str, keys: list[str]) -> None:
        self.calls.append(("send_keys", {"pane_id": pane_id, "keys": keys}))

    def read_pane(self, target: str, *, source: str, lines: int) -> str:
        self.calls.append(("read_pane", target))
        return "PANE TAIL: the worker was here"

    def close_pane(self, pane_id: str) -> None:
        self.calls.append(("close_pane", pane_id))
        self.live_panes.discard(pane_id)

    def subscribe(self, pane_id: str) -> FakeStream:
        self.calls.append(("subscribe", pane_id))
        stream = FakeStream(self.script)
        self.streams.append(stream)
        return stream

    def close(self) -> None:
        self.calls.append(("close", None))

    def calls_of(self, method: str) -> list[Any]:
        return [payload for name, payload in self.calls if name == method]

    def order(self) -> list[str]:
        return [name for name, _ in self.calls]


def make_session(client: FakeClient, *, session: str = "woof-test") -> HerdrSession:
    return HerdrSession(
        client,
        session=session,
        socket=f"/tmp/{session}/herdr.sock",
        preflight=ServerPreflight(
            session=session,
            socket=f"/tmp/{session}/herdr.sock",
            version=client.version,
            protocol=client.protocol,
        ),
    )


def payload_writer(path: Path, status: str = "idle", text: str = "ANSWER") -> Callable[[], str]:
    def _write() -> str:
        path.write_text(text, encoding="utf-8")
        return status

    return _write


def ready(path: Path) -> Callable[[], bool]:
    def _ready() -> bool:
        return path.exists() and path.stat().st_size > 0

    return _ready


# --- preflight: a live server, an orphaned socket, an incompatible protocol ---


def test_preflight_records_socket_version_and_protocol() -> None:
    client = FakeClient()
    result = preflight_server(client, session="woof-test", socket="/tmp/woof-test/herdr.sock")
    assert result.session == "woof-test"
    assert result.socket == "/tmp/woof-test/herdr.sock"
    assert result.version == "0.7.3"
    assert result.protocol == HERDR_PROTOCOL
    assert result.as_payload()["protocol"] == HERDR_PROTOCOL


def test_preflight_fails_on_a_protocol_mismatch_before_any_worker_starts() -> None:
    client = FakeClient(protocol=HERDR_PROTOCOL + 1, version="0.9.0")
    with pytest.raises(ProtocolMismatch) as exc:
        preflight_server(client, session="woof-test", socket="/tmp/woof-test/herdr.sock")
    assert exc.value.protocol == HERDR_PROTOCOL + 1
    assert exc.value.version == "0.9.0"
    assert str(HERDR_PROTOCOL) in str(exc.value)
    assert client.calls_of("start_agent") == []


def test_socket_alive_is_false_for_a_socket_file_with_no_listener(tmp_path: Path) -> None:
    orphan = tmp_path / "herdr.sock"
    orphan.write_bytes(b"")
    assert orphan.exists()
    assert socket_alive(orphan) is False


def test_socket_alive_is_true_when_a_listener_accepts(tmp_path: Path) -> None:
    path = tmp_path / "herdr.sock"
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(path))
    server.listen(1)
    try:
        assert socket_alive(path) is True
    finally:
        server.close()


def test_ensure_session_reaps_an_orphaned_socket_and_respawns(tmp_path: Path) -> None:
    """The 2026-07-12 defect: a socket file outliving its server.

    Presence of the socket file proves nothing. Without a liveness probe the
    orphan is handed back as if it were live and every dispatch afterwards fails
    with a connection refusal that never self-heals.
    """
    sock = tmp_path / "herdr.sock"
    client_sock = tmp_path / "herdr-client.sock"
    sock.write_bytes(b"")
    client_sock.write_bytes(b"")
    launched: list[str] = []
    holder: list[socket.socket] = []

    def launch(session: str) -> None:
        launched.append(session)
        assert not sock.exists(), "the orphaned socket must be reaped before the respawn binds"
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(sock))
        server.listen(1)
        holder.append(server)

    try:
        resolved = ensure_session(
            "woof-test",
            socket_path=sock,
            launch_server=launch,
            boot_timeout_s=5.0,
        )
    finally:
        for server in holder:
            server.close()
    assert resolved == sock
    assert launched == ["woof-test"]
    assert not client_sock.exists()


def test_ensure_session_reuses_a_serving_session_without_respawning(tmp_path: Path) -> None:
    sock = tmp_path / "herdr.sock"
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(sock))
    server.listen(1)
    launched: list[str] = []
    try:
        resolved = ensure_session(
            "woof-test",
            socket_path=sock,
            launch_server=lambda name: launched.append(name),
            boot_timeout_s=5.0,
        )
    finally:
        server.close()
    assert resolved == sock
    assert launched == [], "a serving session must be reused, never restarted"


def test_ensure_session_fails_when_the_respawned_server_never_serves(tmp_path: Path) -> None:
    sock = tmp_path / "herdr.sock"
    with pytest.raises(TransportUnavailable):
        ensure_session(
            "woof-test",
            socket_path=sock,
            launch_server=lambda name: None,
            boot_timeout_s=0.3,
        )


def test_reap_session_socket_removes_both_leftover_sockets(tmp_path: Path) -> None:
    sock = tmp_path / "herdr.sock"
    client_sock = tmp_path / "herdr-client.sock"
    sock.write_bytes(b"")
    client_sock.write_bytes(b"")
    reap_session_socket(sock)
    assert not sock.exists()
    assert not client_sock.exists()


# --- launch, receipt, completion ---


def test_launch_arms_observation_before_the_prompt_is_submitted(tmp_path: Path) -> None:
    payload = tmp_path / "answer.txt"
    client = FakeClient(script=["working", payload_writer(payload)], baseline="idle")
    session = make_session(client)
    pane = session.start_worker(worker_name="woof-p1", cwd=str(tmp_path), argv=["cld"])
    session.turn(
        pane_id=pane,
        kickoff="read the prompt",
        payload_ready=ready(payload),
        readiness_timeout_s=5,
        completion_timeout_s=5,
    )
    order = client.order()
    assert order.index("subscribe") < order.index("send_text"), (
        "the status stream is edge-triggered: arming it after submission can miss "
        "a fast working->idle turn"
    )
    assert order.index("send_text") < order.index("send_keys")


def test_turn_completes_on_working_then_idle_with_the_payload_present(tmp_path: Path) -> None:
    payload = tmp_path / "answer.txt"
    client = FakeClient(script=["working", payload_writer(payload)])
    session = make_session(client)
    pane = session.start_worker(worker_name="woof-p1", cwd=str(tmp_path), argv=["cld"])
    result = session.turn(
        pane_id=pane,
        kickoff="go",
        payload_ready=ready(payload),
        readiness_timeout_s=5,
        completion_timeout_s=5,
    )
    assert result.completed_on == "idle"
    assert client.streams[0].closed is True


def test_an_idle_without_the_payload_does_not_complete_the_turn(tmp_path: Path) -> None:
    """A multi-turn worker idles between tool calls; that is not turn completion."""
    payload = tmp_path / "answer.txt"
    client = FakeClient(
        script=["working", "idle", "working", payload_writer(payload)],
    )
    session = make_session(client)
    pane = session.start_worker(worker_name="woof-p1", cwd=str(tmp_path), argv=["cld"])
    result = session.turn(
        pane_id=pane,
        kickoff="go",
        payload_ready=ready(payload),
        readiness_timeout_s=5,
        completion_timeout_s=5,
    )
    assert result.completed_on == "idle"


def test_a_turn_never_completes_until_the_worker_took_the_prompt_up(tmp_path: Path) -> None:
    """Receipt: an idle before any working means the worker never took the prompt.

    Completing there would trust a worker that had not started, and a stale payload
    from an earlier round would be read back as this round's answer.
    """
    payload = tmp_path / "answer.txt"
    payload.write_text("STALE ANSWER FROM AN EARLIER ROUND", encoding="utf-8")
    client = FakeClient(script=["idle", "idle"])  # settles, but never worked
    session = make_session(client)
    pane = session.start_worker(worker_name="woof-p1", cwd=str(tmp_path), argv=["cld"])
    with pytest.raises(WorkerTimeout):
        session.turn(
            pane_id=pane,
            kickoff="go",
            payload_ready=ready(payload),
            readiness_timeout_s=5,
            completion_timeout_s=5,
        )


def test_done_with_the_payload_completes_the_turn(tmp_path: Path) -> None:
    payload = tmp_path / "answer.txt"
    client = FakeClient(script=["working", payload_writer(payload, status="done")])
    session = make_session(client)
    pane = session.start_worker(worker_name="woof-p1", cwd=str(tmp_path), argv=["cld"])
    result = session.turn(
        pane_id=pane,
        kickoff="go",
        payload_ready=ready(payload),
        readiness_timeout_s=5,
        completion_timeout_s=5,
    )
    assert result.completed_on == "done"


# --- the three distinct failure outcomes, each with evidence ---


def test_a_worker_that_exits_without_the_payload_is_payload_absence(tmp_path: Path) -> None:
    payload = tmp_path / "answer.txt"
    client = FakeClient(script=["working", "done"])
    session = make_session(client)
    pane = session.start_worker(worker_name="woof-p1", cwd=str(tmp_path), argv=["cld"])
    with pytest.raises(PayloadAbsent) as exc:
        session.turn(
            pane_id=pane,
            kickoff="go",
            payload_ready=ready(payload),
            readiness_timeout_s=5,
            completion_timeout_s=5,
        )
    assert exc.value.outcome == "payload_absent"
    assert "PANE TAIL" in exc.value.evidence


def test_a_blocked_worker_is_a_blocked_outcome_with_evidence(tmp_path: Path) -> None:
    payload = tmp_path / "answer.txt"
    client = FakeClient(script=["working", "blocked"])
    session = make_session(client)
    pane = session.start_worker(worker_name="woof-p1", cwd=str(tmp_path), argv=["cld"])
    with pytest.raises(WorkerBlocked) as exc:
        session.turn(
            pane_id=pane,
            kickoff="go",
            payload_ready=ready(payload),
            readiness_timeout_s=5,
            completion_timeout_s=5,
        )
    assert exc.value.outcome == "blocked"
    assert "PANE TAIL" in exc.value.evidence


def test_a_silent_worker_is_a_timeout_outcome_with_evidence(tmp_path: Path) -> None:
    payload = tmp_path / "answer.txt"
    client = FakeClient(script=["working"])  # then the stream runs dry
    session = make_session(client)
    pane = session.start_worker(worker_name="woof-p1", cwd=str(tmp_path), argv=["cld"])
    with pytest.raises(WorkerTimeout) as exc:
        session.turn(
            pane_id=pane,
            kickoff="go",
            payload_ready=ready(payload),
            readiness_timeout_s=5,
            completion_timeout_s=5,
        )
    assert exc.value.outcome == "timeout"
    assert "PANE TAIL" in exc.value.evidence


def test_blocked_timeout_and_payload_absence_are_three_distinct_outcomes() -> None:
    outcomes = {WorkerBlocked.outcome, WorkerTimeout.outcome, PayloadAbsent.outcome}
    assert outcomes == {"blocked", "timeout", "payload_absent"}


def test_a_worker_blocked_at_launch_is_blocked_not_a_readiness_timeout(tmp_path: Path) -> None:
    payload = tmp_path / "answer.txt"
    client = FakeClient(script=[], baseline="blocked")
    session = make_session(client)
    pane = session.start_worker(worker_name="woof-p1", cwd=str(tmp_path), argv=["cld"])
    with pytest.raises(WorkerBlocked):
        session.turn(
            pane_id=pane,
            kickoff="go",
            payload_ready=ready(payload),
            readiness_timeout_s=5,
            completion_timeout_s=5,
        )
    assert client.calls_of("send_text") == [], "a blocked worker must never be sent the prompt"


# --- worker identity and close ---


def test_start_worker_names_the_worker_and_reports_liveness(tmp_path: Path) -> None:
    client = FakeClient()
    session = make_session(client)
    pane = session.start_worker(
        worker_name="woof-run1-unit-producer", cwd=str(tmp_path), argv=["cld"]
    )
    assert client.calls_of("start_agent")[0]["name"] == "woof-run1-unit-producer"
    assert session.worker_alive(pane) is True


def test_close_worker_terminates_the_worker_it_launched(tmp_path: Path) -> None:
    """The 2026-07-12 defect: a worker outliving its client.

    Two workers in one working tree edit the same files. Close must actually
    terminate the pane, addressed by the recorded worker reference.
    """
    client = FakeClient()
    session = make_session(client)
    pane = session.start_worker(worker_name="woof-p1", cwd=str(tmp_path), argv=["cld"])
    session.close_worker(pane)
    assert client.calls_of("close_pane") == [pane]
    assert pane not in client.live_panes
    assert session.worker_alive(pane) is False


# --- preflight surfaces the running server to the operator ---


def test_preflight_finding_reports_a_live_compatible_server(tmp_path: Path) -> None:
    from woof.cli.preflight import check_herdr_server

    client = FakeClient()
    finding = check_herdr_server(
        "woof-test",
        socket_path=tmp_path / "herdr.sock",
        alive=True,
        client=client,
    )
    assert finding.ok is True
    assert "0.7.3" in finding.detail
    assert f"protocol {HERDR_PROTOCOL}" in finding.detail
    assert str(tmp_path / "herdr.sock") in finding.detail


def test_preflight_finding_fails_on_an_incompatible_server(tmp_path: Path) -> None:
    from woof.cli.preflight import check_herdr_server

    client = FakeClient(protocol=HERDR_PROTOCOL + 1, version="0.9.0")
    finding = check_herdr_server(
        "woof-test",
        socket_path=tmp_path / "herdr.sock",
        alive=True,
        client=client,
    )
    assert finding.ok is False
    assert str(HERDR_PROTOCOL) in finding.detail


def test_preflight_finding_calls_an_orphaned_socket_a_dead_session(tmp_path: Path) -> None:
    """A socket file with no listener is not a live server; preflight says so."""
    from woof.cli.preflight import check_herdr_server

    orphan = tmp_path / "herdr.sock"
    orphan.write_bytes(b"")
    finding = check_herdr_server("woof-test", socket_path=orphan, alive=False, client=None)
    assert finding.warn is True, "a dead session is a warning: dispatch reaps and respawns it"
    assert "no listener" in finding.detail
    assert "orphaned socket left by a dead server" in finding.detail


def test_a_declared_session_is_checked_and_an_undeclared_one_is_not(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The named session is a runtime choice, so preflight checks it only when declared."""
    from woof.cli import preflight as preflight_mod
    from woof.cli.transport import SESSION_ENV

    monkeypatch.delenv(SESSION_ENV, raising=False)
    assert preflight_mod._check_declared_herdr_server() is None

    monkeypatch.setenv(SESSION_ENV, "woof-not-serving")
    finding = preflight_mod._check_declared_herdr_server()
    assert finding is not None
    assert finding.id == "dispatch.herdr.server"
    assert finding.warn is True  # nothing is serving it; dispatch respawns
