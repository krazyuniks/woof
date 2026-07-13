"""The transport boundary holds: no backend leaks above it, and no field is named for one.

Two properties of ADR-012 that are easy to regress one call site at a time, so they
are checked mechanically rather than trusted.
"""

from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src" / "woof"
EVENTS_SCHEMA = REPO_ROOT / "schemas" / "jsonl-events.schema.json"

# The transport seam, the two backends, and the registry that declares the backends
# are the only modules allowed to name one. Preflight joins them because reporting a
# named session's running server -- its socket, version, and protocol -- is
# irreducibly a fact about that backend, and is what it exists to check.
#
# Everything else -- the graph, the checks, the gates, the trackers, and the dispatch
# adapter itself -- runs workers without knowing what runs them.
TRANSPORT_MODULES = {
    "cli/transport.py",
    "cli/tmux_backend.py",
    "cli/herdr.py",
    "cli/harness_registry.py",
    "cli/preflight.py",
}
BACKEND_NAMES = ("tmux", "herdr")


def _workflow_sources() -> list[Path]:
    return [
        path
        for path in SRC.rglob("*.py")
        if path.relative_to(SRC).as_posix() not in TRANSPORT_MODULES
    ]


def test_workflow_code_contains_no_transport_branch() -> None:
    """Above the seam, the transport is resolved from the profile, never branched on."""
    offenders: list[str] = []
    for path in _workflow_sources():
        relative = path.relative_to(SRC).as_posix()
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            lowered = line.lower()
            for backend in BACKEND_NAMES:
                if backend in lowered:
                    offenders.append(f"{relative}:{number}: {line.strip()}")
    assert offenders == [], (
        "workflow code names a transport backend; the backend is declared by the "
        "harness profile and resolved at the transport seam:\n" + "\n".join(offenders)
    )


def test_no_durable_event_field_is_named_after_a_backend() -> None:
    """A field carrying either backend must not be named for one of them."""
    schema = json.loads(EVENTS_SCHEMA.read_text(encoding="utf-8"))
    offenders = [
        name
        for name in schema["properties"]
        if any(backend in name.lower() for backend in BACKEND_NAMES)
    ]
    assert offenders == [], (
        f"durable event fields named after one backend: {offenders}. A field that can "
        "carry either backend is named for neither."
    )


def test_the_prompt_transport_value_is_backend_neutral() -> None:
    schema = json.loads(EVENTS_SCHEMA.read_text(encoding="utf-8"))
    values = schema["properties"]["prompt_transport"]["enum"]
    assert not any(backend in value.lower() for value in values for backend in BACKEND_NAMES), (
        f"prompt_transport values name a backend: {values}. Every interactive harness "
        "transport delivers the prompt as a file."
    )


def test_blocked_timeout_and_payload_absence_are_distinct_durable_outcomes() -> None:
    schema = json.loads(EVENTS_SCHEMA.read_text(encoding="utf-8"))
    exit_types = set(schema["properties"]["exit_type"]["enum"])
    assert {"blocked", "wallclock_timeout", "payload_absent"} <= exit_types
