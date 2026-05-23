"""Transaction manifest construction and verification."""

from __future__ import annotations

from pathlib import Path

from woof.graph.dispositions import story_disposition_relpath
from woof.graph.git import changed_paths, staged_paths
from woof.graph.pathspec import PathspecEvaluationError, filter_paths_matching
from woof.graph.state import ManifestVerification, StorySpec, TransactionManifest


def _rel(repo_root: Path, path: Path) -> str:
    return path.relative_to(repo_root).as_posix()


def _audit_paths(epic_dir: Path, repo_root: Path) -> list[str]:
    audit_dir = epic_dir / "audit"
    if not audit_dir.is_dir():
        return []
    return sorted(
        _rel(repo_root, path)
        for path in audit_dir.rglob("*")
        if path.is_file() and "raw" not in path.relative_to(audit_dir).parts
    )


def durable_epic_paths(epic_dir: Path, repo_root: Path) -> list[str]:
    """Return durable epic-state files that belong in a story transaction."""

    if not epic_dir.is_dir():
        return []
    transient_names = {
        ".wf.lock",
        ".last-sync",
        "gate.md",
        "executor_result.json",
        "check-result.json",
    }
    durable: list[str] = []
    for path in sorted(epic_dir.rglob("*")):
        if not path.is_file():
            continue
        relative_parts = path.relative_to(epic_dir).parts
        if not relative_parts:
            continue
        if relative_parts[0] == "audit" and "raw" in relative_parts:
            continue
        if relative_parts[-1] in transient_names:
            continue
        durable.append(_rel(repo_root, path))
    return durable


def build_story_manifest(repo_root: Path, epic_id: int, story: StorySpec) -> TransactionManifest:
    """Compute the exact file set expected for a story commit."""

    epic_dir = repo_root / ".woof" / "epics" / f"E{epic_id}"
    required_paths = [
        f".woof/epics/E{epic_id}/plan.json",
        f".woof/epics/E{epic_id}/epic.jsonl",
        f".woof/epics/E{epic_id}/dispatch.jsonl",
        f".woof/epics/E{epic_id}/critique/story-{story.id}.md",
        story_disposition_relpath(epic_id, story.id),
    ]
    audit_paths = _audit_paths(epic_dir, repo_root)
    durable_paths = durable_epic_paths(epic_dir, repo_root)
    candidate_paths = [path for path in changed_paths(repo_root) if not path.startswith(".woof/")]
    try:
        story_paths = filter_paths_matching(repo_root, candidate_paths, list(story.paths))
    except PathspecEvaluationError:
        story_paths = []
    expected = sorted(set(required_paths + durable_paths + audit_paths + story_paths))
    return TransactionManifest(
        epic_id=epic_id,
        story_id=story.id,
        expected_paths=expected,
        story_paths=story_paths,
        required_paths=required_paths,
        audit_paths=audit_paths,
    )


def verify_staged_manifest(repo_root: Path, manifest: TransactionManifest) -> ManifestVerification:
    staged = staged_paths(repo_root)
    expected = sorted(manifest.expected_paths)
    missing = [path for path in expected if path not in staged]
    extra = [path for path in staged if path not in expected]
    return ManifestVerification(
        ok=not missing and not extra,
        manifest=manifest,
        staged_paths=staged,
        missing_paths=missing,
        extra_paths=extra,
    )
