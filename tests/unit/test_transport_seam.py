"""The backend-neutral transport seam.

The seam is the one place that knows a backend exists. Callers hand it a resolved
harness profile and get a worker; the profile carries the backend, so no caller
branches on a harness name or a transport. These tests drive the herdr backend
through an injected fake client and the tmux backend through an injected fake
transport API, so neither a socket nor a terminal is needed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests.unit.test_herdr_transport import FakeClient, make_session
from woof.cli.harness_registry import BACKEND_HERDR, BACKEND_TMUX, get_profile
from woof.cli.transport import (
    PROTECTED_SESSIONS,
    WorkerIdentity,
    clear_worker_identity,
    close_worker,
    load_worker_identity,
    open_backend,
    resolve_backend,
    reviewer_worker_name,
    run_turn,
    save_worker_identity,
    teardown_session,
    warm_worker_name,
)
from woof.cli.transport_errors import TransportUnavailable


class FakeTmux:
    """The slice of the tmux transport the seam uses."""

    def __init__(self, *, live: set[str] | None = None) -> None:
        self.live = live or set()
        self.calls: list[tuple[str, Any]] = []

    def has_session(self, session: str) -> bool:
        self.calls.append(("has_session", session))
        return session in self.live

    def launch_session(self, session: str, cwd: Path, argv: list[str]) -> None:
        self.calls.append(("launch_session", {"session": session, "cwd": str(cwd), "argv": argv}))
        self.live.add(session)

    def wait_for_input_ready(self, session: str, *, readiness_timeout_s: int) -> None:
        self.calls.append(("wait_for_input_ready", session))

    def deliver_prompt_file(self, session: str, prompt_path: Path) -> None:
        self.calls.append(("deliver_prompt_file", session))

    def capture_pane_tail(self, session: str, lines: int) -> str:
        self.calls.append(("capture_pane_tail", session))
        return "PANE TAIL"

    def kill_session(self, session: str) -> None:
        self.calls.append(("kill_session", session))
        self.live.discard(session)

    def calls_of(self, method: str) -> list[Any]:
        return [payload for name, payload in self.calls if name == method]


def herdr_backend(client: FakeClient, session: str = "woof-test") -> Any:
    return open_backend(
        get_profile("claude"),
        session=session,
        herdr_session=make_session(client, session=session),
    )


def tmux_backend(tmux: FakeTmux) -> Any:
    return open_backend(get_profile("reasonix"), tmux_api=tmux)


def written(path: Path, text: str = "ANSWER") -> Any:
    def _write() -> str:
        path.write_text(text, encoding="utf-8")
        return "idle"

    return _write


def payload_ready(path: Path) -> Any:
    return lambda: path.exists() and path.stat().st_size > 0


# --- the backend is resolved from the profile, never from a harness name ---


def test_a_herdr_profile_resolves_to_the_herdr_backend() -> None:
    assert resolve_backend(get_profile("claude")) == BACKEND_HERDR
    assert resolve_backend(get_profile("codex")) == BACKEND_HERDR


def test_a_tmux_profile_resolves_to_the_tmux_backend() -> None:
    assert resolve_backend(get_profile("reasonix")) == BACKEND_TMUX
    assert resolve_backend(get_profile("pi")) == BACKEND_TMUX


def test_both_backends_report_the_same_result_shape(tmp_path: Path) -> None:
    """Backend-equivalent result metadata: same keys, no field named after a backend."""
    payload = tmp_path / "answer.txt"
    client = FakeClient(script=["working", written(payload)])
    herdr = run_turn(
        herdr_backend(client),
        worker_name="woof-a",
        cwd=tmp_path,
        argv=["cld"],
        prompt_path=tmp_path / "p.txt",
        payload_ready=payload_ready(payload),
        readiness_timeout_s=5,
        completion_timeout_s=5,
    )
    tmux_payload = tmp_path / "answer-tmux.txt"
    tmux_payload.write_text("ANSWER", encoding="utf-8")
    tmux = run_turn(
        tmux_backend(FakeTmux()),
        worker_name="woof-b",
        cwd=tmp_path,
        argv=["reasonix"],
        prompt_path=tmp_path / "p.txt",
        payload_ready=payload_ready(tmux_payload),
        readiness_timeout_s=5,
        completion_timeout_s=5,
    )
    assert herdr.metadata().keys() == tmux.metadata().keys()
    for key in list(herdr.metadata()) + list(tmux.metadata()):
        assert "tmux" not in key, f"public result field {key!r} is named after one backend"
        assert "herdr" not in key, f"public result field {key!r} is named after one backend"
    assert herdr.metadata()["backend"] == BACKEND_HERDR
    assert tmux.metadata()["backend"] == BACKEND_TMUX


# --- a warm producer keeps one worker identity; a reviewer gets a fresh one ---


def test_the_producer_worker_name_is_stable_across_fix_rounds() -> None:
    first = warm_worker_name("run-1", "unit-a", "primary")
    second = warm_worker_name("run-1", "unit-a", "primary")
    assert first == second


def test_every_reviewer_round_gets_its_own_worker_name() -> None:
    first = reviewer_worker_name("run-1", "unit-a", round_id=1)
    second = reviewer_worker_name("run-1", "unit-a", round_id=2)
    assert first != second


def test_a_herdr_producer_keeps_one_worker_across_fix_rounds(tmp_path: Path) -> None:
    """The warm producer's whole point: round two lands in round one's context."""
    client = FakeClient()
    backend = herdr_backend(client)
    record = tmp_path / "worker.json"
    name = warm_worker_name("run-1", "unit-a", "primary")

    outcomes = []
    for round_id in (1, 2, 3):
        payload = tmp_path / f"answer-{round_id}.txt"
        client.script = ["working", written(payload)]
        outcome = run_turn(
            backend,
            worker_name=name,
            cwd=tmp_path,
            argv=["cld"],
            prompt_path=tmp_path / f"p{round_id}.txt",
            payload_ready=payload_ready(payload),
            readiness_timeout_s=5,
            completion_timeout_s=5,
            identity=load_worker_identity(record),
        )
        save_worker_identity(record, outcome.identity)
        outcomes.append(outcome)

    assert len(client.calls_of("start_agent")) == 1, (
        "a fix round must reattach to the producer, not start a second worker"
    )
    assert {outcome.identity.worker_ref for outcome in outcomes} == {"%7"}
    assert {outcome.identity.worker_name for outcome in outcomes} == {name}
    assert [o.reattached for o in outcomes] == [False, True, True]
    assert client.calls_of("close_pane") == [], "the producer stays open between fix rounds"


def test_every_reviewer_round_starts_and_closes_a_fresh_worker(tmp_path: Path) -> None:
    client = FakeClient(panes=["%1", "%2"])
    backend = herdr_backend(client)
    refs = []
    for round_id in (1, 2):
        payload = tmp_path / f"review-{round_id}.txt"
        client.script = ["working", written(payload)]
        outcome = run_turn(
            backend,
            worker_name=reviewer_worker_name("run-1", "unit-a", round_id=round_id),
            cwd=tmp_path,
            argv=["cld"],
            prompt_path=tmp_path / f"r{round_id}.txt",
            payload_ready=payload_ready(payload),
            readiness_timeout_s=5,
            completion_timeout_s=5,
            identity=None,  # a reviewer never reattaches
            close_after=True,
        )
        refs.append(outcome.identity.worker_ref)

    assert len(client.calls_of("start_agent")) == 2
    assert refs == ["%1", "%2"], "each review round is an independent worker"
    assert client.calls_of("close_pane") == ["%1", "%2"], "each review worker is torn down"


# --- resume: reattach when safe, respawn from disk authority after process loss ---


def test_worker_identity_round_trips_through_disk_with_no_backend_named_field(
    tmp_path: Path,
) -> None:
    record = tmp_path / "worker.json"
    identity = WorkerIdentity(
        backend=BACKEND_HERDR,
        worker_name="woof-run1-unit-primary",
        worker_ref="%7",
        session="drains",
        socket="/run/herdr.sock",
        protocol=16,
        version="0.7.3",
    )
    save_worker_identity(record, identity)
    assert load_worker_identity(record) == identity
    stored = json.loads(record.read_text())
    for key in stored:
        assert "tmux" not in key and "herdr" not in key, (
            f"persisted identity field {key!r} is named after one backend"
        )


def test_a_lost_worker_respawns_under_the_same_name_from_the_disk_record(
    tmp_path: Path,
) -> None:
    """Process loss: the record survives, the worker does not. Respawn, do not reattach."""
    client = FakeClient(panes=["%1", "%2"])
    backend = herdr_backend(client)
    record = tmp_path / "worker.json"
    name = warm_worker_name("run-1", "unit-a", "primary")

    payload = tmp_path / "a1.txt"
    client.script = ["working", written(payload)]
    first = run_turn(
        backend,
        worker_name=name,
        cwd=tmp_path,
        argv=["cld"],
        prompt_path=tmp_path / "p1.txt",
        payload_ready=payload_ready(payload),
        readiness_timeout_s=5,
        completion_timeout_s=5,
        identity=load_worker_identity(record),
    )
    save_worker_identity(record, first.identity)

    # The worker dies while the client is away; only the disk record survives.
    client.live_panes.discard("%1")

    payload2 = tmp_path / "a2.txt"
    client.script = ["working", written(payload2)]
    second = run_turn(
        backend,
        worker_name=name,
        cwd=tmp_path,
        argv=["cld"],
        prompt_path=tmp_path / "p2.txt",
        payload_ready=payload_ready(payload2),
        readiness_timeout_s=5,
        completion_timeout_s=5,
        identity=load_worker_identity(record),
    )
    save_worker_identity(record, second.identity)

    assert second.respawned is True
    assert second.reattached is False
    assert second.identity.worker_name == name, "the worker identity is stable across a respawn"
    assert second.identity.worker_ref == "%2", "the respawned worker is a new pane"
    assert load_worker_identity(record) is not None
    assert load_worker_identity(record).worker_ref == "%2"  # type: ignore[union-attr]


def test_a_missing_record_is_a_cold_start_not_a_failure(tmp_path: Path) -> None:
    assert load_worker_identity(tmp_path / "absent.json") is None


def test_a_corrupt_record_is_a_cold_start_not_a_crash(tmp_path: Path) -> None:
    record = tmp_path / "worker.json"
    record.write_text("{not json", encoding="utf-8")
    assert load_worker_identity(record) is None


# --- close and teardown ---


def test_close_worker_terminates_the_worker_and_clears_the_record(tmp_path: Path) -> None:
    client = FakeClient()
    backend = herdr_backend(client)
    record = tmp_path / "worker.json"
    payload = tmp_path / "a.txt"
    client.script = ["working", written(payload)]
    outcome = run_turn(
        backend,
        worker_name=warm_worker_name("run-1", "unit-a", "primary"),
        cwd=tmp_path,
        argv=["cld"],
        prompt_path=tmp_path / "p.txt",
        payload_ready=payload_ready(payload),
        readiness_timeout_s=5,
        completion_timeout_s=5,
    )
    save_worker_identity(record, outcome.identity)

    close_worker(backend, outcome.identity)
    clear_worker_identity(record)

    assert client.calls_of("close_pane") == ["%7"]
    assert "%7" not in client.live_panes
    assert not record.exists()
    assert load_worker_identity(record) is None


def test_close_worker_on_a_tmux_profile_kills_the_session(tmp_path: Path) -> None:
    tmux = FakeTmux()
    backend = tmux_backend(tmux)
    payload = tmp_path / "a.txt"
    payload.write_text("ANSWER", encoding="utf-8")
    outcome = run_turn(
        backend,
        worker_name="woof-run1-unit-primary",
        cwd=tmp_path,
        argv=["reasonix"],
        prompt_path=tmp_path / "p.txt",
        payload_ready=payload_ready(payload),
        readiness_timeout_s=5,
        completion_timeout_s=5,
    )
    close_worker(backend, outcome.identity)
    assert tmux.calls_of("kill_session") == ["woof-run1-unit-primary"]


def test_tearing_down_a_disposable_named_session_closes_its_workers(tmp_path: Path) -> None:
    client = FakeClient()
    session = "woof-smoke-1"
    backend = herdr_backend(client, session=session)
    payload = tmp_path / "a.txt"
    client.script = ["working", written(payload)]
    outcome = run_turn(
        backend,
        worker_name="woof-smoke-worker",
        cwd=tmp_path,
        argv=["cld"],
        prompt_path=tmp_path / "p.txt",
        payload_ready=payload_ready(payload),
        readiness_timeout_s=5,
        completion_timeout_s=5,
    )
    stopped: list[str] = []
    teardown_session(
        backend,
        identities=[outcome.identity],
        stop_server=lambda name: stopped.append(name),
    )
    assert client.calls_of("close_pane") == ["%7"]
    assert stopped == [session]


def test_an_operator_session_is_never_torn_down(tmp_path: Path) -> None:
    """The operator's live sessions carry running drains; a smoke must not stop them."""
    assert set(PROTECTED_SESSIONS) >= {"default", "drains"}
    client = FakeClient()
    backend = herdr_backend(client, session="drains")
    with pytest.raises(TransportUnavailable) as exc:
        teardown_session(backend, identities=[], stop_server=lambda name: None)
    assert "drains" in str(exc.value)
