"""Deterministic Stage-2.5 contract-readiness checks.

Runs after ``EPIC.md`` exists and before ``breakdown_planning``. This module owns
the checks; ``graph.nodes.contract_readiness_node`` owns the artefact write,
schema validation, ``readiness_passed`` event, and ``readiness_gate``.

This is prompt 2 of E2: the full readiness matrix. Six checks run against the
Stage-2 contract, each fails closed on its own concern:

- ``readiness_acceptance_signal`` - machine-verified outcomes carry a real
  machine-checkable acceptance signal (a non-deprecated contract decision or a
  machinable acceptance criterion; a bare ``O<n>``/``CD<n>`` mention is not a
  signal);
- ``readiness_acceptance_prose`` - subjective acceptance prose must be paired
  with a concrete signal;
- ``readiness_contract_concreteness`` - non-deprecated contract decisions carry
  a concrete (non-placeholder) ref unless forward-created;
- ``readiness_path_resolution`` - referenced existing paths resolve against
  ``git ls-files`` unless forward-created;
- ``readiness_symbol_resolution`` - file-based symbols resolve cheaply (tracked
  file + top-level ``ast`` class/function) unless forward-created;
- ``readiness_decomposition_sufficiency`` - Stage 3 can decompose without
  inventing interfaces.

Path and symbol resolution are bounded by a deterministic ``time_budget_s``: if
the budget is exhausted they are skipped and a single non-blocking
``readiness_checker_budget`` warn check is emitted. A timeout never pulls
``ReadinessResult.ok`` to false and never opens a gate on its own.

The dataclasses and the ``evaluate_readiness`` three-positional-argument call
shape are the stable seam; ``time_budget_s`` is keyword-only.
"""

from __future__ import annotations

import ast
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter
from typing import Any

import yaml

from woof.graph.git import git

# Outcomes verified by machine (or partly by machine) must carry a
# machine-checkable acceptance signal; a purely manual outcome is exempt from
# the acceptance-signal check (but still needs decomposition coverage).
_MACHINE_VERIFICATIONS = {"automated", "hybrid"}

ACCEPTANCE_SIGNAL_CHECK_ID = "readiness_acceptance_signal"
ACCEPTANCE_PROSE_CHECK_ID = "readiness_acceptance_prose"
CONTRACT_CONCRETENESS_CHECK_ID = "readiness_contract_concreteness"
PATH_RESOLUTION_CHECK_ID = "readiness_path_resolution"
SYMBOL_RESOLUTION_CHECK_ID = "readiness_symbol_resolution"
DECOMPOSITION_SUFFICIENCY_CHECK_ID = "readiness_decomposition_sufficiency"
CHECKER_BUDGET_CHECK_ID = "readiness_checker_budget"

# Generous deterministic budget for the filesystem/ast resolution checks. The
# cheap front-matter checks always run; only path/symbol resolution is bounded.
DEFAULT_READINESS_TIME_BUDGET_S = 10.0

# Exactly the two annotation forms, parsed from the EPIC body and contract
# decision notes. The annotated backtick token is whitelisted from path/symbol
# resolution. A malformed annotation (wrong spelling, missing ticket id, no
# parentheses) matches nothing and therefore exempts nothing.
_FORWARD_CREATED_RE = re.compile(
    r"`(?P<token>[^`\n]+)`[ \t]*\((?:forward-created|created by ticket[ \t]+[^)\n]+)\)"
)

_BACKTICK_SPAN_RE = re.compile(r"`(?P<token>[^`\n]+)`")

# A backtick symbol reference: a Python file plus a top-level symbol name.
_SYMBOL_TOKEN_RE = re.compile(r"^(?P<file>[\w./-]+\.py):(?P<symbol>[A-Za-z_]\w*)$")

# Placeholder ref values that are not a concrete contract reference.
_PLACEHOLDER_WORD_RE = re.compile(r"\b(?:todo|tbd|tbc|fixme|xxx|placeholder|wip)\b", re.IGNORECASE)
_ANGLE_PLACEHOLDER_RE = re.compile(r"<[^>]+>")

