"""woof - agentic software delivery graph CLI.

Subcommands:
    wf           Run the deterministic orchestration graph.
    observe      Inspect read-only workflow status, timeline, gate, and audit views.
    preflight    Validate local prerequisites for a Woof consumer checkout.
    init         Scaffold a fresh .woof/ consumer config and .gitignore block.
    hooks        Manage Woof-owned git hook blocks.
    validate     Validate artefacts against woof JSON Schemas via ajv-cli.
    dispatch     Spawn a public CLI subprocess for a role declared in agents.toml.
    audit-bundle Copy referenced Claude transcripts into an epic audit folder.
    render-epic  Render EPIC.md front-matter into the managed tracker body;
                 optionally sync to the tracker with conflict detection.
    check-cd     Verify each contract_decision's referenced artefact exists
                 and resolves under its native tooling (Stage 5 Check 4).

Schemas live at ``schemas/*.schema.json`` (JSON Schema 2020-12).
Auto-detection maps filename to schema; ``--schema`` overrides.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

import yaml

from woof.checks.contract_refs import ContractRefUsageError, validate_contract_refs
from woof.cli.dispatcher import ADAPTERS, cmd_dispatch, find_woof_root
from woof.lib.audit_bundle import (
    AuditBundleError,
    NonPortableTranscriptError,
    bundle_claude_transcripts,
)
from woof.paths import schema_dir
from woof.trackers import TrackerError, resolve_tracker
from woof.trackers.epic_body import render_epic_issue_body, split_epic_front_matter

SCHEMA_DIR = schema_dir()

SCHEMAS: dict[str, str] = {
    "epic": "epic.schema.json",
    "brainstorm": "brainstorm.schema.json",
    "plan": "plan.schema.json",
    "gate": "gate.schema.json",
    "critique": "critique.schema.json",
    "disposition": "disposition.schema.json",
    "jsonl-events": "jsonl-events.schema.json",
    "prerequisites": "prerequisites.schema.json",
    "agents": "agents.schema.json",
    "test-markers": "test-markers.schema.json",
    "language-registry": "language-registry.schema.json",
    "freshness": "freshness.schema.json",
    "quality-gates": "quality-gates.schema.json",
    "docs-paths": "docs-paths.schema.json",
    "check-result": "check-result.schema.json",
    "executor-result": "executor-result.schema.json",
    "readiness-result": "readiness-result.schema.json",
    "node-input": "node-input.schema.json",
    "node-output": "node-output.schema.json",
    "planning-node-input": "planning-node-input.schema.json",
    "planning-node-output": "planning-node-output.schema.json",
    "transaction-manifest": "transaction-manifest.schema.json",
}

# Filename -> schema (basename match)
FILENAME_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^EPIC\.md$"), "epic"),
    (re.compile(r"^plan\.json$"), "plan"),
    (re.compile(r"^gate\.md$"), "gate"),
    (re.compile(r"^.+\.jsonl$"), "jsonl-events"),
    (re.compile(r"^prerequisites\.toml$"), "prerequisites"),
    (re.compile(r"^agents\.toml$"), "agents"),
    (re.compile(r"^test-markers\.toml$"), "test-markers"),
    (re.compile(r"^quality-gates\.toml$"), "quality-gates"),
    (re.compile(r"^docs-paths\.toml$"), "docs-paths"),
    (re.compile(r"^check-result\.json$"), "check-result"),
    (re.compile(r"^executor-result\.json$"), "executor-result"),
    (re.compile(r"^readiness-result\.json$"), "readiness-result"),
    (re.compile(r"^freshness\.json$"), "freshness"),
    (re.compile(r"^node-input\.json$"), "node-input"),
    (re.compile(r"^node-output\.json$"), "node-output"),
    (re.compile(r"^planning-node-input\.json$"), "planning-node-input"),
    (re.compile(r"^planning-node-output\.json$"), "planning-node-output"),
    (re.compile(r"^transaction-manifest\.json$"), "transaction-manifest"),
]

# Path-suffix -> schema (for files distinguished by parent dir)
PATH_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(^|/)critique/[^/]+\.md$"), "critique"),
    (re.compile(r"(^|/)dispositions/[^/]+\.md$"), "disposition"),
    (re.compile(r"(^|/)languages/[^/]+\.toml$"), "language-registry"),
]


def detect_schema(path: Path) -> str | None:
    """Return schema name for ``path`` or None if no rule matches."""
    name = path.name
    for pattern, schema in FILENAME_RULES:
        if pattern.match(name):
            return schema
    posix = path.as_posix()
    for pattern, schema in PATH_RULES:
        if pattern.search(posix):
            return schema
    return None


def extract_front_matter(path: Path) -> object:
    """Parse YAML front-matter (delimited by ``---`` on its own line)."""
    text = path.read_text()
    if not text.startswith("---\n"):
        raise ValueError(f"{path}: no YAML front-matter (file must start with '---\\n')")
    end = text.find("\n---\n", 4)
    if end < 0:
        end_alt = text.find("\n---", 4)
        if end_alt < 0 or text[end_alt:].rstrip() != "---":
            raise ValueError(f"{path}: unterminated YAML front-matter")
        end = end_alt
    block = text[4:end]
    return yaml.safe_load(block) or {}


def load_payload(path: Path, schema: str) -> object:
    """Extract the structured payload appropriate for ``schema``."""
    if schema in {"epic", "brainstorm", "gate", "critique", "disposition"}:
        return extract_front_matter(path)
    if schema in {
        "plan",
        "check-result",
        "executor-result",
        "readiness-result",
        "freshness",
        "node-input",
        "node-output",
        "planning-node-input",
        "planning-node-output",
        "transaction-manifest",
    }:
        return json.loads(path.read_text())
    if schema in {
        "prerequisites",
        "agents",
        "test-markers",
        "language-registry",
        "quality-gates",
        "docs-paths",
    }:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    raise ValueError(f"load_payload: unhandled schema '{schema}'")


def run_ajv(schema_path: Path, data_json: bytes) -> tuple[bool, str]:
    """Run ajv-cli; return (ok, combined-output)."""
    with tempfile.NamedTemporaryFile("wb", suffix=".json", delete=False) as fh:
        fh.write(data_json)
        data_path = fh.name
    try:
        proc = subprocess.run(
            [
                "ajv",
                "validate",
                "--spec=draft2020",
                "-c",
                "ajv-formats",
                "-s",
                str(schema_path),
                "-d",
                data_path,
            ],
            capture_output=True,
            text=True,
        )
    finally:
        Path(data_path).unlink(missing_ok=True)
    output = (proc.stdout + proc.stderr).strip()
    return proc.returncode == 0, output


def ensure_ajv() -> None:
    """Exit non-zero if ajv-cli is not on PATH."""
    if shutil.which("ajv") is None:
        sys.stderr.write(
            "woof: ajv-cli not found on PATH.\nInstall: volta install ajv-cli ajv-formats\n"
        )
        sys.exit(2)


def validate_jsonl(path: Path, schema_path: Path) -> tuple[bool, list[str]]:
    """Validate every non-blank line of a JSONL file."""
    messages: list[str] = []
    all_ok = True
    line_count = 0
    for lineno, raw in enumerate(path.read_text().splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        line_count += 1
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            all_ok = False
            messages.append(f"{path}:{lineno}: invalid JSON: {exc}")
            continue
        ok, output = run_ajv(schema_path, json.dumps(payload, default=str).encode())
        if not ok:
            all_ok = False
            messages.append(f"{path}:{lineno}: INVALID\n{output}")
    if all_ok:
        messages.append(f"{path}: valid (jsonl-events, {line_count} line(s))")
    return all_ok, messages


def validate_path(path: Path, schema_override: str | None) -> tuple[bool, list[str]]:
    """Validate one file. Returns ``(ok, messages)``."""
    schema = schema_override or detect_schema(path)
    if schema is None:
        return False, [f"{path}: no schema rule matches; pass --schema explicitly"]
    if schema not in SCHEMAS:
        return False, [f"{path}: unknown schema '{schema}'"]
    schema_path = SCHEMA_DIR / SCHEMAS[schema]

    if schema == "jsonl-events":
        return validate_jsonl(path, schema_path)

    try:
        payload = load_payload(path, schema)
    except (ValueError, json.JSONDecodeError, yaml.YAMLError, tomllib.TOMLDecodeError) as exc:
        return False, [f"{path}: parse error: {exc}"]

    ok, output = run_ajv(schema_path, json.dumps(payload, default=str).encode())
    msg = f"{path}: {'valid' if ok else 'INVALID'} ({schema})"
    if not ok:
        msg += "\n" + output
    return ok, [msg]


def cmd_validate(args: argparse.Namespace) -> int:
    ensure_ajv()
    overall_ok = True
    for raw in args.paths:
        path = Path(raw)
        if not path.is_file():
            sys.stderr.write(f"woof: {raw}: not a file\n")
            overall_ok = False
            continue
        ok, messages = validate_path(path, args.schema)
        for line in messages:
            print(line)
        if not ok:
            overall_ok = False
    return 0 if overall_ok else 1


# ---------------------------------------------------------------------------
# audit-bundle
# ---------------------------------------------------------------------------


def cmd_audit_bundle(args: argparse.Namespace) -> int:
    repo_root = find_woof_root(Path.cwd().resolve())
    try:
        result = bundle_claude_transcripts(repo_root, args.epic)
    except NonPortableTranscriptError as exc:
        sys.stderr.write(f"woof: {exc}\n")
        return 2
    except AuditBundleError as exc:
        sys.stderr.write(f"woof: {exc}\n")
        return 2

    destination = _display_path(repo_root, result.destination_dir)
    if result.copied:
        print(f"{result.epic}: copied {len(result.copied)} Claude transcript(s) into {destination}")
        for item in result.copied:
            print(f"  copied {item.reference} -> {_display_path(repo_root, item.destination)}")
    else:
        print(f"{result.epic}: copied 0 Claude transcript(s) into {destination}")

    if result.missing:
        sys.stderr.write(f"{result.epic}: missing {len(result.missing)} Claude transcript(s)\n")
        for item in result.missing:
            sys.stderr.write(f"  missing {item.reference}\n")
        return 1

    return 0


def _display_path(repo_root: Path, path: Path) -> str:
    try:
        return path.relative_to(repo_root).as_posix()
    except ValueError:
        return path.as_posix()


# ---------------------------------------------------------------------------
# render-epic
# ---------------------------------------------------------------------------


def cmd_render_epic(args: argparse.Namespace) -> int:
    ensure_ajv()
    repo_root = find_woof_root(Path.cwd().resolve())
    epic_dir = repo_root / ".woof" / "epics" / f"E{args.epic}"
    epic_md = epic_dir / "EPIC.md"
    if not epic_md.is_file():
        sys.stderr.write(f"woof: {epic_md} not found\n")
        return 2

    try:
        front, prose = split_epic_front_matter(epic_md)
    except (ValueError, yaml.YAMLError) as exc:
        sys.stderr.write(f"woof: {epic_md}: {exc}\n")
        return 2

    schema_path = SCHEMA_DIR / SCHEMAS["epic"]
    ok, output = run_ajv(schema_path, json.dumps(front).encode())
    if not ok:
        sys.stderr.write(f"woof: {epic_md}: front-matter invalid\n{output}\n")
        return 2

    if not args.sync:
        body = render_epic_issue_body(front, prose, remote_body=None)
        if args.output:
            Path(args.output).write_text(body)
        else:
            sys.stdout.write(body)
        return 0

    try:
        tracker = resolve_tracker(repo_root)
        result = tracker.push_epic_definition(args.epic, front, prose)
    except TrackerError as exc:
        message = str(exc)
        sys.stderr.write(f"woof: {message}\n")
        return 3 if "tracker_sync_conflict" in message else 2

    body = result.body
    if args.output:
        Path(args.output).write_text(body)
    else:
        sys.stdout.write(body)
    return 0


# ---------------------------------------------------------------------------
# check-cd - Stage 5 Check 4 (contract-decision artefact verification)
# ---------------------------------------------------------------------------


def cmd_check_cd(args: argparse.Namespace) -> int:
    ensure_ajv()
    epic_md = Path(args.epic_md).resolve()
    try:
        result = validate_contract_refs(epic_md)
    except ContractRefUsageError as exc:
        sys.stderr.write(f"woof: {exc}\n")
        return 2

    if args.format == "json":
        print(
            json.dumps(
                {
                    "epic_md": str(result.epic_md),
                    "total": result.total,
                    "verified": result.verified,
                    "findings": [
                        {
                            "id": finding.id,
                            "kind": finding.kind,
                            "ref": finding.ref,
                            "ok": finding.ok,
                            "detail": finding.detail,
                            "source_path": finding.source_path,
                        }
                        for finding in result.findings
                    ],
                }
            )
        )
    else:
        repo_root = result.epic_md.parent
        while repo_root != repo_root.parent and not (repo_root / ".git").exists():
            repo_root = repo_root.parent
        display_path = (
            result.epic_md.relative_to(repo_root)
            if (repo_root / ".git").exists() and result.epic_md.is_relative_to(repo_root)
            else result.epic_md
        )
        print(f"{display_path}: {result.total} contract decision(s)")
        for finding in result.findings:
            status = "OK  " if finding.ok else "FAIL"
            print(f"  {status} {finding.id:<6} ({finding.kind}) {finding.ref}")
            if not finding.ok or args.verbose:
                print(f"         -> {finding.detail}")
        print(f"{result.verified}/{result.total} verified")

    return 0 if result.verified == result.total else 1


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="woof",
        description="agentic software delivery graph CLI",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    validate = sub.add_parser("validate", help="validate woof artefacts against schemas")
    validate.add_argument("paths", nargs="+", help="paths to validate")
    validate.add_argument(
        "--schema",
        choices=sorted(SCHEMAS),
        help="override schema auto-detection",
    )
    validate.set_defaults(func=cmd_validate)

    dispatch = sub.add_parser(
        "dispatch",
        help="spawn a public CLI subprocess for a role declared in agents.toml",
    )
    dispatch.add_argument(
        "target",
        nargs="?",
        choices=sorted(ADAPTERS),
        help="deprecated adapter target; role routes now resolve this from .woof/agents.toml",
    )
    dispatch.add_argument("--role", required=True, help="role name from .woof/agents.toml")
    dispatch.add_argument(
        "--epic",
        type=int,
        required=True,
        help="tracker-assigned epic id",
    )
    dispatch.add_argument("--story", help="story id (e.g. S1); optional")
    dispatch.add_argument(
        "--route-key",
        help=(
            "node group selecting the dispatch route overlay "
            "(discovery, definition, planning, execution); optional"
        ),
    )
    dispatch.add_argument(
        "--prompt-file",
        help="path to a file holding the prompt; if omitted, prompt is read from stdin",
    )
    dispatch.add_argument(
        "--artefact",
        "--artefact-loaded",
        dest="artefacts_loaded",
        action="append",
        default=[],
        help="repo-relative file path explicitly referenced by the prompt; repeatable",
    )
    dispatch.add_argument(
        "--dry-run",
        action="store_true",
        help="print the resolved invocation as JSON and exit without spawning",
    )
    dispatch.set_defaults(func=cmd_dispatch)

    audit_bundle = sub.add_parser(
        "audit-bundle",
        help="copy referenced Claude transcripts into an epic audit folder",
    )
    audit_bundle.add_argument("epic", help="epic reference, e.g. E17 or 17")
    audit_bundle.set_defaults(func=cmd_audit_bundle)

    render = sub.add_parser(
        "render-epic",
        help="render EPIC.md front-matter into the managed tracker body",
        description="render EPIC.md front-matter into the managed tracker body",
    )
    render.add_argument("--epic", type=int, required=True, help="tracker-assigned epic id")
    render.add_argument("--output", help="write rendered body to PATH instead of stdout")
    render.add_argument(
        "--sync",
        action="store_true",
        help=(
            "push through the configured tracker; hosted trackers conflict-check against .last-sync"
        ),
    )
    render.set_defaults(func=cmd_render_epic)

    check_cd = sub.add_parser(
        "check-cd",
        help="verify each contract_decision's referenced artefact (Stage 5 Check 4)",
    )
    check_cd.add_argument("epic_md", help="path to EPIC.md")
    check_cd.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="output format (default: text)",
    )
    check_cd.add_argument(
        "--verbose",
        action="store_true",
        help="show OK detail lines",
    )
    check_cd.set_defaults(func=cmd_check_cd)

    from woof.cli.commands.check import setup_check_parser
    from woof.cli.commands.gate import setup_gate_parser
    from woof.cli.commands.observe import setup_observe_parser
    from woof.cli.commands.wf import setup_wf_parser
    from woof.cli.hooks import setup_hooks_parser
    from woof.cli.init import setup_init_parser
    from woof.cli.preflight import cmd_preflight

    preflight = sub.add_parser(
        "preflight",
        help="validate local prerequisites for a woof project",
    )
    preflight.add_argument(
        "--project-root",
        help="woof project root to check; defaults to the nearest ancestor containing .woof/",
    )
    preflight.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="output format (default: text)",
    )
    preflight.add_argument(
        "--force",
        action="store_true",
        help="refresh cached prerequisite and runtime checks",
    )
    preflight.set_defaults(func=cmd_preflight)

    setup_hooks_parser(sub)
    setup_init_parser(sub)
    setup_observe_parser(sub)
    setup_wf_parser(sub)
    setup_check_parser(sub)
    setup_gate_parser(sub)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
