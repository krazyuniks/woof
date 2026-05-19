"""Bundle portable Claude Code transcript references into an epic audit folder."""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

EPIC_REF_RE = re.compile(r"^E?([1-9]\d*)$")
CLAUDE_TRANSCRIPT_RE = re.compile(r"^~/\.claude/projects/([^/]+)/([^/]+\.jsonl)$")


class AuditBundleError(ValueError):
    """Raised when an audit bundle request cannot be completed."""


class NonPortableTranscriptError(AuditBundleError):
    """Raised when dispatch.jsonl contains a non-portable Claude transcript path."""


@dataclass(frozen=True)
class TranscriptCopy:
    reference: str
    destination: Path


@dataclass(frozen=True)
class MissingTranscript:
    reference: str


@dataclass(frozen=True)
class AuditBundleResult:
    epic: str
    dispatch_jsonl: Path
    destination_dir: Path
    copied: tuple[TranscriptCopy, ...] = field(default_factory=tuple)
    missing: tuple[MissingTranscript, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return not self.missing


def normalise_epic_ref(epic: str) -> str:
    """Return canonical ``E<N>`` form for an epic reference."""

    match = EPIC_REF_RE.fullmatch(epic.strip())
    if not match:
        raise AuditBundleError(f"invalid epic reference {epic!r}; expected E<N> or <N>")
    return f"E{match.group(1)}"


def bundle_claude_transcripts(
    repo_root: Path,
    epic: str,
    *,
    home: Path | None = None,
) -> AuditBundleResult:
    """Copy referenced Claude Code transcripts into ``audit/claude-code/``.

    ``dispatch.jsonl`` must store portable home-relative transcript references
    such as ``~/.claude/projects/<project-slug>/<session>.jsonl``. The bundle
    preserves the project slug below the destination directory to avoid filename
    collisions across source checkouts.
    """

    epic_ref = normalise_epic_ref(epic)
    epic_dir = repo_root / ".woof" / "epics" / epic_ref
    dispatch_jsonl = epic_dir / "dispatch.jsonl"
    if not epic_dir.is_dir():
        raise AuditBundleError(f"{epic_dir} not found")
    if not dispatch_jsonl.is_file():
        raise AuditBundleError(f"{dispatch_jsonl} not found")

    transcript_refs = _claude_transcript_refs(dispatch_jsonl)
    destination_dir = epic_dir / "audit" / "claude-code"
    home_dir = home or Path.home()
    copied: list[TranscriptCopy] = []
    missing: list[MissingTranscript] = []

    for reference in transcript_refs:
        source, destination = _paths_for_reference(
            reference,
            home=home_dir,
            destination_dir=destination_dir,
        )
        if not source.is_file():
            missing.append(MissingTranscript(reference=reference))
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        copied.append(TranscriptCopy(reference=reference, destination=destination))

    return AuditBundleResult(
        epic=epic_ref,
        dispatch_jsonl=dispatch_jsonl,
        destination_dir=destination_dir,
        copied=tuple(copied),
        missing=tuple(missing),
    )


def _claude_transcript_refs(dispatch_jsonl: Path) -> tuple[str, ...]:
    refs: list[str] = []
    seen: set[str] = set()
    for lineno, raw in enumerate(dispatch_jsonl.read_text().splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise AuditBundleError(f"{dispatch_jsonl}:{lineno}: invalid JSON: {exc}") from exc
        reference = _event_transcript_ref(event)
        if reference is None:
            continue
        _parse_portable_reference(reference, context=f"{dispatch_jsonl}:{lineno}")
        if reference not in seen:
            seen.add(reference)
            refs.append(reference)
    return tuple(refs)


def _event_transcript_ref(event: Any) -> str | None:
    if not isinstance(event, dict) or "claude_transcript_path" not in event:
        return None
    reference = event["claude_transcript_path"]
    if not isinstance(reference, str):
        raise NonPortableTranscriptError("claude_transcript_path must be a string")
    return reference


def _paths_for_reference(
    reference: str,
    *,
    home: Path,
    destination_dir: Path,
) -> tuple[Path, Path]:
    project_slug, filename = _parse_portable_reference(reference, context="transcript")
    source = home / ".claude" / "projects" / project_slug / filename
    destination = destination_dir / project_slug / filename
    return source, destination


def _parse_portable_reference(reference: str, *, context: str) -> tuple[str, str]:
    match = CLAUDE_TRANSCRIPT_RE.fullmatch(reference)
    if not match:
        raise NonPortableTranscriptError(
            f"{context}: Claude transcript path {reference!r} is not portable; "
            "expected ~/.claude/projects/<project-slug>/<session>.jsonl"
        )
    project_slug, filename = match.groups()
    if project_slug in {".", ".."} or filename in {".", ".."}:
        raise NonPortableTranscriptError(
            f"{context}: Claude transcript path {reference!r} contains unsafe path parts"
        )
    return project_slug, filename