# Subjective acceptance prose: vague quality adjectives that prove nothing on
# their own. An entry that trips this lexicon is a blocker unless it also
# carries a concrete signal (see ``has_concrete_signal``).
_SUBJECTIVE_RE = re.compile(
    r"\b(?:good|great)\s+ux\b"
    r"|\bgood\s+user\s+experience\b"
    r"|\buser[- ]friendly\b"
    r"|\b(?:robust|performant|fast|snappy|scalable|secure|intuitive|seamless"
    r"|clean|nice|elegant|delightful|smooth|polished|modern|ergonomic|lightweight)\b",
    re.IGNORECASE,
)

# Concrete machine-checkable tokens.
_COMPARISON_RE = re.compile(r"(==|!=|>=|<=)|[<>]\s*-?\d")
_NUMBER_UNIT_RE = re.compile(
    r"\b\d+(?:\.\d+)?\s?"
    r"(?:ms|s|sec|secs|seconds|m|min|mins|h|hr|hrs|hours|%|x|kb|mb|gb|tb|b|bytes?"
    r"|files?|rows?|chars?|tokens?|items?|requests?|calls?|nodes?|stages?|checks?|lines?)\b",
    re.IGNORECASE,
)
_CODE_PATH_RE = re.compile(
    r"\b[\w./-]+\.(?:py|json|ya?ml|md|toml|txt|sh|cfg|ini|go|ts|tsx|js|jsx|sql|rs|proto)\b"
)
_TEST_MARKER_RE = re.compile(r"\btest_[A-Za-z0-9_]+\b|::")

_ID_ONLY_RE = re.compile(r"^(?:O|CD)[1-9]\d*$")


@dataclass(frozen=True)
class ReadinessFinding:
    """One offending artefact reference within a readiness check."""

    detail: str
    ref: str = ""

    def to_payload(self) -> dict[str, str]:
        payload: dict[str, str] = {"detail": self.detail}
        if self.ref:
            payload["ref"] = self.ref
        return payload


@dataclass(frozen=True)
class ReadinessCheck:
    """Outcome of a single readiness check."""

    id: str
    ok: bool
    severity: str
    summary: str
    findings: list[ReadinessFinding] = field(default_factory=list)

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.id,
            "ok": self.ok,
            "severity": self.severity,
            "summary": self.summary,
        }
        if self.findings:
            payload["findings"] = [finding.to_payload() for finding in self.findings]
        return payload


@dataclass(frozen=True)
class ReadinessResult:
    """Aggregate readiness result for an epic contract."""

    epic_id: int
    checks: list[ReadinessCheck]

    @property
    def ok(self) -> bool:
        # A ``warn`` check is a non-blocking performance/timeout finding; it
        # never pulls readiness to false on its own.
        return all(check.ok for check in self.checks if check.severity != "warn")

    def to_payload(self, timestamp: str) -> dict[str, Any]:
        return {
            "epic_id": self.epic_id,
            "ok": self.ok,
            "timestamp": timestamp,
            "checks": [check.to_payload() for check in self.checks],
        }


@dataclass(frozen=True)
class EpicDocument:
    """An EPIC.md split into validated front matter and free-form prose body."""

    front: dict[str, Any]
    body: str


@dataclass(frozen=True)
class _SymbolRef:
    raw: str
    locator: str
    symbol: str
    file_based: bool
    origin: str


class _BudgetExceeded(Exception):
    """Internal signal that the readiness checker exhausted its time budget."""


