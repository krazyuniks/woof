#!/usr/bin/env python3
"""Generate the vendored `woof-brainstorm` operator skill from the canonical source.

Woof is a standalone public repo: it must not depend on the agent-toolkit
checkout at runtime. The interactive design specialist therefore lives in
``skills/woof-brainstorm/`` as a generated, pinned, drift-checked bundle.

This supersedes ``vendor_brainstorm.py``. Rather than copying the canonical
files verbatim into ``playbooks/``, it *composes* ``skills/woof-brainstorm/SKILL.md``
from two parts:

- a **woof wrapper** (woof-owned: epic context, the deterministic discovery
  bucket, the Revise/Start-fresh/Cancel redo flow, and the ``woof wf`` handoff),
  followed by
- the **canonical brainstorm body** (the two loops, contracts, and artefacts),
  vendored verbatim between BEGIN/END markers.

The companion format docs (``TEMPLATE.md``, ``CONTEXT-FORMAT.md``,
``ADR-FORMAT.md``, ``ACKNOWLEDGEMENTS.md``) are vendored verbatim alongside it.

``VENDOR.json`` pins the source commit and records the hash of the embedded
canonical body plus each companion file. ``--check`` re-derives those hashes
from the emitted skill and compares - a CI-safe drift guard that needs no source
checkout. The copy flows one way, agent-toolkit -> Woof.

Usage:
    gen_woof_brainstorm.py            # regenerate from the source skill
    gen_woof_brainstorm.py --check    # verify the emitted skill against VENDOR.json
    gen_woof_brainstorm.py --source <dir>   # override the source skill directory
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = REPO_ROOT / "skills" / "woof-brainstorm"
SKILL_MD = SKILL_DIR / "SKILL.md"
MANIFEST = SKILL_DIR / "VENDOR.json"

# Verbatim companion docs the canonical body references. SKILL.md is composed
# (wrapper + vendored body) and handled separately.
COMPANION_FILES = (
    "TEMPLATE.md",
    "CONTEXT-FORMAT.md",
    "ADR-FORMAT.md",
    "ACKNOWLEDGEMENTS.md",
)

BEGIN_MARKER = "<!-- BEGIN VENDORED canonical body (agent-toolkit skills/brainstorm) -->"
END_MARKER = "<!-- END VENDORED canonical body -->"

COMPANION_MARKER = (
    "<!-- VENDORED from agent-toolkit skills/brainstorm - do not edit here. "
    "Regenerate with `just gen-brainstorm`. -->\n\n"
)

FRONT_MATTER = """\
---
name: woof-brainstorm
description: Interactive design discovery for a Woof epic. Runs the two brainstorm loops (Brainstorm then Grill Me) and writes the resolved design bundle straight into the epic's discovery/brainstorm/ bucket, then hands off to `woof wf`. The /woof umbrella routes the design phase here; invoke directly as /woof:brainstorm.
allowed-tools: Bash(woof:*), Bash(git:*), Read, Write, Edit, Glob, Grep, AskUserQuestion, Task
---
"""

WRAPPER = """\
# Woof Brainstorm

The interactive design specialist for a Woof epic. The `/woof` umbrella routes the design phase
here, or invoke it directly as `/woof:brainstorm`. It runs the two-loop brainstorm process below
and hydrates a Woof epic: it turns the epic's one-line spark into a resolved design bundle that the
deterministic graph then decomposes into stories.

Everything from "Canonical process" onward is the shared brainstorm skill, vendored from
agent-toolkit (pinned and drift-checked). This wrapper adds only what is specific to Woof: where the
epic lives, where the bundle is written, the redo flow, and the handoff.

## Wrapper: how this runs inside Woof

1. Find the epic. Read `.woof/.current-epic` (it holds `E<N>`) to get the active epic; use a
   different epic only if the operator names one. If there is no current epic, stop and tell them to
   create one first with `woof wf new "<spark>"` (the umbrella does this). Brainstorm hydrates an
   existing spark; it does not create epics.

2. Read the spark. `.woof/epics/E<N>/spark.md` is the seed for Loop 1; treat it as the Contract 0
   input. The design concerns the consumer repository, so this is a brownfield discovery whenever
   there is existing code to grill against.

