"""check_2_outcome_markers — Stage-5 Check 2.

Verifies that every outcome ID in the active work unit's ``satisfies[]`` list is
present in the staged test diff. Marker discovery is intentionally textual and
configuration-driven via the project config's ``[test_markers]`` section.
"""

from __future__ import annotations

import fnmatch
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from woof.checks import CheckContext, CheckOutcome
from woof.project_config import ProjectConfigError, load_project_config

CHECK_ID = "check_2_outcome_markers"
_MANUAL_TEST_TYPES = {"documentation", "manual"}

_DEFAULT_MARKER_CONFIG: dict[str, Any] = {
    "languages": {
        "python": {
            "test_paths": ["tests/", "src/**/test_*.py"],
            "marker_regex": r"(?<![A-Za-z0-9])O\d+(?![A-Za-z0-9])",
            "docstring_keyword": "outcomes:",
            "comment_prefix": "#",
            "context_lines": 3,
        },
        "typescript": {
            "test_paths": ["tests/", "src/**/*.test.ts"],
            "marker_regex": r"(?<![A-Za-z0-9])O\d+(?![A-Za-z0-9])",
            "docstring_keyword": "outcomes:",
            "comment_prefix": "//",
            "context_lines": 3,
        },
    }
}


@dataclass(frozen=True)
class _LanguageMarkers:
    name: str
    test_paths: tuple[str, ...]
    marker_regex: re.Pattern[str]


def check_2_outcome_markers_runner(ctx: CheckContext) -> CheckOutcome:
    work_unit = _work_unit_for_id(ctx.plan, ctx.work_unit_id)
    if work_unit is None:
        return CheckOutcome(
            id=CHECK_ID,
            ok=False,
            severity="blocker",
            summary=f"work unit {ctx.work_unit_id!r} not found in plan.json",
        )

    satisfies = work_unit.get("satisfies")
    if not isinstance(satisfies, list) or not all(isinstance(item, str) for item in satisfies):
        return CheckOutcome(
            id=CHECK_ID,
            ok=False,
            severity="blocker",
            summary=f"work unit {ctx.work_unit_id} has malformed satisfies[]",
        )

    if not _requires_automated_test_markers(work_unit):
        return CheckOutcome(
            id=CHECK_ID,
            ok=True,
            severity="info",
            summary=(
                f"work unit {ctx.work_unit_id} declares no automated test work; "
                "outcome marker check skipped"
            ),
        )

    required = list(dict.fromkeys(satisfies))
    if not required:
        return CheckOutcome(
            id=CHECK_ID,
            ok=True,
            severity="info",
            summary=f"work unit {ctx.work_unit_id} declares no outcome markers to verify",
        )

    config_result = _load_marker_config()
    if isinstance(config_result, CheckOutcome):
        return config_result
    languages = config_result

    staged_result = _staged_paths(ctx.repo_root)
    if isinstance(staged_result, CheckOutcome):
        return staged_result

    test_paths = _matching_test_paths(staged_result, languages)
    if not test_paths:
        return CheckOutcome(
            id=CHECK_ID,
            ok=False,
            severity="blocker",
            summary=f"missing staged test markers for outcomes: {', '.join(required)}",
            evidence="no staged test files matched the configured test_paths",
        )

    diff_result = _staged_added_lines(ctx.repo_root, test_paths)
    if isinstance(diff_result, CheckOutcome):
        return diff_result

    found: dict[str, set[str]] = {outcome_id: set() for outcome_id in required}
    regexes = [language.marker_regex for language in languages]
    for rel_path, line in diff_result:
        for regex in regexes:
            for marker in _markers_from_line(regex, line):
                if marker in found:
                    found[marker].add(rel_path)

    missing = [outcome_id for outcome_id in required if not found[outcome_id]]
    if missing:
        present = {outcome_id: sorted(paths) for outcome_id, paths in found.items() if paths}
        return CheckOutcome(
            id=CHECK_ID,
            ok=False,
            severity="blocker",
            summary=f"missing staged test markers for outcomes: {', '.join(missing)}",
            evidence=f"present markers: {_format_present(present)}",
            paths=test_paths,
        )

    present = {outcome_id: sorted(paths) for outcome_id, paths in found.items()}
    return CheckOutcome(
        id=CHECK_ID,
        ok=True,
        severity="info",
        summary=f"all {len(required)} outcome marker(s) present in staged test diff",
        evidence=_format_present(present),
        paths=test_paths,
    )


def _work_unit_for_id(plan: dict[str, Any], work_unit_id: str) -> dict[str, Any] | None:
    work_units = plan.get("work_units", [])
    if not isinstance(work_units, list):
        return None
    for work_unit in work_units:
        if isinstance(work_unit, dict) and work_unit.get("id") == work_unit_id:
            return work_unit
    return None