def evaluate_readiness(
    repo_root: Path,
    epic_id: int,
    epic_path: Path,
    *,
    time_budget_s: float = DEFAULT_READINESS_TIME_BUDGET_S,
) -> ReadinessResult:
    """Evaluate the Stage-2.5 readiness of an epic contract.

    The three positional arguments are the stable prompt-1 seam. ``time_budget_s``
    is a keyword-only deterministic budget for the filesystem/ast resolution
    checks: when exhausted, path and symbol resolution are skipped and a single
    non-blocking ``readiness_checker_budget`` warn check is emitted.
    """

    document = _load_epic_document(epic_path)
    front = document.front

    nd_contract_decisions = _non_deprecated(front.get("contract_decisions"))
    annotatable_text = _annotatable_text(document.body, nd_contract_decisions)
    forward_created = _forward_created_tokens(annotatable_text)
    tracked = _tracked_paths(repo_root)

    checks: list[ReadinessCheck] = [
        _check_acceptance_signal(front, nd_contract_decisions),
        _check_acceptance_prose(front),
        _check_contract_concreteness(nd_contract_decisions, forward_created),
        _check_decomposition_sufficiency(front, nd_contract_decisions),
    ]

    deadline = perf_counter() + max(0.0, float(time_budget_s))
    skipped: list[str] = []

    path_check = _run_within_budget(
        PATH_RESOLUTION_CHECK_ID,
        skipped,
        lambda: _check_path_resolution(
            nd_contract_decisions, annotatable_text, tracked, forward_created, deadline
        ),
    )
    if path_check is not None:
        checks.append(path_check)

    symbol_check = _run_within_budget(
        SYMBOL_RESOLUTION_CHECK_ID,
        skipped,
        lambda: _check_symbol_resolution(
            repo_root, nd_contract_decisions, annotatable_text, tracked, forward_created, deadline
        ),
    )
    if symbol_check is not None:
        checks.append(symbol_check)

    if skipped:
        checks.append(
            ReadinessCheck(
                id=CHECKER_BUDGET_CHECK_ID,
                ok=True,
                severity="warn",
                summary=(
                    "readiness checker time budget exhausted; skipped checks: " + ", ".join(skipped)
                ),
            )
        )

    return ReadinessResult(epic_id=epic_id, checks=checks)


# --------------------------------------------------------------------------- #
# Check 1: acceptance signal (tightened prompt-1 check)
# --------------------------------------------------------------------------- #


def _check_acceptance_signal(
    front: dict[str, Any], nd_contract_decisions: list[dict[str, Any]]
) -> ReadinessCheck:
    """Every machine-verified outcome must carry a machine-checkable signal.

    A signal is a non-deprecated ``contract_decision`` whose ``related_outcomes``
    names the outcome, or an ``acceptance_criteria`` entry that names the outcome
    *and* carries a concrete signal. A bare ``O<n>``/``CD<n>`` mention is not a
    signal.
    """

    realised = _outcomes_realised_by_contract(nd_contract_decisions)
    machinable = _outcomes_with_machinable_criterion(front)

    findings: list[ReadinessFinding] = []
    for outcome in _non_deprecated(front.get("observable_outcomes")):
        if outcome.get("verification") not in _MACHINE_VERIFICATIONS:
            continue
        outcome_id = _str_id(outcome.get("id"))
        if not outcome_id:
            continue
        if outcome_id in realised or outcome_id in machinable:
            continue
        findings.append(
            ReadinessFinding(
                ref=outcome_id,
                detail=(
                    f"{outcome_id} is verified by machine but has no machine-checkable "
                    "acceptance signal: no non-deprecated contract_decision realises it and "
                    "no acceptance_criteria entry names it with a concrete signal (a bare "
                    "O<n>/CD<n> mention is not a signal)"
                ),
            )
        )

    if findings:
        return ReadinessCheck(
            id=ACCEPTANCE_SIGNAL_CHECK_ID,
            ok=False,
            severity="blocker",
            summary=(
                f"{len(findings)} machine-verified outcome(s) lack a machine-checkable "
                "acceptance signal"
            ),
            findings=findings,
        )
    return ReadinessCheck(
        id=ACCEPTANCE_SIGNAL_CHECK_ID,
        ok=True,
        severity="info",
        summary="every machine-verified outcome carries a machine-checkable acceptance signal",
    )


# --------------------------------------------------------------------------- #
# Check 2: acceptance prose (subjective prose must pair with a concrete signal)
# --------------------------------------------------------------------------- #


