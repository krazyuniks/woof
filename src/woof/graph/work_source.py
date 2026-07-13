"""Work-unit state writeback to the work-source PM document (ADR-017).

A work-source document - an epic, or a ``work_units[]`` backlog - is an input,
not engine state. It lives where the project's PM convention puts it and the
engine reads it. Writing a unit's ``state:`` back to it is the one deliberate
exception where the engine writes outside the operator home, and the exception is
narrow in three ways:

* Only the engine writes it. A producer that mutates unit state in the drained
  document is rejected before publish by ``check_10_work_source_state``.
* Only the document is written. The engine puts no directory, artefact, or
  sidecar into the repository that holds it; even the writeback lock lives in the
  operator home.
* Only the one ``state:`` field of the one unit changes. The document is
  human-authored markdown, so the edit is a single-line substitution that
  preserves key order, quoting, comments, blank lines, and prose byte-for-byte.
  Round-tripping the document through a YAML dump would reformat an operator's
  file, which is a defect, not a writeback.

The document is resolved explicitly: it is the source the drain was invoked with
and recorded at intake, never inferred from the delivery repo, a conventional
location, or a directory name. A run whose aggregate has no work-source document
writes back nothing, which is a no-op rather than an error.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
from collections.abc import Generator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import yaml

from woof import state
from woof.graph.state import Plan

# The engine's work-unit states mapped onto the backlog schema's vocabulary.
# The backlog's ``blocked`` has no engine twin: the engine derives blocking from
# dependency state rather than recording it, so it never writes that value.
BACKLOG_STATE_BY_ENGINE_STATE = {
    "pending": "todo",
    "in_progress": "in_progress",
    "done": "done",
    "abandoned": "cancelled",
}

_FRONT_MATTER_FENCE = "---\n"
_WORK_UNITS_KEY = re.compile(r"^work_units:\s*(#.*)?$")
_TOP_LEVEL_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*:")
_ITEM = re.compile(r"^(?P<indent>[ \t]*)-[ \t]+(?P<rest>\S.*)$")
_KEY = re.compile(r"^(?P<indent>[ \t]*)(?P<key>[A-Za-z_][A-Za-z0-9_-]*):(?P<rest>.*)$")
_STATE_VALUE = re.compile(
    r"^(?P<prefix>[ \t]*(?:-[ \t]+)?state:[ \t]*)"
    r"(?P<quote>[\"']?)(?P<value>[^\"'#\s]*)(?P=quote)"
    r"(?P<suffix>.*)$"
)


class WorkSourceError(RuntimeError):
    """The work-source document cannot take the engine's unit-state edit."""


class WorkSourceConflictError(WorkSourceError):
    """The document changed underneath the engine; fail closed rather than clobber."""


@dataclass(frozen=True)
class WorkSourceWriteback:
    """What the engine wrote to the work-source document, and what it replaced."""

    document: Path
    work_unit_id: str
    previous_state: str
    state: str
    changed: bool
    text: str


def content_digest(text: str) -> str:
    """Digest a document's content, so a caller can prove it has not moved on."""

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def resolve_work_source(project_key: str, context: Mapping | object | None) -> Path | None:
    """Return the work-source document the drain was invoked with, or ``None``.

    A pre-decomposed work-unit set records its source at intake, so the aggregate
    context names the document. An epic-backed aggregate has no ``work_units[]``
    document behind it: its units are decomposed by the engine and their ids are
    local to the plan, so there is nothing to write back to.
    """

    payload = _context_payload(context)
    if payload is None or payload.get("kind") != "work_unit_set":
        return None
    set_id = payload.get("set_id")
    if not isinstance(set_id, str) or not set_id:
        return None
    metadata_path = state.work_unit_set_dir(project_key, set_id) / "intake.json"
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    source = metadata.get("source") if isinstance(metadata, dict) else None
    path = source.get("path") if isinstance(source, dict) else None
    if not isinstance(path, str) or not path:
        return None
    return Path(path)


def publish_unit_state(
    project_key: str,
    plan: Plan,
    work_unit_id: str,
    engine_state: str,
) -> WorkSourceWriteback | None:
    """Write ``engine_state`` back to the plan's work-source document, if it has one."""

    document = resolve_work_source(project_key, plan.context)
    if document is None:
        return None
    return writeback_unit_state(document, work_unit_id, engine_state)


def unit_states(text: str) -> dict[str, str]:
    """Return ``{work unit id: state}`` from a work-source document's front matter."""

    front = yaml.safe_load(_front_matter(text)) or {}
    if not isinstance(front, dict):
        return {}
    units = front.get("work_units")
    if not isinstance(units, list):
        return {}
    return {
        unit["id"]: str(unit.get("state", ""))
        for unit in units
        if isinstance(unit, dict) and isinstance(unit.get("id"), str)
    }