def _requires_automated_test_markers(work_unit: dict[str, Any]) -> bool:
    tests = work_unit.get("tests")
    if not isinstance(tests, dict):
        return True

    count = tests.get("count")
    if type(count) is int and count <= 0:
        return False

    types = tests.get("types")
    return not (
        isinstance(types, list)
        and types
        and all(isinstance(item, str) and item.lower() in _MANUAL_TEST_TYPES for item in types)
    )


def _load_marker_config() -> list[_LanguageMarkers] | CheckOutcome:
    """Resolve marker rules from ``[test_markers.languages.*]``.

    The section is optional: a project that does not declare one gets the
    built-in Python and TypeScript rules.
    """

    try:
        config = load_project_config()
    except ProjectConfigError as exc:
        return CheckOutcome(
            id=CHECK_ID,
            ok=False,
            severity="blocker",
            summary=str(exc),
        )

    if not config.test_markers.declared:
        return _default_markers()

    parsed: list[_LanguageMarkers] = []
    for name, language in config.test_markers.languages.items():
        try:
            compiled = re.compile(language.marker_regex)
        except re.error as exc:
            return _malformed_config(f"{name}.marker_regex is invalid: {exc}")
        parsed.append(
            _LanguageMarkers(name=name, test_paths=language.test_paths, marker_regex=compiled)
        )
    return parsed


def _default_markers() -> list[_LanguageMarkers]:
    return [
        _LanguageMarkers(
            name=name,
            test_paths=tuple(block["test_paths"]),
            marker_regex=re.compile(str(block["marker_regex"])),
        )
        for name, block in _DEFAULT_MARKER_CONFIG["languages"].items()
    ]


def _malformed_config(detail: str) -> CheckOutcome:
    return CheckOutcome(
        id=CHECK_ID,
        ok=False,
        severity="blocker",
        summary=f"malformed test marker config: {detail}",
        paths=[],
    )


def _staged_paths(repo_root: Path) -> list[str] | CheckOutcome:
    proc = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "-z"],
        cwd=repo_root,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        return CheckOutcome(
            id=CHECK_ID,
            ok=False,
            severity="blocker",
            summary="failed to read staged paths",
            evidence=proc.stderr.decode(errors="replace").strip() or None,
            command="git diff --cached --name-only -z",
            exit_code=proc.returncode,
        )
    return sorted(path.decode() for path in proc.stdout.split(b"\0") if path)


def _matching_test_paths(paths: list[str], languages: list[_LanguageMarkers]) -> list[str]:
    patterns = [pattern for language in languages for pattern in language.test_paths]
    return [path for path in paths if any(_path_matches(path, pattern) for pattern in patterns)]


def _path_matches(path: str, pattern: str) -> bool:
    normalised_pattern = pattern.replace("\\", "/")
    normalised_path = path.replace("\\", "/")

    if normalised_pattern.endswith("/"):
        prefix = normalised_pattern.rstrip("/")
        return normalised_path == prefix or normalised_path.startswith(f"{prefix}/")

    glob_chars = set("*?[")
    if not any(char in normalised_pattern for char in glob_chars):
        return normalised_path == normalised_pattern or normalised_path.startswith(
            f"{normalised_pattern.rstrip('/')}/"
        )

    return fnmatch.fnmatchcase(normalised_path, normalised_pattern)


def _staged_added_lines(repo_root: Path, paths: list[str]) -> list[tuple[str, str]] | CheckOutcome:
    proc = subprocess.run(
        ["git", "diff", "--cached", "--unified=0", "--", *paths],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return CheckOutcome(
            id=CHECK_ID,
            ok=False,
            severity="blocker",
            summary="failed to read staged test diff",
            evidence=proc.stderr.strip() or None,
            command=f"git diff --cached --unified=0 -- {' '.join(paths)}",
            exit_code=proc.returncode,
            paths=paths,
        )

    current_path: str | None = None
    added: list[tuple[str, str]] = []
    for line in proc.stdout.splitlines():
        if line.startswith("+++ b/"):
            current_path = line.removeprefix("+++ b/")
            continue
        if line.startswith("+++ "):
            current_path = None
            continue
        if current_path and line.startswith("+") and not line.startswith("+++"):
            added.append((current_path, line[1:]))
    return added


def _markers_from_line(regex: re.Pattern[str], line: str) -> list[str]:
    markers: list[str] = []
    for match in regex.findall(line):
        if isinstance(match, tuple):
            markers.extend(part for part in match if part)
        else:
            markers.append(match)
    return markers


def _format_present(present: dict[str, list[str]]) -> str:
    if not present:
        return "none"
    return "; ".join(
        f"{outcome_id}: {', '.join(paths)}" for outcome_id, paths in sorted(present.items())
    )
