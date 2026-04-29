"""check_2_outcome_markers — Stage-5 Check 2.

Verifies that every outcome ID in the active story's ``satisfies[]`` list is
present in the staged test diff. Marker discovery is intentionally textual and
configuration-driven via ``.woof/test-markers.toml``.
"""

from __future__ import annotations

import fnmatch
import re
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from woof.checks import CheckContext, CheckOutcome

CHECK_ID = "check_2_outcome_markers"

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
    story = _story_for_id(ctx.plan, ctx.story_id)
    if story is None:
        return CheckOutcome(
            id=CHECK_ID,
            ok=False,
            severity="blocker",
            summary=f"story {ctx.story_id!r} not found in plan.json",
        )

    satisfies = story.get("satisfies")
    if not isinstance(satisfies, list) or not all(isinstance(item, str) for item in satisfies):
        return CheckOutcome(
            id=CHECK_ID,
            ok=False,
            severity="blocker",
            summary=f"story {ctx.story_id} has malformed satisfies[]",
        )

    required = list(dict.fromkeys(satisfies))
    if not required:
        return CheckOutcome(
            id=CHECK_ID,
            ok=True,
            severity="info",
            summary=f"story {ctx.story_id} declares no outcome markers to verify",
        )

    config_result = _load_marker_config(ctx.repo_root)
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
            evidence="no staged test files matched .woof/test-markers.toml test_paths",
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


def _story_for_id(plan: dict[str, Any], story_id: str) -> dict[str, Any] | None:
    stories = plan.get("stories", [])
    if not isinstance(stories, list):
        return None
    for story in stories:
        if isinstance(story, dict) and story.get("id") == story_id:
            return story
    return None


def _load_marker_config(repo_root: Path) -> list[_LanguageMarkers] | CheckOutcome:
    path = repo_root / ".woof" / "test-markers.toml"
    if path.exists():
        try:
            with path.open("rb") as fh:
                data = tomllib.load(fh)
        except tomllib.TOMLDecodeError as exc:
            return CheckOutcome(
                id=CHECK_ID,
                ok=False,
                severity="blocker",
                summary=f"test marker config TOML parse error: {exc}",
                paths=[str(path.relative_to(repo_root))],
            )
    else:
        data = _DEFAULT_MARKER_CONFIG

    languages = data.get("languages")
    if not isinstance(languages, dict) or not languages:
        return CheckOutcome(
            id=CHECK_ID,
            ok=False,
            severity="blocker",
            summary="test marker config must define non-empty [languages] table",
            paths=[str(path.relative_to(repo_root))] if path.exists() else [],
        )

    parsed: list[_LanguageMarkers] = []
    for name, block in languages.items():
        if not isinstance(name, str) or not isinstance(block, dict):
            return _malformed_config(path, repo_root, f"language block {name!r} must be a table")

        test_paths = block.get("test_paths")
        marker_regex = block.get("marker_regex")
        if not isinstance(test_paths, list) or not test_paths:
            return _malformed_config(path, repo_root, f"{name}.test_paths must be a non-empty list")
        if not all(isinstance(item, str) and item for item in test_paths):
            return _malformed_config(path, repo_root, f"{name}.test_paths must contain strings")
        if not isinstance(marker_regex, str) or not marker_regex:
            return _malformed_config(
                path, repo_root, f"{name}.marker_regex must be a non-empty string"
            )
        try:
            compiled = re.compile(marker_regex)
        except re.error as exc:
            return _malformed_config(path, repo_root, f"{name}.marker_regex is invalid: {exc}")

        parsed.append(
            _LanguageMarkers(
                name=name,
                test_paths=tuple(test_paths),
                marker_regex=compiled,
            )
        )

    return parsed


def _malformed_config(path: Path, repo_root: Path, detail: str) -> CheckOutcome:
    paths = [str(path.relative_to(repo_root))] if path.exists() else []
    return CheckOutcome(
        id=CHECK_ID,
        ok=False,
        severity="blocker",
        summary=f"malformed test marker config: {detail}",
        paths=paths,
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
