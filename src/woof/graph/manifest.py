"""Transaction manifest construction and verification."""

from __future__ import annotations

import fnmatch
from pathlib import Path

from woof.graph.git import changed_paths, staged_paths
from woof.graph.state import ManifestVerification, StorySpec, TransactionManifest


def _rel(repo_root: Path, path: Path) -> str:
    return path.relative_to(repo_root).as_posix()


def _matches_any(path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(path, pattern) for pattern in patterns)


def _audit_paths(epic_dir: Path, repo_root: Path) -> list[str]:
    audit_dir = epic_dir / "audit"
    if not audit_dir.is_dir():
        return []
    return sorted(
        _rel(repo_root, path)
        for path in audit_dir.rglob("*")
        if path.is_file() and "raw" not in path.relative_to(audit_dir).parts
    )


def build_story_manifest(repo_root: Path, epic_id: int, story: StorySpec) -> TransactionManifest:
    """Compute the exact file set expected for a story commit."""

    epic_dir = repo_root / ".woof" / "epics" / f"E{epic_id}"
    required_paths = [
        f".woof/epics/E{epic_id}/plan.json",
        f".woof/epics/E{epic_id}/epic.jsonl",
        f".woof/epics/E{epic_id}/dispatch.jsonl",
        f".woof/epics/E{epic_id}/critique/story-{story.id}.md",
    ]
    audit_paths = _audit_paths(epic_dir, repo_root)
    story_paths = [
        path
        for path in changed_paths(repo_root)
        if not path.startswith(".woof/") and _matches_any(path, story.paths)
    ]
    expected = sorted(set(required_paths + audit_paths + story_paths))
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
