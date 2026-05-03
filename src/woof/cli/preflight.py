"""Uncached preflight checks for Woof consumer projects."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from woof.cli.main import SCHEMAS, load_payload, run_ajv
from woof.paths import schema_dir, tool_root

CONFIG_SCHEMAS = {
    "prerequisites.toml": "prerequisites",
    "agents.toml": "agents",
    "quality-gates.toml": "quality-gates",
    "test-markers.toml": "test-markers",
    "docs-paths.toml": "docs-paths",
}

PREREQUISITES_TEMPLATE = """\
[infra]
just = "1.0+"
git = "2.30+"
gh = "2.0+"

[wrappers]
cld = "any"
cod = "any"
agent-sync = "any"

[validators]
ajv = "any"
ajv-formats = "any"

[github]
repo = "<owner>/<repo>"
"""


@dataclass(frozen=True)
class PreflightFinding:
    id: str
    label: str
    ok: bool
    detail: str
    required: str | None = None
    install: str | None = None
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.id,
            "label": self.label,
            "ok": self.ok,
            "detail": self.detail,
        }
        if self.required is not None:
            payload["required"] = self.required
        if self.install is not None:
            payload["install"] = self.install
        if self.notes:
            payload["notes"] = self.notes
        return payload


@dataclass(frozen=True)
class PreflightResult:
    repo_root: Path
    findings: list[PreflightFinding]

    @property
    def ok(self) -> bool:
        return all(finding.ok for finding in self.findings)

    @property
    def failed(self) -> list[PreflightFinding]:
        return [finding for finding in self.findings if not finding.ok]

    def as_dict(self) -> dict[str, Any]:
        return {
            "repo_root": str(self.repo_root),
            "ok": self.ok,
            "total": len(self.findings),
            "failed": len(self.failed),
            "findings": [finding.as_dict() for finding in self.findings],
        }


def cmd_preflight(args: argparse.Namespace) -> int:
    repo_root = _resolve_repo_root(args.project_root)
    result = run_preflight(repo_root)
    if args.format == "json":
        print(json.dumps(result.as_dict(), indent=2))
    else:
        _print_text_result(result)
    return 0 if result.ok else 1


def run_preflight(repo_root: Path) -> PreflightResult:
    prereq_path = repo_root / ".woof" / "prerequisites.toml"
    if not prereq_path.is_file():
        return PreflightResult(
            repo_root=repo_root,
            findings=[
                PreflightFinding(
                    id="config.prerequisites",
                    label="prerequisites.toml",
                    ok=False,
                    detail=f"{prereq_path} not found",
                    install=f"Create {prereq_path} from this template:\n{PREREQUISITES_TEMPLATE}",
                )
            ],
        )

    findings: list[PreflightFinding] = []
    prereq = _load_toml(prereq_path)
    if isinstance(prereq, dict):
        findings.extend(_check_config_schemas(repo_root))
        findings.extend(_check_declared_binaries(prereq))
        findings.extend(_check_ajv_formats(prereq))
        findings.extend(_check_github(prereq))
        findings.extend(_check_language_tools(prereq))
        findings.extend(_check_tree_sitter(prereq))
        findings.extend(_check_quality_gate_commands(repo_root))
    else:
        findings.append(
            PreflightFinding(
                id="config.prerequisites",
                label="prerequisites.toml",
                ok=False,
                detail=prereq,
            )
        )
    return PreflightResult(repo_root=repo_root, findings=findings)


def _resolve_repo_root(project_root: str | None) -> Path:
    if project_root:
        root = Path(project_root).resolve()
        if not (root / ".woof").is_dir():
            sys.stderr.write(f"woof: {root}/.woof not found; not a woof project\n")
            sys.exit(2)
        return root

    current = Path.cwd().resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".woof").is_dir():
            return candidate
    sys.stderr.write(f"woof: no .woof/ directory found at or above {current}; not a woof project\n")
    sys.exit(2)


def _load_toml(path: Path) -> dict[str, Any] | str:
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        return f"{path}: TOML parse error: {exc}"


def _check_config_schemas(repo_root: Path) -> list[PreflightFinding]:
    if shutil.which("ajv") is None:
        return [
            PreflightFinding(
                id="config.schemas",
                label="consumer config schemas",
                ok=False,
                detail="ajv-cli not found; cannot validate .woof/*.toml schemas",
                install="volta install ajv-cli ajv-formats",
            )
        ]

    findings: list[PreflightFinding] = []
    for filename, schema in CONFIG_SCHEMAS.items():
        path = repo_root / ".woof" / filename
        if filename != "prerequisites.toml" and not path.is_file():
            continue
        if not path.is_file():
            findings.append(
                PreflightFinding(
                    id=f"config.{schema}",
                    label=filename,
                    ok=False,
                    detail=f"{path} not found",
                )
            )
            continue
        try:
            payload = load_payload(path, schema)
        except (ValueError, tomllib.TOMLDecodeError) as exc:
            findings.append(
                PreflightFinding(
                    id=f"config.{schema}",
                    label=filename,
                    ok=False,
                    detail=f"parse error: {exc}",
                )
            )
            continue
        ok, output = run_ajv(schema_dir() / SCHEMAS[schema], json.dumps(payload).encode())
        findings.append(
            PreflightFinding(
                id=f"config.{schema}",
                label=filename,
                ok=ok,
                detail="schema valid" if ok else output,
            )
        )
    return findings


def _check_declared_binaries(prereq: dict[str, Any]) -> list[PreflightFinding]:
    findings: list[PreflightFinding] = []
    for section in ("infra", "wrappers", "validators"):
        for binary, version_spec in (prereq.get(section) or {}).items():
            if binary == "ajv-formats":
                continue
            findings.append(_check_binary(section, binary, str(version_spec)))

    indexing = prereq.get("indexing") or {}
    for binary, version_spec in indexing.items():
        if binary == "tree-sitter":
            continue
        findings.append(_check_binary("indexing", binary, str(version_spec)))

    tree_sitter = indexing.get("tree-sitter") or {}
    if tree_sitter:
        findings.append(_check_binary("tree-sitter", "tree-sitter", str(tree_sitter["cli"])))
    return findings


def _check_binary(section: str, binary: str, version_spec: str) -> PreflightFinding:
    path = shutil.which(binary)
    label = f"{binary} ({section})"
    if path is None:
        return PreflightFinding(
            id=f"{section}.{binary}",
            label=label,
            ok=False,
            detail=f"{binary} not found on PATH",
            required=version_spec,
        )
    if version_spec == "any":
        return PreflightFinding(
            id=f"{section}.{binary}",
            label=label,
            ok=True,
            detail=f"found at {path}",
            required=version_spec,
        )

    ok, found = _version_meets_floor(binary, version_spec)
    return PreflightFinding(
        id=f"{section}.{binary}",
        label=label,
        ok=ok,
        detail=f"version {found} meets floor" if ok else f"version {found} below required floor",
        required=version_spec,
    )


def _version_meets_floor(binary: str, version_spec: str) -> tuple[bool, str]:
    returncode, output = _run_capture([binary, "--version"], timeout=10)
    if returncode != 0:
        return False, f"unknown ({output})"
    match = re.search(r"\d+(?:\.\d+){0,2}", output)
    if not match:
        return False, f"unknown ({output})"
    found = match.group(0)
    return _version_tuple(found) >= _version_tuple(version_spec.rstrip("+")), found


def _version_tuple(version: str) -> tuple[int, int, int]:
    parts = [int(part) for part in version.split(".")]
    return tuple([*parts, 0, 0, 0][:3])


def _check_ajv_formats(prereq: dict[str, Any]) -> list[PreflightFinding]:
    if "ajv-formats" not in (prereq.get("validators") or {}):
        return []
    if shutil.which("ajv") is None:
        return [
            PreflightFinding(
                id="validators.ajv-formats",
                label="ajv-formats",
                ok=False,
                detail="ajv-cli not found, so ajv-formats cannot be loaded",
                install="volta install ajv-cli ajv-formats",
            )
        ]

    schema = '{"type":"string","format":"date-time"}\n'
    data = '"2026-05-03T00:00:00Z"\n'
    with tempfile.NamedTemporaryFile("w", suffix=".schema.json", delete=False) as schema_fh:
        schema_fh.write(schema)
        schema_path = Path(schema_fh.name)
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as data_fh:
        data_fh.write(data)
        data_path = Path(data_fh.name)
    try:
        returncode, output = _run_capture(
            [
                "ajv",
                "validate",
                "--spec=draft2020",
                "-c",
                "ajv-formats",
                "-s",
                str(schema_path),
                "-d",
                str(data_path),
            ],
            timeout=20,
        )
    finally:
        schema_path.unlink(missing_ok=True)
        data_path.unlink(missing_ok=True)

    return [
        PreflightFinding(
            id="validators.ajv-formats",
            label="ajv-formats",
            ok=returncode == 0,
            detail="ajv-formats loaded" if returncode == 0 else output,
            install="volta install ajv-cli ajv-formats",
        )
    ]


def _check_github(prereq: dict[str, Any]) -> list[PreflightFinding]:
    repo = (prereq.get("github") or {}).get("repo")
    if not repo:
        return []
    findings = [
        _run_command_check(
            id_="github.rate_limit",
            label="GitHub auth",
            argv=["gh", "api", "/rate_limit"],
            ok_detail="gh api /rate_limit succeeded",
            install="gh auth login",
        ),
        _run_command_check(
            id_="github.repo",
            label=f"GitHub repo {repo}",
            argv=["gh", "api", f"/repos/{repo}", "-H", "Accept: application/vnd.github+json"],
            ok_detail=f"gh can access {repo}",
            install=f"gh repo view {repo}",
        ),
    ]
    return findings


def _check_language_tools(prereq: dict[str, Any]) -> list[PreflightFinding]:
    findings: list[PreflightFinding] = []
    plugin_list: str | None = None
    for language in (prereq.get("lsp") or {}).get("languages") or []:
        registry = _load_language_registry(str(language))
        if isinstance(registry, PreflightFinding):
            findings.append(registry)
            continue

        lsp = registry["lsp"]
        binary = lsp["binary"]
        binary_path = shutil.which(binary)
        findings.append(
            PreflightFinding(
                id=f"lsp.{language}.binary",
                label=f"{binary} ({language} LSP)",
                ok=binary_path is not None,
                detail=f"found at {binary_path}" if binary_path else f"{binary} not found on PATH",
                install=lsp["binary_install"],
                notes=list(lsp.get("gotchas") or []),
            )
        )

        plugin = lsp.get("plugin")
        if plugin:
            if plugin_list is None:
                plugin_list = _claude_plugin_list()
            plugin_list_ok = not plugin_list.startswith("ERROR:")
            findings.append(
                PreflightFinding(
                    id=f"lsp.{language}.plugin",
                    label=f"{plugin} ({language} Claude plugin)",
                    ok=plugin_list_ok and plugin in plugin_list,
                    detail=(
                        "plugin installed"
                        if plugin_list_ok and plugin in plugin_list
                        else plugin_list.removeprefix("ERROR: ")
                        if not plugin_list_ok
                        else "plugin not installed"
                    ),
                    install=lsp["plugin_install"],
                    notes=list(lsp.get("gotchas") or []),
                )
            )
    return findings


def _claude_plugin_list() -> str:
    if shutil.which("claude") is None:
        return "ERROR: claude not found on PATH"
    returncode, output = _run_capture(["claude", "plugin", "list"], timeout=20)
    if returncode != 0:
        return f"ERROR: {output}"
    return output


def _check_tree_sitter(prereq: dict[str, Any]) -> list[PreflightFinding]:
    tree_sitter = ((prereq.get("indexing") or {}).get("tree-sitter")) or {}
    findings: list[PreflightFinding] = []
    for language in tree_sitter.get("grammars") or []:
        registry = _load_language_registry(str(language))
        if isinstance(registry, PreflightFinding):
            findings.append(registry)
            continue
        if shutil.which("tree-sitter") is None:
            findings.append(
                PreflightFinding(
                    id=f"tree-sitter.{language}",
                    label=f"tree-sitter grammar: {language}",
                    ok=False,
                    detail="tree-sitter not found on PATH",
                    install=registry["tree-sitter"]["grammar_install"],
                )
            )
            continue
        ts = registry["tree-sitter"]
        with tempfile.NamedTemporaryFile("w", suffix=f".{language}", delete=False) as fh:
            fh.write(ts["verify_snippet"] + "\n")
            snippet_path = Path(fh.name)
        try:
            returncode, output = _run_capture(
                [
                    "tree-sitter",
                    "parse",
                    "--scope",
                    ts["verify_scope"],
                    str(snippet_path),
                ],
                timeout=20,
            )
        finally:
            snippet_path.unlink(missing_ok=True)
        findings.append(
            PreflightFinding(
                id=f"tree-sitter.{language}",
                label=f"tree-sitter grammar: {language}",
                ok=returncode == 0,
                detail="verify snippet parsed" if returncode == 0 else output,
                install=ts["grammar_install"],
            )
        )
    return findings


def _load_language_registry(language: str) -> dict[str, Any] | PreflightFinding:
    path = tool_root() / "languages" / f"{language}.toml"
    if not path.is_file():
        return PreflightFinding(
            id=f"language.{language}",
            label=f"language registry: {language}",
            ok=False,
            detail=f"{path} not found",
        )
    loaded = _load_toml(path)
    if not isinstance(loaded, dict):
        return PreflightFinding(
            id=f"language.{language}",
            label=f"language registry: {language}",
            ok=False,
            detail=loaded,
        )
    if shutil.which("ajv") is None:
        return loaded
    ok, output = run_ajv(
        schema_dir() / SCHEMAS["language-registry"],
        json.dumps(loaded).encode(),
    )
    if not ok:
        return PreflightFinding(
            id=f"language.{language}",
            label=f"language registry: {language}",
            ok=False,
            detail=output,
        )
    return loaded


def _check_quality_gate_commands(repo_root: Path) -> list[PreflightFinding]:
    config_path = repo_root / ".woof" / "quality-gates.toml"
    if not config_path.is_file():
        return []
    loaded = _load_toml(config_path)
    if not isinstance(loaded, dict):
        return [
            PreflightFinding(
                id="quality-gates.config",
                label="quality-gates.toml",
                ok=False,
                detail=loaded,
            )
        ]
    findings: list[PreflightFinding] = []
    for name, gate in (loaded.get("gates") or {}).items():
        command = str(gate.get("command") or "")
        try:
            first = shlex.split(command)[0]
        except (IndexError, ValueError) as exc:
            findings.append(
                PreflightFinding(
                    id=f"quality-gates.{name}",
                    label=f"quality gate command: {name}",
                    ok=False,
                    detail=f"cannot parse command {command!r}: {exc}",
                )
            )
            continue

        exists = _command_exists(first, repo_root)
        findings.append(
            PreflightFinding(
                id=f"quality-gates.{name}",
                label=f"quality gate command: {name}",
                ok=exists,
                detail=f"{first} resolves" if exists else f"{first} not found on PATH",
            )
        )
    return findings


def _command_exists(command: str, repo_root: Path) -> bool:
    if "/" in command:
        candidate = (
            (repo_root / command).resolve() if not command.startswith("/") else Path(command)
        )
        return candidate.exists() and os.access(candidate, os.X_OK)
    return shutil.which(command) is not None


def _run_command_check(
    *,
    id_: str,
    label: str,
    argv: list[str],
    ok_detail: str,
    install: str | None = None,
) -> PreflightFinding:
    if shutil.which(argv[0]) is None:
        return PreflightFinding(
            id=id_,
            label=label,
            ok=False,
            detail=f"{argv[0]} not found on PATH",
            install=install,
        )
    returncode, output = _run_capture(argv, timeout=20)
    return PreflightFinding(
        id=id_,
        label=label,
        ok=returncode == 0,
        detail=ok_detail if returncode == 0 else output,
        install=install,
    )


def _run_capture(argv: list[str], *, timeout: int) -> tuple[int, str]:
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return 127, f"{argv[0]} not found on PATH"
    except subprocess.TimeoutExpired as exc:
        output = ((exc.stdout or "") + (exc.stderr or "")).strip()
        detail = f"timed out after {timeout}s"
        return 124, f"{detail}\n{output}".strip()
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def _print_text_result(result: PreflightResult) -> None:
    failed = result.failed
    if failed:
        print(f"[INFRA PREFLIGHT FAILED - {len(failed)} missing prerequisite(s)]")
    else:
        print(f"[INFRA PREFLIGHT PASSED - {len(result.findings)} check(s)]")

    for finding in result.findings:
        mark = "OK" if finding.ok else "FAIL"
        print(f"{mark} {finding.label}")
        if finding.required:
            print(f"  Required: {finding.required}")
        print(f"  {finding.detail}")
        if not finding.ok and finding.install:
            print("  Install:")
            for line in finding.install.rstrip().splitlines():
                print(f"    {line}")
        if finding.notes:
            print("  Notes:")
            for note in finding.notes:
                print(f"    - {note}")

    if failed:
        print()
        print("Re-run `woof preflight` after installing.")
