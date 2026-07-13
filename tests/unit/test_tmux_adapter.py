"""The production tmux adapter, driven as production code.

The seam's own tests drive tmux through ``FakeTmux``. A fake can only certify a
contract the production adapter actually honours if something holds the two
together, so these tests build the real adapter (``tmux_transport``) over a stub
``tmux_harness`` module and then check the fake against it. The stub is in turn
checked against the installed tmux_harness source, so the chain from the fake to
the real package has no unverified link.

The stub exists because ``tmux_harness`` cannot be imported here without its own
runtime dependency; parsing its source proves the stub's shapes without it.
"""

from __future__ import annotations

import ast
import inspect
import sys
import types
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from tests.unit.test_transport_seam import FakeTmux
from woof.cli.tmux_backend import _SEARCH_PATHS, tmux_transport
from woof.cli.transport import TmuxBackend, build_kickoff

WORKER = "woof-run1-unit-primary"


def _stub_tmux_harness(calls: list[tuple[str, Any]]) -> types.ModuleType:
    """A ``tmux_harness`` whose signatures mirror the installed package's.

    Every parameter list here is held to the real source by
    :func:`test_the_stub_matches_the_installed_tmux_harness_source`.
    """
    module = types.ModuleType("tmux_harness")

    def has_session(session: str, *, runner: Any = None) -> bool:
        calls.append(("has_session", session))
        return True

    def launch_session(session: str, cwd: Path, argv: list[str], *, runner: Any = None) -> None:
        calls.append(("launch_session", {"session": session, "cwd": cwd, "argv": argv}))

    def wait_for_input_ready(
        session: str,
        *,
        readiness_timeout_s: int,
        idle_threshold_s: float = 2.0,
        runner: Any = None,
        clock: Callable[[float], None] = lambda _s: None,
    ) -> None:
        calls.append(("wait_for_input_ready", session))

    def capture_pane_tail(session: str, lines: int = 40, *, runner: Any = None) -> str:
        calls.append(("capture_pane_tail", {"session": session, "lines": lines}))
        return "PANE TAIL"

    def kill_session(session: str, *, runner: Any = None) -> None:
        calls.append(("kill_session", session))

    def deliver_prompt_file(
        session: str,
        prompt_path: Path,
        kickoff: str,
        *,
        prompt: str | None = None,
        runner: Any = None,
        clock: Callable[[float], None] = lambda _s: None,
    ) -> None:
        calls.append(
            (
                "deliver_prompt_file",
                {
                    "session": session,
                    "prompt_path": prompt_path,
                    "kickoff": kickoff,
                    "prompt": prompt,
                },
            )
        )

    def read_file_kickoff(prompt_path: Path) -> str:
        calls.append(("read_file_kickoff", prompt_path))
        return f"read {prompt_path}"

    module.tmux = types.SimpleNamespace(  # type: ignore[attr-defined]
        has_session=has_session,
        launch_session=launch_session,
        wait_for_input_ready=wait_for_input_ready,
        capture_pane_tail=capture_pane_tail,
        kill_session=kill_session,
    )
    module.deliver_prompt_file = deliver_prompt_file  # type: ignore[attr-defined]
    module.read_file_kickoff = read_file_kickoff  # type: ignore[attr-defined]
    return module


@pytest.fixture
def stub_calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, Any]]:
    calls: list[tuple[str, Any]] = []
    monkeypatch.setitem(sys.modules, "tmux_harness", _stub_tmux_harness(calls))
    return calls


def _calls_of(calls: list[tuple[str, Any]], method: str) -> list[Any]:
    return [payload for name, payload in calls if name == method]


def _call_shape(func: Any) -> tuple[list[str], set[str]]:
    """The calls a member accepts: its positional names and its required keywords."""
    signature = inspect.signature(func)
    positional = [
        name
        for name, param in signature.parameters.items()
        if param.kind
        in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    ]
    required_keywords = {
        name
        for name, param in signature.parameters.items()
        if param.kind is inspect.Parameter.KEYWORD_ONLY and param.default is inspect.Parameter.empty
    }
    return positional, required_keywords


# --- the seam drives the production adapter, not only the fake ---