def _check_acceptance_prose(front: dict[str, Any]) -> ReadinessCheck:
    findings: list[ReadinessFinding] = []
    criteria = front.get("acceptance_criteria")
    criteria = criteria if isinstance(criteria, list) else []
    for index, criterion in enumerate(criteria):
        text = str(criterion)
        if not _SUBJECTIVE_RE.search(text):
            continue
        if has_concrete_signal(text):
            continue
        findings.append(
            ReadinessFinding(
                ref=f"acceptance_criteria[{index}]",
                detail=(
                    "subjective acceptance prose without a concrete signal: "
                    f"{text!r}. Pair the quality term with a command, test marker, "
                    "path, comparison, or number+unit."
                ),
            )
        )

    if findings:
        return ReadinessCheck(
            id=ACCEPTANCE_PROSE_CHECK_ID,
            ok=False,
            severity="blocker",
            summary=f"{len(findings)} acceptance criterion/criteria are subjective prose with no signal",
            findings=findings,
        )
    return ReadinessCheck(
        id=ACCEPTANCE_PROSE_CHECK_ID,
        ok=True,
        severity="info",
        summary="no subjective acceptance prose without a concrete signal",
    )


# --------------------------------------------------------------------------- #
# Check 3: contract-decision concreteness (concrete refs, not placeholders)
# --------------------------------------------------------------------------- #


def _check_contract_concreteness(
    nd_contract_decisions: list[dict[str, Any]], forward_created: set[str]
) -> ReadinessCheck:
    findings: list[ReadinessFinding] = []
    for cd in nd_contract_decisions:
        cd_id = _str_id(cd.get("id")) or "<missing>"
        ref, kind = _contract_ref(cd)
        if not ref:
            findings.append(
                ReadinessFinding(
                    ref=cd_id,
                    detail=(
                        f"{cd_id} declares no concrete openapi_ref, pydantic_ref, or "
                        "json_schema_ref"
                    ),
                )
            )
            continue
        if _is_forward_created_ref(ref, kind, forward_created):
            continue
        if _is_placeholder_ref(ref):
            findings.append(
                ReadinessFinding(
                    ref=cd_id,
                    detail=(
                        f"{cd_id} {kind} {ref!r} is placeholder prose, not a concrete "
                        "reference; supply a real path/schema/API ref or annotate the "
                        "target forward-created"
                    ),
                )
            )

    if findings:
        return ReadinessCheck(
            id=CONTRACT_CONCRETENESS_CHECK_ID,
            ok=False,
            severity="blocker",
            summary=f"{len(findings)} contract decision(s) carry a placeholder reference",
            findings=findings,
        )
    return ReadinessCheck(
        id=CONTRACT_CONCRETENESS_CHECK_ID,
        ok=True,
        severity="info",
        summary="every non-deprecated contract decision carries a concrete reference",
    )


# --------------------------------------------------------------------------- #
# Check 4: path resolution against git ls-files
# --------------------------------------------------------------------------- #


def _check_path_resolution(
    nd_contract_decisions: list[dict[str, Any]],
    annotatable_text: str,
    tracked: set[str],
    forward_created: set[str],
    deadline: float,
) -> ReadinessCheck:
    _ensure_budget(deadline)
    findings: list[ReadinessFinding] = []
    for path, origin in _referenced_paths(nd_contract_decisions, annotatable_text):
        _ensure_budget(deadline)
        if path in forward_created:
            continue
        if path in tracked:
            continue
        findings.append(
            ReadinessFinding(
                ref=path,
                detail=(
                    f"referenced path {path!r} ({origin}) is not tracked by git and is not "
                    "annotated forward-created"
                ),
            )
        )

    if findings:
        return ReadinessCheck(
            id=PATH_RESOLUTION_CHECK_ID,
            ok=False,
            severity="blocker",
            summary=f"{len(findings)} referenced path(s) do not resolve against git ls-files",
            findings=findings,
        )
    return ReadinessCheck(
        id=PATH_RESOLUTION_CHECK_ID,
        ok=True,
        severity="info",
        summary="every referenced existing path resolves against git ls-files",
    )


# --------------------------------------------------------------------------- #
# Check 5: cheap file-based symbol resolution
# --------------------------------------------------------------------------- #


