"""Transaction manifest construction and verification.

The manifest is the exact file set a work-unit commit may contain, and since
ADR-017 that set is *only* the work unit's own delivery paths. Engine state lives
in the operator home, so there is nothing engine-owned left to stage: no plan,
no event log, no critique, no disposition, no audit file. A delivery commit
contains the delivery change and nothing else.
"""

from __future__ import annotations

from pathlib import Path

from woof.graph.git import changed_paths, staged_paths
from woof.graph.pathspec import PathspecEvaluationError, filter_paths_matching
from woof.graph.state import ManifestVerification, TransactionManifest, WorkUnitSpec


def build_work_unit_manifest(repo_root: Path, work_unit: WorkUnitSpec) -> TransactionManifest:
    """Compute the exact file set expected for a work-unit commit.

    Changed files in the delivery checkout that match ``work_unit.paths[]``. The
    repo root is the only input that matters: the manifest describes the delivery
    diff, and the engine's state root is not part of it.
    """

    changed = changed_paths(repo_root)
    try:
        work_unit_paths = filter_paths_matching(repo_root, changed, list(work_unit.paths))
    except PathspecEvaluationError:
        work_unit_paths = []
    return TransactionManifest(
        work_unit_id=work_unit.id,
        expected_paths=sorted(set(work_unit_paths)),
        work_unit_paths=sorted(set(work_unit_paths)),
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