def test_the_seam_drives_the_production_adapter_exactly_as_it_drives_the_fake(
    tmp_path: Path, stub_calls: list[tuple[str, Any]]
) -> None:
    """The B1 defect: the seam's three-argument delivery call, through real code.

    Every tmux dispatch went through ``tmux_transport()``; only the fake was ever
    driven by a test, so the adapter's two-parameter delivery never raised in the
    suite while raising ``TypeError`` on every real dispatch.
    """
    prompt = tmp_path / "task.prompt"
    payload = tmp_path / "task.payload"
    payload.write_text("ANSWER", encoding="utf-8")
    kickoff = build_kickoff(prompt, payload)

    for backend in (
        TmuxBackend(tmux_transport(), harness="reasonix"),
        TmuxBackend(FakeTmux(live={WORKER}), harness="reasonix"),
    ):
        completed_on, _ = backend.deliver(
            WORKER,
            prompt_path=prompt,
            kickoff=kickoff,
            payload_ready=lambda: payload.exists(),
            readiness_timeout_s=5,
            completion_timeout_s=5,
        )
        assert completed_on == "payload"


def test_the_tmux_adapter_pastes_the_seams_kickoff_with_its_payload_path(
    tmp_path: Path, stub_calls: list[tuple[str, Any]]
) -> None:
    """Backend equivalence: tmux honours the seam's kickoff, as herdr does.

    The one-shot kickoff names the payload file the worker must write. An adapter
    that substitutes its own kickoff never tells the worker where to write, so the
    payload never appears and reviewer, mapper, and enrichment capture nothing.
    """
    prompt = tmp_path / "task.prompt"
    payload = tmp_path / "task.payload"
    payload.write_text("ANSWER", encoding="utf-8")
    kickoff = build_kickoff(prompt, payload)

    backend = TmuxBackend(tmux_transport(), harness="reasonix")
    backend.deliver(
        WORKER,
        prompt_path=prompt,
        kickoff=kickoff,
        payload_ready=lambda: payload.exists(),
        readiness_timeout_s=5,
        completion_timeout_s=5,
    )

    delivered = _calls_of(stub_calls, "deliver_prompt_file")
    assert delivered == [
        {"session": WORKER, "prompt_path": prompt, "kickoff": kickoff, "prompt": None}
    ]
    assert str(payload) in delivered[0]["kickoff"], (
        "the worker must be told the payload file it has to write"
    )
    assert _calls_of(stub_calls, "read_file_kickoff") == [], (
        "the adapter must not substitute a kickoff of its own for the seam's"
    )


# --- the fake is held to the production adapter's contract ---


def test_the_tmux_fake_accepts_every_call_the_production_adapter_accepts(
    stub_calls: list[tuple[str, Any]],
) -> None:
    """A fake may not certify a contract the production adapter does not honour."""
    production = tmux_transport()
    fake = FakeTmux()
    for name, member in vars(production).items():
        fake_member = getattr(fake, name, None)
        assert fake_member is not None, f"the fake has no {name!r}; the seam calls it"
        assert _call_shape(member) == _call_shape(fake_member), (
            f"the fake's {name!r} and the production adapter's disagree on their call shape"
        )


@pytest.mark.tmux_substrate
def test_the_stub_matches_the_installed_tmux_harness_source() -> None:
    """The stub above stands in for tmux_harness, so its shapes are held to the real ones.

    The source is parsed rather than imported: the package pulls in a runtime
    dependency the unit suite does not install, and a signature does not need one.
    """
    checkout = next((path for path in _SEARCH_PATHS if path.is_dir()), None)
    assert checkout is not None, "no tmux_harness checkout on the adapter's search path"
    real: dict[str, list[str]] = {}
    for source in ("tmux_harness/tmux.py", "tmux_harness/dispatch.py"):
        tree = ast.parse((checkout / source).read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.FunctionDef):
                real[node.name] = [arg.arg for arg in node.args.args]

    stub = _stub_tmux_harness([])
    members = {
        **{name: member for name, member in vars(stub.tmux).items()},  # type: ignore[attr-defined]
        "deliver_prompt_file": stub.deliver_prompt_file,  # type: ignore[attr-defined]
        "read_file_kickoff": stub.read_file_kickoff,  # type: ignore[attr-defined]
    }
    for name, member in members.items():
        assert name in real, f"tmux_harness no longer exports {name!r}"
        assert _call_shape(member)[0] == real[name], (
            f"the stub's {name!r} no longer matches the installed tmux_harness"
        )
