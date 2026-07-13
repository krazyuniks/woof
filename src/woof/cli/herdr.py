"""The herdr backend: named-session preflight, lifecycle observation, retained workers.

herdr hosts interactive agent CLIs and owns their lifecycle detection, reporting
working/idle/blocked/done over a versioned JSON socket. This module speaks that
socket and turns it into the three things dispatch needs: a preflight that proves
the server is live and compatible, a turn whose completion is an observed event
rather than a guess at terminal bytes, and a worker whose identity survives the
client that launched it.

Two failures seen on 2026-07-12 shape the design.

A socket file outlives its server. Presence of ``herdr.sock`` proves nothing: a
dead server leaves it on disk, and a transport that treats the file as the
session hands back a path that refuses every connection, forever, with no
self-heal. :func:`socket_alive` connects; only an accepted connection is
liveness. A dead session is reaped and respawned.

A worker outlives its client. Killing a dispatch client leaves the herdr worker
running and detached, and a replacement dispatch then puts two workers in one
working tree editing the same files. Every worker is launched under a stable
name, its pane reference is recorded on disk, and :meth:`HerdrSession.close_worker`
terminates it by that reference rather than by guessing at a process id.

The socket client is injectable, so the orchestration above is driven by a fake
in tests and only the liveness probe touches a real socket.
"""

from __future__ import annotations

import contextlib
import json
import os
import socket as socketlib
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, cast, runtime_checkable

from woof.cli.transport_errors import (
    PayloadAbsent,
    ProtocolMismatch,
    TransportUnavailable,
    WorkerBlocked,
    WorkerTimeout,
)

# The socket-protocol revision this transport is built and verified against. A
# server reporting anything else may have moved the wire shapes underneath us, so
# preflight fails loud rather than corrupting a drain.
HERDR_PROTOCOL = 16

BACKEND = "herdr"
EVIDENCE_LINES = 80
DEFAULT_BOOT_TIMEOUT_S = 15.0
DEFAULT_LIVENESS_TIMEOUT_S = 1.0


