"""The tmux transport, adapted to the shape the seam expects.

tmux stays supported for profiles whose TUI has no validated herdr lifecycle
integration. It reports no semantic lifecycle, so the seam observes completion
from the payload on disk rather than from status events; everything else -- worker
identity, close, and the result metadata -- is the same as the herdr backend's.

The ``tmux_harness`` package is imported lazily and only when a tmux profile is
actually dispatched, so a herdr-only run never needs it installed.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from woof.cli.transport_errors import TransportUnavailable

_SEARCH_PATHS = (
    Path(__file__).resolve().parents[3] / "agent-toolkit" / "skills" / "tmux-harness",
    Path.home() / "Work" / "agent-toolkit" / "skills" / "tmux-harness",
)


def _import_tmux_harness() -> Any:
    try:
        import tmux_harness
    except ModuleNotFoundError:
        for candidate in _SEARCH_PATHS:
            if candidate.is_dir():
                sys.path.insert(0, str(candidate))
                try:
                    import tmux_harness
                except ModuleNotFoundError:
                    continue
                return tmux_harness
        raise TransportUnavailable(
            "the tmux transport is not installed; a tmux harness profile cannot be "
            "dispatched without the tmux_harness package",
            backend="tmux",
        ) from None
    return tmux_harness


def tmux_transport() -> Any:
    """Return the tmux worker mechanics the seam calls.

    ``deliver_prompt_file`` is narrowed to the seam's two arguments: the full task
    lives in the prompt file and the worker is pasted a pointer to it, which is the
    same delivery contract the herdr backend uses.
    """
    harness = _import_tmux_harness()
    tmux = harness.tmux

    def deliver_prompt_file(session: str, prompt_path: Path) -> None:
        harness.deliver_prompt_file(
            session,
            prompt_path,
            harness.read_file_kickoff(prompt_path),
            prompt=None,
        )

    return SimpleNamespace(
        has_session=tmux.has_session,
        launch_session=tmux.launch_session,
        wait_for_input_ready=tmux.wait_for_input_ready,
        deliver_prompt_file=deliver_prompt_file,
        capture_pane_tail=tmux.capture_pane_tail,
        kill_session=tmux.kill_session,
    )


__all__ = ["tmux_transport"]
