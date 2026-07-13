"""The backend-neutral dispatch failure surface.

One typed outcome per way a worker turn can fail, shared by every backend. The
graph consumes ``outcome`` and ``evidence``; it never inspects which transport
produced the failure, and no outcome is named after a backend.

A leaf module: it imports no transport and no registry, so both backends can
normalise their native errors into it without a cycle.
"""

from __future__ import annotations

from typing import Any


class WorkerError(RuntimeError):
    """A dispatch failure carrying its graph outcome and its evidence.

    ``evidence`` is the worker's own output at the moment of failure (the pane
    tail). It is what the operator reads to see why the turn ended, and it is
    persisted with the failure rather than being interpolated into a message and
    lost.
    """

    outcome = "error"

    def __init__(
        self,
        message: str,
        *,
        evidence: str = "",
        backend: str | None = None,
        session: str | None = None,
        socket: str | None = None,
        protocol: int | None = None,
        version: str | None = None,
        worker_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.evidence = evidence
        self.backend = backend
        self.session = session
        self.socket = socket
        self.protocol = protocol
        self.version = version
        self.worker_id = worker_id

    def as_payload(self) -> dict[str, Any]:
        """Return the durable, backend-neutral provenance of this failure."""
        payload: dict[str, Any] = {"outcome": self.outcome, "message": str(self)}
        for key in ("backend", "session", "socket", "protocol", "version", "worker_id"):
            value = getattr(self, key)
            if value is not None:
                payload[f"transport_{key}" if key != "worker_id" else key] = value
        if self.evidence:
            payload["evidence"] = self.evidence
        return payload


class WorkerBlocked(WorkerError):
    """The worker is waiting on human input (a permission prompt, a form, a menu)."""

    outcome = "blocked"


class WorkerTimeout(WorkerError):
    """The worker did not complete its turn within the completion ceiling."""

    outcome = "timeout"


class PayloadAbsent(WorkerError):
    """The worker settled or exited without writing the payload it was asked for.

    Distinct from a timeout: the turn ended, so waiting longer changes nothing.
    Distinct from blocked: nothing is waiting on the operator.
    """

    outcome = "payload_absent"


class ProtocolMismatch(WorkerError):
    """The running server speaks a socket protocol this transport is not built for."""

    outcome = "protocol_mismatch"


class TransportUnavailable(WorkerError):
    """The transport could not be reached at all: no server, no socket, no launch."""

    outcome = "transport_unavailable"


__all__ = [
    "PayloadAbsent",
    "ProtocolMismatch",
    "TransportUnavailable",
    "WorkerBlocked",
    "WorkerError",
    "WorkerTimeout",
]
