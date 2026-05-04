"""woof — workflow orchestrator CLI.

Subcommands:
    wf           Run the deterministic orchestration graph.
    preflight    Validate local prerequisites for a Woof consumer checkout.
    hooks        Manage Woof-owned git hook blocks.
    validate     Validate artefacts against woof JSON Schemas via ajv-cli.
    dispatch     Spawn a cld/cod subprocess for a role declared in agents.toml.
    render-epic  Render EPIC.md front-matter into the gh issue body; optionally
                 sync to GitHub with conflict detection (.last-sync).
    check-cd     Verify each contract_decision's referenced artefact actually
                 exists and parses (Stage 5 Check 4 / E146 regression).

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
from datetime import UTC, datetime
from pathlib import Path

import yaml

from woof.checks.contract_refs import ContractRefUsageError, validate_contract_refs
from woof.cli.github import (
    GithubSyncError,
    render_epic_issue_body,
    split_epic_front_matter,
    sync_epic_definition,
)
from woof.paths import schema_dir

SCHEMA_DIR = schema_dir()

SCHEMAS: dict[str, str] = {
    "epic": "epic.schema.json",
    "plan": "plan.schema.json",
    "gate": "gate.schema.json",
    "critique": "critique.schema.json",
    "jsonl-events": "jsonl-events.schema.json",
    "prerequisites": "prerequisites.schema.json",
    "agents": "agents.schema.json",
    "test-markers": "test-markers.schema.json",
    "language-registry": "language-registry.schema.json",
    "quality-gates": "quality-gates.schema.json",
    "docs-paths": "docs-paths.schema.json",
    "check-result": "check-result.schema.json",
    "executor-result": "executor-result.schema.json",
    "node-input": "node-input.schema.json",
    "node-output": "node-output.schema.json",
    "transaction-manifest": "transaction-manifest.schema.json",
}

# Filename → schema (basename match)
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
    (re.compile(r"^node-input\.json$"), "node-input"),
    (re.compile(r"^node-output\.json$"), "node-output"),
    (re.compile(r"^transaction-manifest\.json$"), "transaction-manifest"),
]

# Path-suffix → schema (for files distinguished by parent dir)
PATH_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(^|/)critique/[^/]+\.md$"), "critique"),
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
    if schema in {"epic", "gate", "critique"}:
        return extract_front_matter(path)
    if schema in {
        "plan",
        "check-result",
        "executor-result",
        "node-input",
        "node-output",
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
# dispatch
# ---------------------------------------------------------------------------

HARNESS_BY_TARGET = {"claude": "cld", "codex": "cod"}
STORY_ID_RE = re.compile(r"^S[1-9]\d*$")


def find_woof_root(start: Path) -> Path:
    """Walk up from ``start`` to the first directory containing ``.woof/``."""
    for candidate in (start, *start.parents):
        if (candidate / ".woof").is_dir():
            return candidate
    sys.stderr.write(f"woof: no .woof/ directory found at or above {start}; not a woof project\n")
    sys.exit(2)


def iso_utc(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def append_jsonl(path: Path, event: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, separators=(",", ":")) + "\n")


def parse_claude_output(stdout: str) -> tuple[dict, str | None]:
    """Extract token usage and session_id from ``claude -p --output-format json``."""
    lines = [ln for ln in stdout.strip().splitlines() if ln.strip()]
    if not lines:
        return {}, None
    try:
        data = json.loads(lines[-1])
    except json.JSONDecodeError:
        return {}, None
    usage = data.get("usage") or {}
    tokens = {
        "tokens_in": int(usage.get("input_tokens", 0) or 0),
        "tokens_out": int(usage.get("output_tokens", 0) or 0),
        "cache_read_tokens": int(usage.get("cache_read_input_tokens", 0) or 0),
        "cache_write_tokens": int(usage.get("cache_creation_input_tokens", 0) or 0),
    }
    return tokens, data.get("session_id")


def parse_codex_output(stdout: str) -> tuple[dict, str | None]:
    """Sum token usage across ``turn.completed`` events from ``codex exec --json``."""
    tokens_in = tokens_out = cache_read = 0
    saw_turn = False
    thread_id: str | None = None
    for raw in stdout.splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            evt = json.loads(line)
        except json.JSONDecodeError:
            continue
        kind = evt.get("type")
        if kind == "thread.started":
            thread_id = evt.get("thread_id")
        elif kind == "turn.completed":
            saw_turn = True
            usage = evt.get("usage") or {}
            tokens_in += int(usage.get("input_tokens", 0) or 0)
            tokens_out += int(usage.get("output_tokens", 0) or 0)
            tokens_out += int(usage.get("reasoning_output_tokens", 0) or 0)
            cache_read += int(usage.get("cached_input_tokens", 0) or 0)
    if not saw_turn:
        return {}, thread_id
    return {
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "cache_read_tokens": cache_read,
    }, thread_id


def build_argv(harness: str, role: dict, prompt: str) -> list[str]:
    """Construct the cld/cod invocation argv from a role definition."""
    argv: list[str] = [harness]
    mcp = role.get("mcp") or []
    flags = role.get("flags") or []
    model = role.get("model")

    if harness == "cld":
        if role.get("think"):
            argv.append("-t")
        if mcp:
            argv += ["-m", ",".join(mcp)]
        argv += ["--", "-p", "--output-format", "json"]
        if model:
            argv += ["--model", model]
        argv += flags
    else:  # cod
        if mcp:
            argv += ["-m", ",".join(mcp)]
        argv += ["--", "exec", "--json", "--skip-git-repo-check"]
        if model:
            argv += ["--model", model]
        argv += flags
    argv.append(prompt)
    return argv


def cmd_dispatch(args: argparse.Namespace) -> int:
    ensure_ajv()

    expected_harness = HARNESS_BY_TARGET[args.target]
    repo_root = find_woof_root(Path.cwd().resolve())
    agents_path = repo_root / ".woof" / "agents.toml"
    if not agents_path.is_file():
        sys.stderr.write(f"woof: {agents_path} not found; cannot dispatch\n")
        return 2

    with agents_path.open("rb") as fh:
        agents = tomllib.load(fh)

    schema_path = SCHEMA_DIR / SCHEMAS["agents"]
    ok, output = run_ajv(schema_path, json.dumps(agents).encode())
    if not ok:
        sys.stderr.write(f"woof: {agents_path}: schema invalid\n{output}\n")
        return 2

    role = (agents.get("roles") or {}).get(args.role)
    if role is None:
        sys.stderr.write(f"woof: role '{args.role}' not declared in {agents_path}\n")
        return 2

    if role["harness"] == "in-session":
        sys.stderr.write(f"woof: role '{args.role}' is in-session; cannot be dispatched\n")
        return 2
    if role["harness"] != expected_harness:
        sys.stderr.write(
            f"woof: role '{args.role}' has harness={role['harness']!r}; "
            f"expected {expected_harness!r} for 'woof dispatch {args.target}'\n"
        )
        return 2

    if args.story is not None and not STORY_ID_RE.match(args.story):
        sys.stderr.write(f"woof: --story {args.story!r}: must match S<n> (n>=1)\n")
        return 2

    prompt = Path(args.prompt_file).read_text() if args.prompt_file else sys.stdin.read()
    if not prompt.strip():
        sys.stderr.write("woof: empty prompt\n")
        return 2

    timeout_min = int((agents.get("timeouts") or {}).get("default_minutes", 30))
    argv = build_argv(expected_harness, role, prompt)

    if args.dry_run:
        wrapped = ["timeout", f"{timeout_min}m", *argv]
        print(
            json.dumps(
                {
                    "argv": wrapped,
                    "epic": args.epic,
                    "story": args.story,
                    "role": args.role,
                    "harness": expected_harness,
                    "model": role.get("model"),
                    "mcp": role.get("mcp") or [],
                    "flags": role.get("flags") or [],
                    "timeout_min": timeout_min,
                }
            )
        )
        return 0

    epic_dir = repo_root / ".woof" / "epics" / f"E{args.epic}"
    audit_dir = epic_dir / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    started_at = datetime.now(UTC)
    ts = started_at.strftime("%Y%m%dT%H%M%SZ")
    base = audit_dir / f"{expected_harness}-{args.role}-{ts}"
    prompt_file = base.with_suffix(".prompt")
    output_file = base.with_suffix(".output")
    stderr_file = base.with_suffix(".stderr")
    meta_file = base.with_suffix(".meta")
    prompt_file.write_text(prompt)

    dispatch_jsonl = epic_dir / "dispatch.jsonl"
    wrapped_argv = ["timeout", f"{timeout_min}m", *argv]

    proc = subprocess.Popen(wrapped_argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    spawned_event: dict = {
        "event": "subprocess_spawned",
        "at": iso_utc(started_at),
        "epic_id": args.epic,
        "role": args.role,
        "harness": expected_harness,
        "pid": proc.pid,
    }
    if args.story:
        spawned_event["story_id"] = args.story
    if role.get("model"):
        spawned_event["model"] = role["model"]
    append_jsonl(dispatch_jsonl, spawned_event)

    try:
        stdout, stderr = proc.communicate()
    except KeyboardInterrupt:
        proc.terminate()
        stdout, stderr = proc.communicate()
        ended_at = datetime.now(UTC)
        append_jsonl(
            dispatch_jsonl,
            {
                "event": "subprocess_killed",
                "at": iso_utc(ended_at),
                "epic_id": args.epic,
                "pid": proc.pid,
                "signal": "SIGINT",
                "reason": "manual_cancel",
            },
        )
        output_file.write_text(stdout or "")
        stderr_file.write_text(stderr or "")
        return 130

    ended_at = datetime.now(UTC)
    duration_ms = int((ended_at - started_at).total_seconds() * 1000)
    output_file.write_text(stdout)
    stderr_file.write_text(stderr)

    if expected_harness == "cld":
        tokens, session_id = parse_claude_output(stdout)
        thread_id = None
    else:
        tokens, thread_id = parse_codex_output(stdout)
        session_id = None

    timed_out = proc.returncode == 124  # GNU timeout(1) exit code
    if timed_out:
        append_jsonl(
            dispatch_jsonl,
            {
                "event": "subprocess_killed",
                "at": iso_utc(ended_at),
                "epic_id": args.epic,
                "pid": proc.pid,
                "signal": "SIGTERM",
                "reason": "timeout",
            },
        )

    returned: dict = {
        "event": "subprocess_returned",
        "at": iso_utc(ended_at),
        "epic_id": args.epic,
        "role": args.role,
        "harness": expected_harness,
        "pid": proc.pid,
        "exit_code": proc.returncode,
        "duration_ms": duration_ms,
    }
    if args.story:
        returned["story_id"] = args.story
    if role.get("model"):
        returned["model"] = role["model"]
    if tokens:
        returned.update(tokens)
    if session_id:
        returned["cc_session_id"] = session_id
    if expected_harness == "cod":
        returned["codex_audit_path"] = str(base.relative_to(repo_root))
    append_jsonl(dispatch_jsonl, returned)

    meta = {
        "harness": expected_harness,
        "role": args.role,
        "epic_id": args.epic,
        "story_id": args.story,
        "model": role.get("model"),
        "mcp": role.get("mcp") or [],
        "flags": role.get("flags") or [],
        "argv": wrapped_argv,
        "pid": proc.pid,
        "started_at": iso_utc(started_at),
        "ended_at": iso_utc(ended_at),
        "duration_ms": duration_ms,
        "exit_code": proc.returncode,
        "timed_out": timed_out,
        "tokens": tokens,
    }
    if session_id:
        meta["cc_session_id"] = session_id
    if thread_id:
        meta["codex_thread_id"] = thread_id
    meta_file.write_text(json.dumps(meta, indent=2) + "\n")

    return proc.returncode


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
        result = sync_epic_definition(repo_root, args.epic, front, prose)
    except GithubSyncError as exc:
        message = str(exc)
        sys.stderr.write(f"woof: {message}\n")
        return 3 if "github_sync_conflict" in message else 2

    body = result.body
    if args.output:
        Path(args.output).write_text(body)
    else:
        sys.stdout.write(body)
    return 0


# ---------------------------------------------------------------------------
# check-cd — Stage 5 Check 4 (contract-decision artefact verification)
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
                print(f"         → {finding.detail}")
        print(f"{result.verified}/{result.total} verified")

    return 0 if result.verified == result.total else 1


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(prog="woof", description="workflow orchestrator CLI")
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
        help="spawn a cld/cod subprocess for a role declared in agents.toml",
    )
    dispatch.add_argument("target", choices=sorted(HARNESS_BY_TARGET))
    dispatch.add_argument("--role", required=True, help="role name from .woof/agents.toml")
    dispatch.add_argument("--epic", type=int, required=True, help="epic id (gh issue number)")
    dispatch.add_argument("--story", help="story id (e.g. S1); optional")
    dispatch.add_argument(
        "--prompt-file",
        help="path to a file holding the prompt; if omitted, prompt is read from stdin",
    )
    dispatch.add_argument(
        "--dry-run",
        action="store_true",
        help="print the resolved invocation as JSON and exit without spawning",
    )
    dispatch.set_defaults(func=cmd_dispatch)

    render = sub.add_parser(
        "render-epic",
        help="render EPIC.md front-matter into the gh issue body",
    )
    render.add_argument("--epic", type=int, required=True, help="epic id (gh issue number)")
    render.add_argument("--output", help="write rendered body to PATH instead of stdout")
    render.add_argument(
        "--sync",
        action="store_true",
        help="fetch remote, conflict-check against .last-sync, push if clean, update .last-sync",
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
    from woof.cli.commands.wf import setup_wf_parser
    from woof.cli.hooks import setup_hooks_parser
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
    setup_wf_parser(sub)
    setup_check_parser(sub)
    setup_gate_parser(sub)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