def _check_symbol_resolution(
    repo_root: Path,
    nd_contract_decisions: list[dict[str, Any]],
    annotatable_text: str,
    tracked: set[str],
    forward_created: set[str],
    deadline: float,
) -> ReadinessCheck:
    _ensure_budget(deadline)
    findings: list[ReadinessFinding] = []
    for ref in _referenced_symbols(nd_contract_decisions, annotatable_text):
        _ensure_budget(deadline)
        if ref.raw in forward_created:
            continue
        if ref.file_based and ref.locator in forward_created:
            continue
        if not ref.file_based:
            findings.append(
                ReadinessFinding(
                    ref=ref.raw,
                    detail=(
                        f"symbol reference {ref.raw!r} ({ref.origin}) is a module/dynamic "
                        "reference that is not cheaply provable; use a file.py:Symbol "
                        "reference or annotate it forward-created"
                    ),
                )
            )
            continue
        if not ref.symbol:
            findings.append(
                ReadinessFinding(
                    ref=ref.raw,
                    detail=(
                        f"symbol reference {ref.raw!r} ({ref.origin}) must be of the form "
                        "file.py:Symbol"
                    ),
                )
            )
            continue
        if ref.locator not in tracked:
            findings.append(
                ReadinessFinding(
                    ref=ref.raw,
                    detail=(
                        f"symbol reference {ref.raw!r} ({ref.origin}): file {ref.locator!r} "
                        "is not tracked by git and is not annotated forward-created"
                    ),
                )
            )
            continue
        if not _file_defines_top_level(repo_root / ref.locator, ref.symbol):
            findings.append(
                ReadinessFinding(
                    ref=ref.raw,
                    detail=(
                        f"symbol reference {ref.raw!r} ({ref.origin}): no top-level class or "
                        f"function {ref.symbol!r} in {ref.locator!r}"
                    ),
                )
            )

    if findings:
        return ReadinessCheck(
            id=SYMBOL_RESOLUTION_CHECK_ID,
            ok=False,
            severity="blocker",
            summary=f"{len(findings)} referenced symbol(s) do not resolve cheaply",
            findings=findings,
        )
    return ReadinessCheck(
        id=SYMBOL_RESOLUTION_CHECK_ID,
        ok=True,
        severity="info",
        summary="every file-based symbol reference resolves cheaply",
    )


# --------------------------------------------------------------------------- #
# Check 6: Stage-3 decomposition sufficiency
# --------------------------------------------------------------------------- #


def _check_decomposition_sufficiency(
    front: dict[str, Any], nd_contract_decisions: list[dict[str, Any]]
) -> ReadinessCheck:
    """Stage 3 can decompose without inventing interfaces.

    Every non-deprecated outcome is realised by a non-deprecated contract
    decision or a machinable acceptance criterion, and every non-deprecated
    contract decision relates to at least one declared non-deprecated outcome.
    """

    nd_outcomes = _non_deprecated(front.get("observable_outcomes"))
    nd_outcome_ids = {oid for outcome in nd_outcomes if (oid := _str_id(outcome.get("id")))}
    realised = _outcomes_realised_by_contract(nd_contract_decisions)
    machinable = _outcomes_with_machinable_criterion(front)

    findings: list[ReadinessFinding] = []
    for outcome in nd_outcomes:
        outcome_id = _str_id(outcome.get("id"))
        if not outcome_id:
            continue
        if outcome_id in realised or outcome_id in machinable:
            continue
        findings.append(
            ReadinessFinding(
                ref=outcome_id,
                detail=(
                    f"{outcome_id} is not realised by any non-deprecated contract_decision "
                    "and has no machinable acceptance criterion, so Stage 3 cannot decompose "
                    "it without inventing an interface"
                ),
            )
        )

    for cd in nd_contract_decisions:
        cd_id = _str_id(cd.get("id")) or "<missing>"
        related = _str_ids(cd.get("related_outcomes"))
        if not related & nd_outcome_ids:
            findings.append(
                ReadinessFinding(
                    ref=cd_id,
                    detail=(
                        f"{cd_id} relates to no declared non-deprecated outcome; its "
                        "related_outcomes are empty, unknown, or all deprecated"
                    ),
                )
            )

    if findings:
        return ReadinessCheck(
            id=DECOMPOSITION_SUFFICIENCY_CHECK_ID,
            ok=False,
            severity="blocker",
            summary=f"{len(findings)} decomposition-sufficiency gap(s) between outcomes and contracts",
            findings=findings,
        )
    return ReadinessCheck(
        id=DECOMPOSITION_SUFFICIENCY_CHECK_ID,
        ok=True,
        severity="info",
        summary="outcomes and contract decisions are mutually realised for Stage-3 decomposition",
    )


