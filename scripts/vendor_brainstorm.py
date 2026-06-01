#!/usr/bin/env python3
"""Vendor the canonical brainstorm skill playbook into Woof.

Woof is a standalone public repo: it must not depend on the agent-toolkit
checkout at runtime. The interactive `brainstorm` skill's prose (the two-loop
process and the artefact formats) is therefore vendored into
``playbooks/brainstorm/`` as a pinned, generated-marked copy. The schemas are
already woof-local (``schemas/brainstorm.schema.json``); only the prose is
vendored here.

The copy flows one way, agent-toolkit -> Woof. ``--check`` verifies the vendored
files match their recorded hashes (a CI-safe drift guard that needs no source
checkout); the default mode regenerates them from the source and rewrites the
``VENDOR.json`` pin.

Usage:
    vendor_brainstorm.py            # regenerate from the source skill
    vendor_brainstorm.py --check    # verify vendored files against VENDOR.json
    vendor_brainstorm.py --source <dir>   # override the source skill directory
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
VENDOR_DIR = REPO_ROOT / "playbooks" / "brainstorm"
MANIFEST = VENDOR_DIR / "VENDOR.json"

# The prose an agent needs to run the two loops and produce a bundle, plus the
# upstream attribution the format docs reference. Schemas and the Python
# validator are intentionally not vendored (Woof has its own).
VENDORED_FILES = (
    "SKILL.md",
    "TEMPLATE.md",
    "CONTEXT-FORMAT.md",
    "ADR-FORMAT.md",
    "ACKNOWLEDGEMENTS.md",
)

MARKER = (
    "<!-- VENDORED from agent-toolkit skills/brainstorm - do not edit here. "
    "Regenerate with `just vendor-brainstorm`. -->\n\n"
)


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


def vendor(source: Path) -> int:
    if not source.is_dir():
        sys.stderr.write(f"vendor-brainstorm: source not found: {source}\n")
        return 1
    VENDOR_DIR.mkdir(parents=True, exist_ok=True)
    files: dict[str, str] = {}
    for name in VENDORED_FILES:
        src = source / name
        if not src.is_file():
            sys.stderr.write(f"vendor-brainstorm: missing source file: {src}\n")
            return 1
        content = MARKER + src.read_text(encoding="utf-8")
        (VENDOR_DIR / name).write_text(content, encoding="utf-8")
        files[name] = _sha256(content)
    manifest = {
        "source": {
            "repo": "agent-toolkit",
            "path": "skills/brainstorm",
            "commit": _source_commit(source),
        },
        "marker": MARKER.strip(),
        "files": files,
    }
    MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(
        f"vendor-brainstorm: vendored {len(files)} files at {source} commit {manifest['source']['commit'][:12]}"
    )
    return 0


def check() -> list[str]:
    """Return a list of drift errors; empty means the vendored copy is intact."""
    errors: list[str] = []
    if not MANIFEST.is_file():
        return [f"missing manifest: {MANIFEST}"]
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    recorded = manifest.get("files", {})
    for name in VENDORED_FILES:
        path = VENDOR_DIR / name
        if not path.is_file():
            errors.append(f"missing vendored file: {name}")
            continue
        text = path.read_text(encoding="utf-8")
        if "VENDORED from agent-toolkit" not in text:
            errors.append(f"{name}: missing the VENDORED marker")
        if name not in recorded:
            errors.append(f"{name}: not in VENDOR.json")
        elif _sha256(text) != recorded[name]:
            errors.append(
                f"{name}: hash drift (file edited by hand? regenerate with `just vendor-brainstorm`)"
            )
    extra = set(recorded) - set(VENDORED_FILES)
    if extra:
        errors.append(f"VENDOR.json lists unexpected files: {', '.join(sorted(extra))}")
    return errors


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Vendor the brainstorm skill playbook into Woof.")
    parser.add_argument(
        "--check", action="store_true", help="verify vendored files against VENDOR.json"
    )
    parser.add_argument("--source", type=Path, default=None, help="source skill directory")
    args = parser.parse_args(argv)
    if args.check:
        errors = check()
        if errors:
            sys.stderr.write("vendor-brainstorm: drift detected:\n")
            for err in errors:
                sys.stderr.write(f"  - {err}\n")
            return 1
        print(f"vendor-brainstorm: {len(VENDORED_FILES)} vendored files intact.")
        return 0
    return vendor(args.source or default_source())


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
