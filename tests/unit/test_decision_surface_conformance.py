"""E17 P6 (S6) - the advertised decision surface equals the implemented one.

The analogue of the Stage-5 check-matrix conformance test, but for gate verbs:
``src/woof/graph/decisions.py`` is the canonical per-gate-type table, and every
other surface that names a verb must equal it. This module fails the instant any
surface drifts from the table, so the verb set cannot re-fragment after E17's
consolidation (the lead E17 risk).

What it locks:

- Surface equality - the argparse ``--resolve`` choices, the ``GateDecision``
  literal, the ``jsonl-events`` decision enum, and the per-gate-type verb lists in
  ``skills/woof/references/gates.md`` and ``skills/woof/SKILL.md`` all equal the
  canonical table (the union for the flat surfaces, the per-gate-type sets for the
  docs).
- Advertised == implemented - every advertised verb has an implemented effect and
  a forward-progress test (registered below and asserted to exist), and no
  implemented effect exists for an unadvertised verb (resolving a gate with a verb
  not valid for its type is a structured error, performing nothing).
- ``split_story`` appears on no surface.

The "per gate type" guarantee decomposes: the doc/argparse/literal/enum asserts
prove each gate type advertises exactly the table's verbs, and the per-verb
forward-progress registry proves every advertised verb (the union) has an
implemented, graph-moving effect. Together they give: for each gate type and each
of its verbs, an implemented effect with a forward-progress test.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
from pathlib import Path
from typing import cast, get_args

import pytest

from woof.cli.commands.wf import _apply_gate_resolution_effects, setup_wf_parser
from woof.graph.decisions import GATE_DECISIONS, all_decisions
from woof.graph.state import GateDecision
from woof.graph.transitions import StageStateError
from woof.trackers.base import CONFLICT_DECISIONS, CONFLICT_TRIGGERS, Tracker

pytestmark = pytest.mark.host_only

REPO_ROOT = Path(__file__).resolve().parents[2]
TESTS_DIR = Path(__file__).resolve().parent
GATES_MD = REPO_ROOT / "skills" / "woof" / "references" / "gates.md"
SKILL_MD = REPO_ROOT / "skills" / "woof" / "SKILL.md"


# --- Forward-progress registry -------------------------------------------------
#
# Each advertised verb maps to the forward-progress test(s) that drive its real
# effect end to end (the proof the verb moves the graph rather than being merely
# accepted). Shared-effect verbs map to one test per gate-type variant they cover.
# Adding a verb to GATE_DECISIONS without registering its forward-progress test
# here fails test_every_advertised_verb_has_a_forward_progress_test; renaming the
# referenced test without updating this map fails test_forward_progress_tests_exist.

FORWARD_PROGRESS: dict[str, tuple[tuple[str, str], ...]] = {
    "approve": (
        ("test_graph", "test_plan_gate_resolution_unblocks_stage_5_work_unit_execution"),
        ("test_graph", "test_wf_resolve_approve_clears_stale_failed_check_result"),
    ),
    "approve_with_reason": (
        ("test_contract_readiness", "test_approve_with_reason_advances_unready_epic_to_planning"),
    ),
    "retry_work_unit": (
        (
            "test_graph",
            "test_wf_resolve_retry_work_unit_resets_and_re_dispatches_without_redoing_siblings",
        ),
    ),
    "revise_work_unit_scope": (
        ("test_graph", "test_wf_resolve_revise_work_unit_scope_clears_stale_failed_check_result"),
    ),
    "revise_plan": (("test_graph", "test_wf_resolve_revise_plan_reenters_breakdown"),),
    "revise_epic_contract": (
        ("test_graph", "test_wf_resolve_revise_epic_contract_reenters_definition_from_plan_gate"),
        (
            "test_contract_readiness",
            "test_readiness_revise_epic_contract_archives_and_reenters_definition",
        ),
    ),
    "abandon_work_unit": (
        ("test_graph", "test_wf_resolve_abandon_work_unit_skips_to_next_ready_work_unit"),
    ),
    "abandon_epic": (
        ("test_graph", "test_wf_resolve_abandon_epic_closes_tracker_and_is_terminal"),
        ("test_contract_readiness", "test_readiness_abandon_epic_closes_tracker_and_is_terminal"),
    ),
    # The three tracker-conflict verbs share one parametrized contract test.
    "keep_local": (("test_trackers", "test_tracker_contract_conflict_resolution_decisions"),),
    "accept_remote": (("test_trackers", "test_tracker_contract_conflict_resolution_decisions"),),
    "hand_merge": (("test_trackers", "test_tracker_contract_conflict_resolution_decisions"),),
}


def _defined_callables(module_stem: str) -> set[str]:
    """Names of functions defined in a sibling test module (no import side effects)."""
    tree = ast.parse((TESTS_DIR / f"{module_stem}.py").read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            names.add(node.name)
    return names


# --- Reading the surfaces ------------------------------------------------------


def _resolve_argparse_choices() -> list[str]:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd")
    setup_wf_parser(sub)
    wf = sub.choices["wf"]
    (action,) = [a for a in wf._actions if a.dest == "resolve"]
    return list(action.choices or [])


def _jsonl_decision_enum() -> list[str]:
    schema = json.loads(
        (REPO_ROOT / "schemas" / "jsonl-events.schema.json").read_text(encoding="utf-8")
    )
    return schema["properties"]["decision"]["enum"]


# Map a doc bullet label to the gate type(s) it advertises. "work-unit / review gate"
# (SKILL.md) and "work-unit gate / review gate" (gates.md) both feed one verb list to
# two gate types, which the canonical table holds as identical rows.
def _label_gate_types(label: str) -> tuple[str, ...]:
    normalised = label.lower()
    if "work-unit" in normalised and "review" in normalised:
        return ("work_unit_gate", "review_gate")
    if "readiness" in normalised:
        return ("readiness_gate",)
    if "plan gate" in normalised:
        return ("plan_gate",)
    if "tracker sync conflict" in normalised:
        return ("tracker_sync_conflict",)
    return ()


_VERB_TOKEN = re.compile(r"`([a-z_]+)`")


def _parse_doc_gate_verbs(path: Path) -> dict[str, set[str]]:
    """Per-gate-type verb sets parsed from a doc's gate-decision bullet list.

    Bullets begin with ``- `` and may wrap onto indented continuation lines. Only
    bullets whose label maps to a gate type are kept, so the prose "what each does"
    bullets (labelled by a verb, not a gate) are ignored.
    """
    gate_verbs: dict[str, set[str]] = {}
    bullets: list[str] = []
    current: str | None = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        if raw.startswith("- "):
            if current is not None:
                bullets.append(current)
            current = raw[2:]
        elif current is not None and raw[:1].isspace() and raw.strip():
            current += " " + raw.strip()
        else:
            if current is not None:
                bullets.append(current)
            current = None
    if current is not None:
        bullets.append(current)

    for bullet in bullets:
        label, _, rest = bullet.partition(":")
        if not rest:
            continue
        gate_types = _label_gate_types(label)
        if not gate_types:
            continue
        verbs = set(_VERB_TOKEN.findall(rest))
        for gate_type in gate_types:
            gate_verbs.setdefault(gate_type, set()).update(verbs)
    return gate_verbs


# --- Surface equality ----------------------------------------------------------


def test_argparse_choices_equal_canonical_union() -> None:
    assert _resolve_argparse_choices() == list(all_decisions())


def test_gate_decision_literal_equals_canonical_union() -> None:
    assert set(get_args(GateDecision)) == set(all_decisions())


def test_jsonl_decision_enum_equals_canonical_union() -> None:
    assert set(_jsonl_decision_enum()) == set(all_decisions())


def test_gates_md_verb_lists_equal_canonical_table() -> None:
    expected = {gate_type: set(verbs) for gate_type, verbs in GATE_DECISIONS.items()}
    assert _parse_doc_gate_verbs(GATES_MD) == expected


def test_skill_md_verb_lists_equal_canonical_table() -> None:
    expected = {gate_type: set(verbs) for gate_type, verbs in GATE_DECISIONS.items()}
    assert _parse_doc_gate_verbs(SKILL_MD) == expected


# --- split_story is gone from every surface ------------------------------------


def test_split_story_absent_from_every_surface() -> None:
    assert "split_story" not in all_decisions()
    assert "split_story" not in get_args(GateDecision)
    assert "split_story" not in _resolve_argparse_choices()
    assert "split_story" not in _jsonl_decision_enum()
    assert all("split_story" not in verbs for verbs in GATE_DECISIONS.values())
    for verbs in _parse_doc_gate_verbs(GATES_MD).values():
        assert "split_story" not in verbs
    for verbs in _parse_doc_gate_verbs(SKILL_MD).values():
        assert "split_story" not in verbs


# --- Advertised == implemented -------------------------------------------------


def test_every_advertised_verb_has_a_forward_progress_test() -> None:
    # The forward-progress registry covers exactly the advertised verb union: no
    # advertised verb lacks a forward-progress proof, and no stale entry survives a
    # verb's removal.
    assert set(FORWARD_PROGRESS) == set(all_decisions())


def test_forward_progress_tests_exist() -> None:
    # Every registered forward-progress test resolves to a real test function, so
    # the registry cannot drift into naming tests that were renamed or deleted.
    callables_by_module: dict[str, set[str]] = {}
    for references in FORWARD_PROGRESS.values():
        for module_stem, test_name in references:
            names = callables_by_module.setdefault(module_stem, _defined_callables(module_stem))
            assert test_name in names, f"{module_stem}.py defines no {test_name}"


class _StubTracker:
    """A Tracker whose methods must never be called: an unadvertised verb is
    rejected before any tracker interaction or file effect runs."""

    def __getattr__(self, name: str):  # pragma: no cover - defensive
        raise AssertionError(f"unadvertised verb reached tracker.{name}")


def _unadvertised_verbs(gate_type: str) -> list[str]:
    allowed = set(GATE_DECISIONS[gate_type])
    return [verb for verb in all_decisions() if verb not in allowed]


@pytest.mark.parametrize("gate_type", sorted(GATE_DECISIONS))
def test_no_implemented_effect_for_an_unadvertised_verb(tmp_path: Path, gate_type: str) -> None:
    # Drive the real resolution effects with every verb the gate type does not
    # advertise. Each must raise StageStateError at the table's validity gate,
    # before any tracker call or file mutation: there is no implemented effect for
    # an unadvertised verb. A conflict gate is keyed by trigger, not gate_type.
    is_conflict = gate_type == "tracker_sync_conflict"
    triggered_by = [CONFLICT_TRIGGERS[0]] if is_conflict else ["plan_review"]
    for verb in _unadvertised_verbs(gate_type):
        with pytest.raises(StageStateError):
            _apply_gate_resolution_effects(
                tmp_path,
                1,
                decision=cast(GateDecision, verb),
                gate_type=gate_type,
                work_unit_id="S1",
                triggered_by=triggered_by,
                tracker=cast(Tracker, _StubTracker()),
            )


def test_canonical_table_covers_every_gate_type_and_conflict_verbs_match_tracker() -> None:
    # The conformance baseline: the table's gate types are exactly the surfaces the
    # rest of the suite checks against, and the tracker-conflict row still mirrors
    # the tracker layer's own CONFLICT_DECISIONS (owned there, not duplicated here).
    assert set(GATE_DECISIONS) == {
        "readiness_gate",
        "plan_gate",
        "work_unit_gate",
        "review_gate",
        "tracker_sync_conflict",
    }
    assert set(GATE_DECISIONS["tracker_sync_conflict"]) == set(CONFLICT_DECISIONS)