def writeback_unit_state(
    document: Path,
    work_unit_id: str,
    engine_state: str,
    *,
    expected_digest: str | None = None,
) -> WorkSourceWriteback:
    """Flip one unit's ``state:`` in ``document``, preserving every other byte.

    The read, the edit, and the replace happen under an exclusive lock, so two
    concurrent drains sharing one document serialise instead of clobbering each
    other. ``expected_digest`` is the digest of the content the caller acted on:
    if the document has moved on since, the writeback fails closed.
    """

    backlog_state = BACKLOG_STATE_BY_ENGINE_STATE.get(engine_state)
    if backlog_state is None:
        raise WorkSourceError(
            f"{engine_state} is not an engine work-unit state; "
            f"expected one of {', '.join(sorted(BACKLOG_STATE_BY_ENGINE_STATE))}"
        )

    with _document_lock(document):
        try:
            text = document.read_text(encoding="utf-8")
        except OSError as exc:
            raise WorkSourceError(f"work-source document {document} cannot be read: {exc}") from exc
        if expected_digest is not None and content_digest(text) != expected_digest:
            raise WorkSourceConflictError(
                f"work-source document {document} changed since the engine read it; "
                "refusing to overwrite another writer's edit"
            )

        line_index = _state_line_index(text, work_unit_id, document)
        lines = text.splitlines(keepends=True)
        match = _STATE_VALUE.match(lines[line_index].rstrip("\n"))
        if match is None:  # pragma: no cover - _state_line_index only returns matches
            raise WorkSourceError(
                f"work-source document {document}: work unit {work_unit_id} has a "
                "state: line the engine cannot edit surgically"
            )
        previous_state = match.group("value")
        if previous_state == backlog_state:
            return WorkSourceWriteback(
                document=document,
                work_unit_id=work_unit_id,
                previous_state=previous_state,
                state=backlog_state,
                changed=False,
                text=text,
            )

        newline = "\n" if lines[line_index].endswith("\n") else ""
        quote = match.group("quote")
        lines[line_index] = (
            f"{match.group('prefix')}{quote}{backlog_state}{quote}{match.group('suffix')}{newline}"
        )
        new_text = "".join(lines)
        _assert_only_the_target_state_changed(document, text, new_text, work_unit_id, backlog_state)
        _atomic_replace(document, new_text)

    return WorkSourceWriteback(
        document=document,
        work_unit_id=work_unit_id,
        previous_state=previous_state,
        state=backlog_state,
        changed=True,
        text=new_text,
    )


@contextmanager
def _document_lock(document: Path) -> Generator[None]:
    lock_path = state.work_source_lock_path(document.resolve())
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("w", encoding="utf-8") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _atomic_replace(document: Path, text: str) -> None:
    """Replace the document via a temporary sibling, as every durable engine write does."""

    tmp = document.with_name(f".{document.name}.woof-tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, document)
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        raise WorkSourceError(f"work-source document {document} cannot be written: {exc}") from exc


def _context_payload(context: Mapping | object | None) -> Mapping | None:
    if context is None:
        return None
    if isinstance(context, Mapping):
        return context
    dump = getattr(context, "model_dump", None)
    if dump is None:
        return None
    payload = dump()
    return payload if isinstance(payload, Mapping) else None


def _front_matter(text: str) -> str:
    if not text.startswith(_FRONT_MATTER_FENCE):
        raise WorkSourceError("work-source document must start with YAML front matter")
    end = text.find("\n" + _FRONT_MATTER_FENCE, len(_FRONT_MATTER_FENCE) - 1)
    if end < 0:
        raise WorkSourceError("work-source document has unterminated YAML front matter")
    return text[len(_FRONT_MATTER_FENCE) : end + 1]


def _state_line_index(text: str, work_unit_id: str, document: Path) -> int:
    """Return the index of the line holding ``work_unit_id``'s ``state:`` value.

    The front matter is walked as lines, not parsed and re-emitted: the document is
    human-authored, and a YAML round trip would rewrite the operator's formatting.
    """

    front_matter_lines = _front_matter(text).splitlines()
    offset = 1  # the opening --- fence
    inside = False
    item_indent: int | None = None
    key_indent: int | None = None
    matched_item = False
    state_line: int | None = None

    for index, line in enumerate(front_matter_lines):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not inside:
            inside = bool(_WORK_UNITS_KEY.match(line))
            continue
        item = _ITEM.match(line)
        if item is None and _TOP_LEVEL_KEY.match(line):
            break  # the next top-level key ends the work_units list
        if item is not None and (item_indent is None or len(item.group("indent")) == item_indent):
            if matched_item:
                break  # the next unit begins; this unit's block is complete
            item_indent = len(item.group("indent"))
            key_indent = item_indent + len(line[item_indent:]) - len(item.group("rest"))
            matched_item = _key_value(item.group("rest"), "id") == work_unit_id
            if matched_item and _key_value(item.group("rest"), "state") is not None:
                state_line = offset + index
            continue
        if not matched_item or key_indent is None:
            continue
        key = _KEY.match(line)
        if key is None or len(key.group("indent")) != key_indent:
            continue  # a nested value inside the unit, not one of its keys
        if key.group("key") == "state":
            state_line = offset + index

    if not matched_item:
        raise WorkSourceError(
            f"work-source document {document} has no work unit {work_unit_id}; "
            "refusing to guess which unit the engine meant"
        )
    if state_line is None:
        raise WorkSourceError(
            f"work-source document {document}: work unit {work_unit_id} declares no state:"
        )
    return state_line


def _key_value(text: str, key: str) -> str | None:
    match = _KEY.match(text)
    if match is None or match.group("key") != key:
        return None
    return _scalar(match.group("rest"))


def _scalar(text: str) -> str:
    value = text.strip()
    if value[:1] in {'"', "'"}:
        quote = value[0]
        end = value.find(quote, 1)
        return value[1:end] if end > 0 else value[1:]
    return value.split(" #", 1)[0].strip()


def _assert_only_the_target_state_changed(
    document: Path,
    before: str,
    after: str,
    work_unit_id: str,
    backlog_state: str,
) -> None:
    """Fail closed unless the edit changed exactly the one state the engine meant."""

    expected = dict(unit_states(before))
    expected[work_unit_id] = backlog_state
    if unit_states(after) != expected:
        raise WorkSourceError(
            f"work-source document {document}: writing {work_unit_id}={backlog_state} would "
            "change more than that unit's state; refusing to rewrite the operator's document"
        )