# --------------------------------------------------------------------------- #
# Concrete-signal lexicon
# --------------------------------------------------------------------------- #


def has_concrete_signal(text: str) -> bool:
    """Return whether ``text`` carries a machine-checkable acceptance signal.

    A bare ``O<n>``/``CD<n>`` identifier is not a signal; a concrete signal is a
    command/path/symbol/test-marker in backticks, a ``module.py:Symbol`` or code
    path, a comparison operator, or a number with a unit.
    """

    for span in _BACKTICK_SPAN_RE.findall(text):
        if not _ID_ONLY_RE.match(span.strip()):
            return True
    if _CODE_PATH_RE.search(text):
        return True
    if _COMPARISON_RE.search(text):
        return True
    if _NUMBER_UNIT_RE.search(text):
        return True
    return bool(_TEST_MARKER_RE.search(text))


# --------------------------------------------------------------------------- #
# Reference collection
# --------------------------------------------------------------------------- #


def _referenced_paths(
    nd_contract_decisions: list[dict[str, Any]], annotatable_text: str
) -> list[tuple[str, str]]:
    """Existing-path references: openapi/json_schema file parts plus backtick paths."""

    refs: list[tuple[str, str]] = []
    seen: set[str] = set()

    def _add(path: str, origin: str) -> None:
        path = path.strip()
        if path and path not in seen:
            seen.add(path)
            refs.append((path, origin))

    for cd in nd_contract_decisions:
        cd_id = _str_id(cd.get("id")) or "<missing>"
        openapi_ref = cd.get("openapi_ref")
        if isinstance(openapi_ref, str) and openapi_ref:
            _add(openapi_ref.split("#", 1)[0], f"openapi_ref of {cd_id}")
        json_schema_ref = cd.get("json_schema_ref")
        if isinstance(json_schema_ref, str) and json_schema_ref:
            _add(json_schema_ref, f"json_schema_ref of {cd_id}")

    for token in _backtick_tokens(annotatable_text):
        if _looks_like_path(token):
            _add(token, "EPIC body or contract-decision notes")

    return refs


def _referenced_symbols(
    nd_contract_decisions: list[dict[str, Any]], annotatable_text: str
) -> list[_SymbolRef]:
    refs: list[_SymbolRef] = []
    seen: set[str] = set()

    def _add(ref: _SymbolRef) -> None:
        if ref.raw and ref.raw not in seen:
            seen.add(ref.raw)
            refs.append(ref)

    for cd in nd_contract_decisions:
        cd_id = _str_id(cd.get("id")) or "<missing>"
        pydantic_ref = cd.get("pydantic_ref")
        if isinstance(pydantic_ref, str) and pydantic_ref:
            _add(_parse_symbol_ref(pydantic_ref, f"pydantic_ref of {cd_id}"))

    for token in _backtick_tokens(annotatable_text):
        if _SYMBOL_TOKEN_RE.match(token):
            _add(_parse_symbol_ref(token, "EPIC body or contract-decision notes"))

    return refs


def _parse_symbol_ref(ref: str, origin: str) -> _SymbolRef:
    ref = ref.strip()
    if ":" in ref:
        locator, _, symbol = ref.rpartition(":")
    else:
        locator, symbol = ref, ""
    file_based = locator.endswith(".py") or "/" in locator
    return _SymbolRef(raw=ref, locator=locator, symbol=symbol, file_based=file_based, origin=origin)


def _contract_ref(cd: dict[str, Any]) -> tuple[str, str]:
    for kind in ("openapi_ref", "pydantic_ref", "json_schema_ref"):
        value = cd.get(kind)
        if isinstance(value, str) and value.strip():
            return value.strip(), kind
    return "", ""