3. Check for an existing bundle (redo). If `.woof/epics/E<N>/discovery/brainstorm/` already holds a
   design, do not silently overwrite it. Ask with `AskUserQuestion`:
   - Revise: load the existing bundle, continue the loops from it, and save back into the same
     bucket.
   - Start fresh: run `woof wf reset --epic <N> --yes`, which returns the epic to its spark - it
     deletes the brainstorm bundle and everything derived from it (discovery, EPIC.md, the plan,
     critiques, gate and result files) while keeping spark.md, the tracker linkage, and the epic log
     - then run the loops from scratch.
   - Cancel: leave everything untouched and stop.
   Never edit or delete files under `.woof/` by hand; the reset verb is the only way to clear
   derived state.

4. Run the two loops. Follow the canonical process below and produce the bundle: the design
   document, `CONTEXT.md`, and any ADRs.

5. Write into the deterministic bucket. Write the whole bundle directly into
   `.woof/epics/E<N>/discovery/brainstorm/` - there is no path argument and the operator never
   chooses a location; Woof owns it. The bundle is self-contained in the bucket: the resolved design
   document there (its front-matter is Contract 2), `CONTEXT.md` alongside it, and any ADRs under
   `discovery/brainstorm/adr/NNNN-slug.md` with `adr_refs` as paths relative to the design document.
   Keep discovery ADRs in the bucket, not the consumer repo's `docs/adr/`, unless the epic is
   explicitly about changing repo docs. The design document's front-matter is the bundle manifest:
   `tier`, `status: accepted`, `work_units[]`, `open_questions[]`, `context_ref`, `adr_refs[]`.

6. Validate before handoff. Woof validates natively: run
   `woof validate --schema brainstorm <the design document>` and fix anything it reports. (Inside
   Woof the native validator is the contract check; the canonical body's reference to a standalone
   `validate.py` is the toolkit path.) A `status: rejected` bundle is the back-edge: it stays in
   Loop 1 and is not handed off.

7. Hand off. Run `woof wf --epic <N>`. With the `discovery/brainstorm/` bucket present, the graph
   skips the headless research/thinking/ideate chain, runs synthesis over `discovery/`, and
   decomposes the bundle into an `EPIC.md` and a story plan. Surface any gate the graph opens to the
   operator and let them resolve it; never auto-approve a gate.