class HerdrError(RuntimeError):
    """A herdr API error response (``code`` plus ``message``)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


# --- wire ---------------------------------------------------------------------


class SocketTransport:
    """One newline-delimited JSON connection to a herdr socket."""

    def __init__(self, socket_path: str, *, timeout: float = 30.0) -> None:
        self._sock = socketlib.socket(socketlib.AF_UNIX, socketlib.SOCK_STREAM)
        self._sock.settimeout(timeout)
        self._sock.connect(socket_path)
        self._buf = b""

    def send_line(self, obj: dict[str, Any]) -> None:
        self._sock.sendall((json.dumps(obj) + "\n").encode("utf-8"))

    def recv_line(self, *, timeout: float | None = None) -> dict[str, Any] | None:
        """Return one parsed message, or None on timeout or clean end of stream."""
        old = self._sock.gettimeout()
        if timeout is not None:
            self._sock.settimeout(timeout)
        try:
            while b"\n" not in self._buf:
                try:
                    chunk = self._sock.recv(8192)
                except TimeoutError:
                    return None
                if not chunk:
                    if self._buf:
                        line, self._buf = self._buf, b""
                        return _loads(line)
                    return None
                self._buf += chunk
            line, self._buf = self._buf.split(b"\n", 1)
            return _loads(line)
        finally:
            if timeout is not None:
                self._sock.settimeout(old)

    def close(self) -> None:
        with contextlib.suppress(OSError):
            self._sock.close()


def _loads(line: bytes) -> dict[str, Any]:
    text = line.decode("utf-8").strip()
    if not text:
        return {}
    loaded = json.loads(text)
    if not isinstance(loaded, dict):
        return {"value": loaded}
    return cast(dict[str, Any], loaded)


class EventStream:
    """A live ``pane.agent_status_changed`` subscription for one pane."""

    def __init__(self, transport: SocketTransport) -> None:
        self._transport = transport

    def next_event(self, timeout_s: float) -> dict[str, Any] | None:
        deadline = time.monotonic() + timeout_s
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            message = self._transport.recv_line(timeout=remaining)
            if message is None:
                return None
            if "event" in message:
                return message

    def close(self) -> None:
        self._transport.close()


@runtime_checkable
class EventSource(Protocol):
    def next_event(self, timeout_s: float) -> dict[str, Any] | None: ...
    def close(self) -> None: ...


@runtime_checkable
class HerdrClient(Protocol):
    """The herdr socket-API surface this backend depends on."""

    def ping(self) -> dict[str, Any]: ...
    def start_agent(self, *, name: str, cwd: str, argv: list[str]) -> dict[str, Any]: ...
    def get_agent(self, target: str) -> dict[str, Any]: ...
    def get_status(self, target: str) -> str: ...
    def send_text(self, target: str, text: str) -> None: ...
    def send_keys(self, pane_id: str, keys: list[str]) -> None: ...
    def read_pane(self, target: str, *, source: str, lines: int) -> str: ...
    def close_pane(self, pane_id: str) -> None: ...
    def subscribe(self, pane_id: str) -> EventSource: ...
    def close(self) -> None: ...


class SocketClient:
    """A :class:`HerdrClient` over the herdr socket API.

    herdr closes a request/response connection after its first reply, so every
    call opens a fresh connection. A subscription is the one long-lived
    connection: it keeps its own socket open to stream events.
    """

    def __init__(self, socket_path: str) -> None:
        self._path = socket_path
        self._next_id = 0

    def _call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self._next_id += 1
        request_id = f"woof:{self._next_id}"
        transport = SocketTransport(self._path)
        try:
            transport.send_line({"id": request_id, "method": method, "params": params})
            message = transport.recv_line()
        finally:
            transport.close()
        if message is None:
            raise HerdrError("no_response", f"socket closed awaiting reply to {method}")
        if message.get("id") != request_id:
            raise HerdrError("bad_response", f"reply id mismatch for {method}")
        if "error" in message:
            error = message["error"]
            raise HerdrError(str(error.get("code", "?")), str(error.get("message", "")))
        result = message.get("result")
        return cast(dict[str, Any], result) if isinstance(result, dict) else {}

    def ping(self) -> dict[str, Any]:
        return self._call("ping", {})

    def start_agent(self, *, name: str, cwd: str, argv: list[str]) -> dict[str, Any]:
        result = self._call("agent.start", {"name": name, "cwd": cwd, "argv": argv})
        agent = result.get("agent")
        if not isinstance(agent, dict):
            raise HerdrError("bad_response", "agent.start returned no agent object")
        pane_id = cast(dict[str, Any], agent).get("pane_id")
        if not isinstance(pane_id, str) or not pane_id:
            raise HerdrError("bad_response", "agent.start returned no pane_id")
        return cast(dict[str, Any], agent)

    def get_agent(self, target: str) -> dict[str, Any]:
        """The agent behind a target. herdr resolves a worker name as readily as a pane."""
        result = self._call("agent.get", {"target": target})
        agent = result.get("agent")
        if not isinstance(agent, dict):
            raise HerdrError("bad_response", "agent.get returned no agent object")
        return cast(dict[str, Any], agent)

    def get_status(self, target: str) -> str:
        status = self.get_agent(target).get("agent_status")
        if not isinstance(status, str) or not status:
            raise HerdrError("bad_response", "agent.get returned no agent_status")
        return status

    def send_text(self, target: str, text: str) -> None:
        self._call("agent.send", {"target": target, "text": text})

    def send_keys(self, pane_id: str, keys: list[str]) -> None:
        self._call("pane.send_keys", {"pane_id": pane_id, "keys": keys})

    def read_pane(self, target: str, *, source: str, lines: int) -> str:
        result = self._call(
            "agent.read",
            {
                "target": target,
                "source": source,
                "lines": lines,
                "format": "text",
                "strip_ansi": True,
            },
        )
        read = result.get("read")
        if not isinstance(read, dict):
            raise HerdrError("bad_response", "agent.read returned no read object")
        text = cast(dict[str, Any], read).get("text")
        if not isinstance(text, str):
            raise HerdrError("bad_response", "agent.read returned no text")
        return text

    def close_pane(self, pane_id: str) -> None:
        self._call("pane.close", {"pane_id": pane_id})

    def subscribe(self, pane_id: str) -> EventSource:
        transport = SocketTransport(self._path)
        transport.send_line(
            {
                "id": "subscribe",
                "method": "events.subscribe",
                "params": {
                    "subscriptions": [{"type": "pane.agent_status_changed", "pane_id": pane_id}]
                },
            }
        )
        ack = transport.recv_line()
        if ack is None:
            transport.close()
            raise HerdrError("subscribe_failed", "no ack to events.subscribe")
        if "error" in ack:
            error = ack["error"]
            transport.close()
            raise HerdrError(str(error.get("code", "?")), str(error.get("message", "")))
        return EventStream(transport)

    def close(self) -> None:
        return


# --- session liveness ---------------------------------------------------------


def session_socket_path(session: str) -> Path:
    """The named session's socket (``~/.config/herdr/sessions/<name>/herdr.sock``)."""
    return Path.home() / ".config" / "herdr" / "sessions" / session / "herdr.sock"


