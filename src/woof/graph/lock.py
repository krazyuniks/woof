"""Per-epic workflow lock for graph-owned mutation."""

from __future__ import annotations

import json
import os
import socket
import sys
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from woof import state
from woof.graph.transitions import append_epic_event


class WorkflowLockError(RuntimeError):
    """The requested epic is already being mutated by another workflow run."""


@dataclass(frozen=True)
class _OwnedLock:
    path: Path
    token: str


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _process_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _read_lock(path: Path) -> dict | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    return payload if isinstance(payload, dict) else None


def _same_host_stale_lock(path: Path, hostname: str) -> dict | None:
    payload = _read_lock(path)
    if payload is None:
        return None
    pid = payload.get("pid")
    if not isinstance(pid, int) or pid < 1:
        return None
    if payload.get("hostname") != hostname:
        return None
    if _process_running(pid):
        return None
    return payload


def _describe_lock(payload: dict | None) -> str:
    if payload is None:
        return "existing lock has unreadable or malformed metadata"
    parts = []
    for key in ("pid", "hostname", "created_at"):
        value = payload.get(key)
        if value is not None:
            parts.append(f"{key}={value}")
    return ", ".join(parts) if parts else "existing lock has incomplete metadata"


def _write_lock(path: Path, token: str, command: Sequence[str]) -> _OwnedLock:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "pid": os.getpid(),
        "hostname": socket.gethostname(),
        "created_at": _now(),
        "token": token,
        "command": list(command),
    }
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(path, flags, 0o644)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, sort_keys=True)
        handle.write("\n")
    return _OwnedLock(path=path, token=token)


def _release_lock(lock: _OwnedLock) -> None:
    payload = _read_lock(lock.path)
    if payload is None or payload.get("token") != lock.token:
        return
    lock.path.unlink(missing_ok=True)


@contextmanager
def epic_workflow_lock(
    project_key: str,
    epic_id: int,
    *,
    command: Sequence[str] | None = None,
) -> Iterator[None]:
    """Acquire the epic's workflow lock in the operator home for graph-owned mutation."""

    lock_path = state.lock_path(project_key, epic_id)
    hostname = socket.gethostname()
    command = tuple(command or sys.argv)
    stale_events: list[dict] = []
    lock: _OwnedLock | None = None
    while lock is None:
        token = uuid4().hex
        try:
            lock = _write_lock(lock_path, token, command)
        except FileExistsError as exc:
            stale = _same_host_stale_lock(lock_path, hostname)
            if stale is None:
                raise WorkflowLockError(
                    f"E{epic_id} is already locked at {lock_path} ({_describe_lock(_read_lock(lock_path))})"
                ) from exc
            lock_path.unlink(missing_ok=True)
            stale_events.append(
                {
                    "event": "wf_lock_stale_removed",
                    "at": _now(),
                    "epic_id": epic_id,
                    "pid": stale["pid"],
                    "reason": "pid_not_running",
                    "paths": [str(lock_path)],
                    "hostname": hostname,
                }
            )
    try:
        for event in stale_events:
            append_epic_event(project_key, epic_id, event)
        yield
    finally:
        _release_lock(lock)