## Canonical process
"""


def default_source() -> Path:
    env = os.environ.get("BRAINSTORM_SKILL_SRC")
    if env:
        return Path(env).expanduser()
    return Path("~/Work/agent-toolkit/skills/brainstorm").expanduser()


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _source_commit(source: Path) -> str:
    try:
        out = subprocess.run(
            ["git", "-C", str(source), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
        return out.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


def _strip_front_matter(text: str) -> str:
    """Return the markdown body of a skill file, dropping its YAML front-matter."""
    if not text.startswith("---\n"):
        return text
    end = text.find("\n---\n", 4)
    if end < 0:
        return text
    body = text[end + len("\n---\n") :]
    return body.lstrip("\n")


def _generated_marker(commit: str) -> str:
    return (
        f"<!-- GENERATED by scripts/gen_woof_brainstorm.py from agent-toolkit skills/brainstorm "
        f"@ {commit}. The 'Wrapper' section is woof-owned; the block between the BEGIN/END VENDORED "
        f"markers is the canonical body, pinned and drift-checked against VENDOR.json. Do not edit "
        f"by hand - regenerate with `just gen-brainstorm`. -->\n\n"
    )


def _embed_body(canonical_body: str) -> str:
    """The exact text embedded between the BEGIN/END markers (drift-hashed)."""
    return canonical_body if canonical_body.endswith("\n") else canonical_body + "\n"


def _compose_skill(canonical_body: str, commit: str) -> tuple[str, str]:
    """Return (full SKILL.md text, embedded canonical body)."""
    embedded = _embed_body(canonical_body)
    text = (
        FRONT_MATTER
        + "\n"
        + _generated_marker(commit)
        + WRAPPER
        + "\n"
        + BEGIN_MARKER
        + "\n"
        + embedded
        + END_MARKER
        + "\n"
    )
    return text, embedded


def _extract_embedded_body(skill_text: str) -> str | None:
    """Pull the vendored canonical body back out of an emitted SKILL.md."""
    begin = skill_text.find(BEGIN_MARKER)
    end = skill_text.find(END_MARKER)
    if begin < 0 or end < 0 or end < begin:
        return None
    start = begin + len(BEGIN_MARKER) + 1  # skip the newline after the marker
    return skill_text[start:end]


def generate(source: Path) -> int:
    if not source.is_dir():
        sys.stderr.write(f"gen-brainstorm: source not found: {source}\n")
        return 1
    src_skill = source / "SKILL.md"
    if not src_skill.is_file():
        sys.stderr.write(f"gen-brainstorm: missing source file: {src_skill}\n")
        return 1

    SKILL_DIR.mkdir(parents=True, exist_ok=True)
    commit = _source_commit(source)

    canonical_body = _strip_front_matter(src_skill.read_text(encoding="utf-8"))
    skill_text, embedded = _compose_skill(canonical_body, commit)
    SKILL_MD.write_text(skill_text, encoding="utf-8")

    companions: dict[str, str] = {}
    for name in COMPANION_FILES:
        src = source / name
        if not src.is_file():
            sys.stderr.write(f"gen-brainstorm: missing source file: {src}\n")
            return 1
        content = COMPANION_MARKER + src.read_text(encoding="utf-8")
        (SKILL_DIR / name).write_text(content, encoding="utf-8")
        companions[name] = _sha256(content)

    manifest = {
        "source": {
            "repo": "agent-toolkit",
            "path": "skills/brainstorm",
            "commit": commit,
        },
        "generator": "scripts/gen_woof_brainstorm.py",
        "skill_body_sha256": _sha256(embedded),
        "companions": companions,
    }
    MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(
        f"gen-brainstorm: generated skills/woof-brainstorm/ "
        f"({len(companions) + 1} files) from {source} commit {commit[:12]}"
    )
    return 0


def check() -> list[str]:
    """Return a list of drift errors; empty means the generated skill is intact."""
    errors: list[str] = []
    if not MANIFEST.is_file():
        return [f"missing manifest: {MANIFEST}"]
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

    if not SKILL_MD.is_file():
        errors.append("missing SKILL.md")
    else:
        skill_text = SKILL_MD.read_text(encoding="utf-8")
        if "name: woof-brainstorm" not in skill_text:
            errors.append("SKILL.md: missing the woof-brainstorm front-matter")
        if "GENERATED by scripts/gen_woof_brainstorm.py" not in skill_text:
            errors.append("SKILL.md: missing the GENERATED marker")
        embedded = _extract_embedded_body(skill_text)
        if embedded is None:
            errors.append("SKILL.md: missing the BEGIN/END VENDORED markers")
        elif _sha256(embedded) != manifest.get("skill_body_sha256"):
            errors.append(
                "SKILL.md: vendored canonical body drift (hand-edited? regenerate with "
                "`just gen-brainstorm`)"
            )

    recorded = manifest.get("companions", {})
    for name in COMPANION_FILES:
        path = SKILL_DIR / name
        if not path.is_file():
            errors.append(f"missing companion file: {name}")
            continue
        text = path.read_text(encoding="utf-8")
        if "VENDORED from agent-toolkit" not in text:
            errors.append(f"{name}: missing the VENDORED marker")
        if name not in recorded:
            errors.append(f"{name}: not in VENDOR.json")
        elif _sha256(text) != recorded[name]:
            errors.append(
                f"{name}: hash drift (hand-edited? regenerate with `just gen-brainstorm`)"
            )
    extra = set(recorded) - set(COMPANION_FILES)
    if extra:
        errors.append(f"VENDOR.json lists unexpected companions: {', '.join(sorted(extra))}")
    return errors


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Generate the vendored woof-brainstorm skill from agent-toolkit."
    )
    parser.add_argument(
        "--check", action="store_true", help="verify the emitted skill against VENDOR.json"
    )
    parser.add_argument("--source", type=Path, default=None, help="source skill directory")
    args = parser.parse_args(argv)
    if args.check:
        errors = check()
        if errors:
            sys.stderr.write("gen-brainstorm: drift detected:\n")
            for err in errors:
                sys.stderr.write(f"  - {err}\n")
            return 1
        print(f"gen-brainstorm: skills/woof-brainstorm/ intact ({len(COMPANION_FILES) + 1} files).")
        return 0
    return generate(args.source or default_source())


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