def socket_alive(path: Path, *, timeout: float = DEFAULT_LIVENESS_TIMEOUT_S) -> bool:
    """True when something is accepting connections on this socket.

    Liveness is an accepted connection, never the presence of the file. A dead
    server leaves its socket behind and that orphan refuses every connect.
    """
    probe = socketlib.socket(socketlib.AF_UNIX, socketlib.SOCK_STREAM)
    probe.settimeout(timeout)
    try:
        probe.connect(str(path))
    except OSError:
        return False
    finally:
        probe.close()
    return True


def reap_session_socket(path: Path) -> None:
    """Remove a dead session's leftover sockets so a fresh server can bind.

    herdr leaves both the server socket and the sibling client socket behind when
    the server dies; a respawn cannot bind until both are gone.
    """
    path.unlink(missing_ok=True)
    path.with_name("herdr-client.sock").unlink(missing_ok=True)


def launch_session_server(session: str) -> None:
    """Bootstrap a named session's headless server, detached.

    ``HERDR_SOCKET_PATH`` is set explicitly: a process running inside a herdr pane
    inherits the ambient session's socket path, which would silently retarget the
    bootstrap onto the operator's session.
    """
    env = dict(os.environ)
    env["HERDR_SOCKET_PATH"] = str(session_socket_path(session))
    try:
        subprocess.Popen(
            ["herdr", "--session", session, "server"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
            start_new_session=True,
        )
    except FileNotFoundError as exc:
        raise TransportUnavailable(
            "herdr is not on PATH; install it before dispatching to a herdr profile",
            backend=BACKEND,
            session=session,
        ) from exc


def ensure_session(
    session: str,
    *,
    socket_path: Path | None = None,
    boot_timeout_s: float = DEFAULT_BOOT_TIMEOUT_S,
    launch_server: Callable[[str], None] | None = None,
) -> Path:
    """Return the named session's socket, bootstrapping a server if none serves.

    Idempotent: a session that is already serving is reused, never restarted, so
    a dispatch into the operator's live session cannot disturb it. A socket file
    with no listener behind it is a dead session, not a live one: it is reaped and
    the server respawned.
    """
    sock = socket_path if socket_path is not None else session_socket_path(session)
    if sock.exists():
        if socket_alive(sock):
            return sock
        reap_session_socket(sock)
    launcher = launch_server if launch_server is not None else launch_session_server
    launcher(session)
    deadline = time.monotonic() + boot_timeout_s
    while time.monotonic() < deadline:
        if socket_alive(sock):
            return sock
        time.sleep(0.05)
    raise TransportUnavailable(
        f"herdr session {session!r} did not accept connections at {sock} within {boot_timeout_s}s",
        backend=BACKEND,
        session=session,
        socket=str(sock),
    )


@dataclass(frozen=True)
class ServerPreflight:
    """What the running server reported when it was reached."""

    session: str
    socket: str
    version: str
    protocol: int

    def as_payload(self) -> dict[str, Any]:
        return {
            "backend": BACKEND,
            "session": self.session,
            "socket": self.socket,
            "version": self.version,
            "protocol": self.protocol,
        }


def preflight_server(client: HerdrClient, *, session: str, socket: str) -> ServerPreflight:
    """Ping the running server, record its identity, and pin the socket protocol.

    Compatibility is a property of the server actually reached through this named
    session's socket, not of the herdr binary on PATH. A protocol mismatch fails
    here, before any worker is started.
    """
    try:
        pong = client.ping()
    except (HerdrError, OSError) as exc:
        raise TransportUnavailable(
            f"herdr session {session!r} at {socket} did not answer a ping: {exc}",
            backend=BACKEND,
            session=session,
            socket=socket,
        ) from exc
    protocol = pong.get("protocol")
    version = str(pong.get("version") or "unknown")
    if protocol != HERDR_PROTOCOL:
        raise ProtocolMismatch(
            f"herdr server incompatible: this transport requires socket protocol "
            f"{HERDR_PROTOCOL}, but session {session!r} at {socket} reports protocol "
            f"{protocol!r} (herdr {version}). Restart the named session server from "
            f"the installed herdr, or rebuild Woof against the running server.",
            backend=BACKEND,
            session=session,
            socket=socket,
            protocol=protocol if isinstance(protocol, int) else None,
            version=version,
        )
    return ServerPreflight(session=session, socket=socket, version=version, protocol=protocol)


# --- turns --------------------------------------------------------------------


@dataclass(frozen=True)
class TurnResult:
    """How a completed turn ended."""

    completed_on: str
    latency_ms: int


def _event_status(event: dict[str, Any]) -> str:
    data = event.get("data")
    if not isinstance(data, dict):
        return "unknown"
    return str(cast(dict[str, Any], data).get("agent_status", "unknown"))


class HerdrSession:
    """A preflighted herdr named session: start, observe, and close workers."""

    def __init__(
        self,
        client: HerdrClient,
        *,
        session: str,
        socket: str,
        preflight: ServerPreflight,
    ) -> None:
        self._client = client
        self.session = session
        self.socket = socket
        self.preflight = preflight

    # -- worker identity --

    def start_worker(self, *, worker_name: str, cwd: str, argv: list[str]) -> str:
        """Launch a named worker and return the pane reference that addresses it."""
        try:
            agent = self._client.start_agent(name=worker_name, cwd=cwd, argv=argv)
        except (HerdrError, OSError) as exc:
            raise TransportUnavailable(
                f"herdr could not start worker {worker_name!r} in session {self.session!r}: {exc}",
                **self._provenance(),
            ) from exc
        return str(agent["pane_id"])

    def worker_alive(self, pane_id: str) -> bool:
        """True when the recorded worker is still running behind this reference."""
        try:
            self._client.get_status(pane_id)
        except (HerdrError, OSError):
            return False
        return True

    def find_worker(self, worker_name: str) -> str | None:
        """The pane reference of the worker running under this name, if one is.

        The recovery ADR-018 states: a worker is launched under a name derived from
        durable run state, so it stays findable when its identity record does not
        survive -- a round that failed before the record was written, or a wiped
        workers directory. Without this the orphan can be neither reattached to nor
        killed, and the next round puts a second worker in the same working tree.
        """
        try:
            agent = self._client.get_agent(worker_name)
        except (HerdrError, OSError):
            return None
        pane_id = agent.get("pane_id")
        return pane_id if isinstance(pane_id, str) and pane_id else None

    def close_worker(self, pane_id: str) -> None:
        """Terminate the worker addressed by this reference.

        A dispatch that stops without closing leaves a detached worker in the
        working tree, and the replacement dispatch then has two workers editing
        the same files.
        """
        try:
            self._client.close_pane(pane_id)
        except (HerdrError, OSError):
            # An already-dead worker is the state close was asking for.
            return

    def evidence(self, pane_id: str) -> str:
        try:
            return self._client.read_pane(pane_id, source="recent", lines=EVIDENCE_LINES)
        except (HerdrError, OSError):
            return "<pane unreadable>"

    # -- one turn --

    def turn(
        self,
        *,
        pane_id: str,
        kickoff: str,
        payload_ready: Callable[[], bool],
        readiness_timeout_s: int,
        completion_timeout_s: int,
    ) -> TurnResult:
        """Submit one prompt to this worker and observe the turn to completion.

        Observation is armed before the prompt is submitted: the status stream is
        edge-triggered, so a subscription opened after submission can miss a fast
        working -> idle turn outright and then wait out the whole ceiling.
        """
        started = time.perf_counter()
        stream = self._subscribe(pane_id)
        try:
            self._await_readiness(stream, pane_id, readiness_timeout_s)
            self._client.send_text(pane_id, kickoff)
            self._client.send_keys(pane_id, ["Enter"])
            completed_on = self._await_completion(
                stream, pane_id, payload_ready, completion_timeout_s
            )
        finally:
            stream.close()
        return TurnResult(
            completed_on=completed_on,
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    def _subscribe(self, pane_id: str) -> EventSource:
        try:
            return self._client.subscribe(pane_id)
        except (HerdrError, OSError) as exc:
            raise TransportUnavailable(
                f"herdr could not observe worker {pane_id} in session {self.session!r}: {exc}",
                **self._provenance(worker_id=pane_id),
            ) from exc

    def _await_readiness(self, stream: EventSource, pane_id: str, timeout_s: int) -> None:
        """Block until the worker is input-ready.

        A current-status read grounds the start state: a worker that was already
        idle before the subscription armed (the warm producer between fix rounds)
        would otherwise be missed by the edge-triggered stream.
        """
        baseline = self._safe_status(pane_id)
        if baseline == "blocked":
            raise self._blocked(pane_id, "worker blocked at launch")
        if baseline == "idle":
            return
        deadline = time.monotonic() + timeout_s
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise self._timeout(
                    pane_id, f"worker did not reach idle within {timeout_s}s readiness window"
                )
            event = stream.next_event(remaining)
            if event is None:
                raise self._timeout(
                    pane_id, f"worker did not reach idle within {timeout_s}s readiness window"
                )
            status = _event_status(event)
            if status == "blocked":
                raise self._blocked(pane_id, "worker blocked at launch")
            if status == "idle":
                return

    def _await_completion(
        self,
        stream: EventSource,
        pane_id: str,
        payload_ready: Callable[[], bool],
        timeout_s: int,
    ) -> str:
        """Observe the turn to one of four ends: idle, done, blocked, or timeout.

        working -> idle is the turn boundary, but an idle counts as completion only
        once the payload exists: a multi-turn worker idles between tool calls and
        would otherwise false-complete with no answer. ``done`` exits the worker,
        so if the payload is missing there it is missing for good, which is payload
        absence rather than a timeout.
        """
        saw_working = False
        deadline = time.monotonic() + timeout_s
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise self._timeout(pane_id, f"worker did not complete within {timeout_s}s")
            event = stream.next_event(remaining)
            if event is None:
                raise self._timeout(pane_id, f"worker did not complete within {timeout_s}s")
            status = _event_status(event)
            if status == "blocked":
                raise self._blocked(
                    pane_id, "worker blocked (a form, a permission prompt, or a menu wants input)"
                )
            if status == "working":
                saw_working = True
            elif status == "idle":
                if saw_working and payload_ready():
                    return "idle"
            elif status == "done":
                if payload_ready():
                    return "done"
                raise PayloadAbsent(
                    "worker exited without writing its payload",
                    evidence=self.evidence(pane_id),
                    **self._provenance(worker_id=pane_id),
                )

    # -- failures --

    def _safe_status(self, pane_id: str) -> str:
        try:
            return self._client.get_status(pane_id)
        except (HerdrError, OSError):
            # A freshly started pane may not be queryable for a tick; let the
            # event stream carry readiness rather than calling it a failure.
            return "unknown"

    def _blocked(self, pane_id: str, message: str) -> WorkerBlocked:
        return WorkerBlocked(
            message,
            evidence=self.evidence(pane_id),
            **self._provenance(worker_id=pane_id),
        )

    def _timeout(self, pane_id: str, message: str) -> WorkerTimeout:
        return WorkerTimeout(
            message,
            evidence=self.evidence(pane_id),
            **self._provenance(worker_id=pane_id),
        )

    def _provenance(self, *, worker_id: str | None = None) -> dict[str, Any]:
        return {
            "backend": BACKEND,
            "session": self.session,
            "socket": self.socket,
            "protocol": self.preflight.protocol,
            "version": self.preflight.version,
            "worker_id": worker_id,
        }


def open_session(
    session: str,
    *,
    socket_path: Path | None = None,
    client: HerdrClient | None = None,
    launch_server: Callable[[str], None] | None = None,
    boot_timeout_s: float = DEFAULT_BOOT_TIMEOUT_S,
) -> HerdrSession:
    """Ensure the named session serves, preflight it, and return it ready to dispatch."""
    if client is not None:
        socket = str(socket_path or session_socket_path(session))
    else:
        resolved = ensure_session(
            session,
            socket_path=socket_path,
            boot_timeout_s=boot_timeout_s,
            launch_server=launch_server,
        )
        socket = str(resolved)
        client = SocketClient(socket)
    preflight = preflight_server(client, session=session, socket=socket)
    return HerdrSession(client, session=session, socket=socket, preflight=preflight)


__all__ = [
    "BACKEND",
    "HERDR_PROTOCOL",
    "EventSource",
    "EventStream",
    "HerdrClient",
    "HerdrError",
    "HerdrSession",
    "ServerPreflight",
    "SocketClient",
    "TurnResult",
    "ensure_session",
    "launch_session_server",
    "open_session",
    "preflight_server",
    "reap_session_socket",
    "session_socket_path",
    "socket_alive",
]
