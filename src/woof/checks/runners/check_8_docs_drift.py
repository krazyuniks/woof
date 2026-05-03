"""check_8_docs_drift - Stage-5 Check 8.

Verifies optional project docs-drift mappings from ``.woof/docs-paths.toml``.
When the config is absent this check is intentionally a no-op. When present,
each mapping that matches a staged code path requires at least one staged docs
path matching the paired documentation pattern.
"""

from __future__ import annotations

import fnmatch
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path

from woof.checks import CheckContext, CheckOutcome

CHECK_ID = "check_8_docs_drift"
CONFIG_PATH = ".woof/docs-paths.toml"


@dataclass(frozen=True)
class _DocsPathMapping:
    code_pattern: str
    doc_pattern: str
    rationale: str | None = None


def check_8_docs_drift_runner(ctx: CheckContext) -> CheckOutcome:
    config_path = ctx.repo_root / CONFIG_PATH
    if not config_path.exists():
        return CheckOutcome(
            id=CHECK_ID,
            ok=True,
            severity="info",
            summary=f"{CONFIG_PATH} absent; docs drift check skipped",
        )

    mappings = _load_mappings(config_path, ctx.repo_root)
    if isinstance(mappings, CheckOutcome):
        return mappings

    staged = _staged_paths(ctx.repo_root)
    if isinstance(staged, CheckOutcome):
        return staged

    doc_paths_by_pattern = {
        mapping.doc_pattern: [path for path in staged if _path_matches(path, mapping.doc_pattern)]
        for mapping in mappings
    }

    missing: list[str] = []
    triggered_code_paths: list[str] = []
    touched_doc_paths: list[str] = []
    for mapping in mappings:
        code_paths = [path for path in staged if _path_matches(path, mapping.code_pattern)]
        if not code_paths:
            continue

        triggered_code_paths.extend(code_paths)
        docs = doc_paths_by_pattern[mapping.doc_pattern]
        touched_doc_paths.extend(docs)
        if docs:
            continue

        line = (
            f"{mapping.code_pattern!r} matched {sorted(code_paths)!r}; "
            f"requires staged doc path matching {mapping.doc_pattern!r}"
        )
        if mapping.rationale:
            line = f"{line} ({mapping.rationale})"
        missing.append(line)

    if missing:
        return CheckOutcome(
            id=CHECK_ID,
            ok=False,
            severity="blocker",
            summary=f"docs drift detected for {len(missing)} mapping(s)",
            evidence="\n".join(missing),
            paths=sorted(set(triggered_code_paths)),
            command="git diff --cached --name-only -z",
        )

    if triggered_code_paths:
        return CheckOutcome(
            id=CHECK_ID,
            ok=True,
            severity="info",
            summary=(
                f"{len(set(triggered_code_paths))} mapped code path(s) accompanied by "
                f"{len(set(touched_doc_paths))} mapped docs path(s)"
            ),
            paths=sorted(set(triggered_code_paths + touched_doc_paths)),
            command="git diff --cached --name-only -z",
        )

    return CheckOutcome(
        id=CHECK_ID,
        ok=True,
        severity="info",
        summary="no staged paths matched docs drift code mappings",
        paths=staged,
        command="git diff --cached --name-only -z",
    )


def _load_mappings(path: Path, repo_root: Path) -> list[_DocsPathMapping] | CheckOutcome:
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        return _config_failure(repo_root, path, f"TOML parse error: {exc}")

    mappings = data.get("mappings")
    if not isinstance(mappings, list) or not mappings:
        return _config_failure(repo_root, path, "mappings must be a non-empty array")

    parsed: list[_DocsPathMapping] = []
    for index, item in enumerate(mappings, start=1):
        if not isinstance(item, dict):
            return _config_failure(repo_root, path, f"mappings[{index}] must be a table")
        unknown_keys = set(item) - {"code_pattern", "doc_pattern", "rationale"}
        if unknown_keys:
            return _config_failure(
                repo_root,
                path,
                f"mappings[{index}] has unsupported keys: {sorted(unknown_keys)!r}",
            )
        code_pattern = item.get("code_pattern")
        doc_pattern = item.get("doc_pattern")
        rationale = item.get("rationale")
        if not isinstance(code_pattern, str) or not code_pattern:
            return _config_failure(
                repo_root, path, f"mappings[{index}].code_pattern must be a non-empty string"
            )
        if not isinstance(doc_pattern, str) or not doc_pattern:
            return _config_failure(
                repo_root, path, f"mappings[{index}].doc_pattern must be a non-empty string"
            )
        if rationale is not None and not isinstance(rationale, str):
            return _config_failure(repo_root, path, f"mappings[{index}].rationale must be a string")
        parsed.append(
            _DocsPathMapping(
                code_pattern=code_pattern,
                doc_pattern=doc_pattern,
                rationale=rationale,
            )
        )

    return parsed


def _config_failure(repo_root: Path, path: Path, detail: str) -> CheckOutcome:
    return CheckOutcome(
        id=CHECK_ID,
        ok=False,
        severity="blocker",
        summary=f"malformed docs-paths config: {detail}",
        paths=[str(path.relative_to(repo_root))],
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