def _is_forward_created_ref(ref: str, kind: str, forward_created: set[str]) -> bool:
    if ref in forward_created:
        return True
    if kind == "openapi_ref":
        return ref.split("#", 1)[0] in forward_created
    if kind == "pydantic_ref" and ":" in ref:
        return ref.rsplit(":", 1)[0] in forward_created
    return False


def _is_placeholder_ref(ref: str) -> bool:
    return bool(_PLACEHOLDER_WORD_RE.search(ref) or _ANGLE_PLACEHOLDER_RE.search(ref))


def _looks_like_path(token: str) -> bool:
    token = token.strip()
    if not token or any(char.isspace() for char in token):
        return False
    if ":" in token or "#" in token:
        return False
    if "/" in token:
        return True
    return bool(re.search(r"\.[A-Za-z0-9]{1,8}$", token))


def _file_defines_top_level(path: Path, symbol: str) -> bool:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return False
    return any(
        isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
        and node.name == symbol
        for node in tree.body
    )


# --------------------------------------------------------------------------- #
# Outcome/contract relationship helpers
# --------------------------------------------------------------------------- #


def _outcomes_realised_by_contract(nd_contract_decisions: list[dict[str, Any]]) -> set[str]:
    realised: set[str] = set()
    for cd in nd_contract_decisions:
        realised |= _str_ids(cd.get("related_outcomes"))
    return realised


def _outcomes_with_machinable_criterion(front: dict[str, Any]) -> set[str]:
    """Outcome IDs named by an acceptance criterion that carries a concrete signal."""

    criteria = front.get("acceptance_criteria")
    criteria = criteria if isinstance(criteria, list) else []
    outcome_ids = {
        oid
        for outcome in _non_deprecated(front.get("observable_outcomes"))
        if (oid := _str_id(outcome.get("id")))
    }

    machinable: set[str] = set()
    for criterion in criteria:
        text = str(criterion)
        if not has_concrete_signal(text):
            continue
        for outcome_id in outcome_ids:
            if _names_token(text, outcome_id):
                machinable.add(outcome_id)
    return machinable


# --------------------------------------------------------------------------- #
# Front matter / body / grammar helpers
# --------------------------------------------------------------------------- #


def _load_epic_document(epic_path: Path) -> EpicDocument:
    """Load EPIC.md front matter plus the free-form prose body."""

    text = epic_path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return EpicDocument(front={}, body=text)
    end = text.find("\n---\n", 4)
    if end < 0:
        return EpicDocument(front={}, body="")
    payload = yaml.safe_load(text[4:end]) or {}
    front = payload if isinstance(payload, dict) else {}
    body = text[end + len("\n---\n") :]
    return EpicDocument(front=front, body=body)


def _annotatable_text(body: str, nd_contract_decisions: list[dict[str, Any]]) -> str:
    """Text the forward-created grammar and backtick refs are parsed from."""

    parts = [body]
    for cd in nd_contract_decisions:
        notes = cd.get("notes")
        if isinstance(notes, str) and notes:
            parts.append(notes)
    return "\n".join(parts)


def _forward_created_tokens(text: str) -> set[str]:
    return {match.group("token").strip() for match in _FORWARD_CREATED_RE.finditer(text)}


def _backtick_tokens(text: str) -> list[str]:
    return [match.group("token").strip() for match in _BACKTICK_SPAN_RE.finditer(text)]


def _tracked_paths(repo_root: Path) -> set[str]:
    try:
        proc = git(repo_root, "ls-files")
    except (subprocess.CalledProcessError, OSError):
        return set()
    return {line.strip() for line in proc.stdout.splitlines() if line.strip()}


def _non_deprecated(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict) and item.get("deprecated") is not True]


def _str_id(value: object) -> str:
    return value if isinstance(value, str) and value else ""


def _str_ids(value: object) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {item for item in value if isinstance(item, str) and item}


def _names_token(text: str, token: str) -> bool:
    return re.search(rf"\b{re.escape(token)}\b", text) is not None


def _ensure_budget(deadline: float) -> None:
    if perf_counter() >= deadline:
        raise _BudgetExceeded


def _run_within_budget(check_id: str, skipped: list[str], run: Any) -> ReadinessCheck | None:
    try:
        return run()
    except _BudgetExceeded:
        skipped.append(check_id)
        return None
