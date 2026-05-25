"""Contract-decision reference validation shared by check-cd and Check 4."""

from __future__ import annotations

import ast
import importlib
import importlib.util
import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from importlib.machinery import SourceFileLoader
from pathlib import Path
from typing import Any

import yaml

from woof.paths import schema_dir

HTTP_METHODS = {"delete", "get", "head", "options", "patch", "post", "put", "trace"}
PYDANTIC_BASE_NAMES = {"BaseModel", "BaseSettings"}


@dataclass(frozen=True)
class ContractRefFinding:
    id: str
    kind: str
    ref: str
    ok: bool
    detail: str
    source_path: str | None = None


@dataclass(frozen=True)
class ContractRefResult:
    epic_md: Path
    total: int
    verified: int
    findings: list[ContractRefFinding]


class ContractRefUsageError(Exception):
    """Raised when the EPIC.md artefact itself cannot be loaded or validated."""


AJV_MISSING_HINT = (
    "ajv-cli not found on PATH; install ajv-cli and ajv-formats "
    "(for example `volta install ajv-cli ajv-formats`) and verify with `woof preflight`"
)


def validate_contract_refs(
    epic_md: Path,
    *,
    only_ids: set[str] | None = None,
) -> ContractRefResult:
    """Validate contract decision references in ``epic_md``.

    ``only_ids`` limits validation to the active story's owned contract IDs. An
    unknown requested ID is a failed finding rather than a usage error because
    it is a Stage-5 contract violation, not a malformed command invocation.
    """

    epic_md = epic_md.resolve()
    if not epic_md.is_file():
        raise ContractRefUsageError(f"{epic_md} not found")

    try:
        front = _load_epic_front_matter(epic_md)
    except (ValueError, yaml.YAMLError) as exc:
        raise ContractRefUsageError(f"{epic_md}: {exc}") from exc

    _validate_epic_schema(epic_md, front)

    repo_root = _repo_root_for(epic_md)
    cds = front.get("contract_decisions") or []
    if not isinstance(cds, list):
        cds = []

    selected_cds = [
        cd for cd in cds if isinstance(cd, dict) and (only_ids is None or cd.get("id") in only_ids)
    ]

    findings: list[ContractRefFinding] = []
    for cd in selected_cds:
        findings.append(_check_contract_decision(repo_root, cd))

    if only_ids is not None:
        found_ids = {finding.id for finding in findings}
        for missing_id in sorted(only_ids - found_ids):
            findings.append(
                ContractRefFinding(
                    id=missing_id,
                    kind="missing",
                    ref="",
                    ok=False,
                    detail="contract decision referenced by story but not declared in EPIC.md",
                )
            )

    return ContractRefResult(
        epic_md=epic_md,
        total=len(findings),
        verified=sum(1 for finding in findings if finding.ok),
        findings=findings,
    )


def _load_epic_front_matter(epic_md: Path) -> dict[str, Any]:
    text = epic_md.read_text()
    if not text.startswith("---\n"):
        raise ValueError("no YAML front-matter (file must start with '---\\n')")
    end = text.find("\n---\n", 4)
    if end < 0:
        raise ValueError("unterminated YAML front-matter")
    front = yaml.safe_load(text[4:end]) or {}
    if not isinstance(front, dict):
        raise ValueError("front-matter root must be an object")
    return front


def _validate_epic_schema(epic_md: Path, front: dict[str, Any]) -> None:
    if shutil.which("ajv") is None:
        raise ContractRefUsageError(AJV_MISSING_HINT)

    schema_path = schema_dir() / "epic.schema.json"
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        json.dump(front, fh)
        data_path = Path(fh.name)

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
                str(data_path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        data_path.unlink(missing_ok=True)

    if proc.returncode != 0:
        output = (proc.stdout + proc.stderr).strip()
        raise ContractRefUsageError(f"{epic_md}: front-matter invalid\n{output}")


def _repo_root_for(epic_md: Path) -> Path:
    repo_root = epic_md.parent
    while repo_root != repo_root.parent and not (repo_root / ".git").exists():
        repo_root = repo_root.parent
    if not (repo_root / ".git").exists():
        return epic_md.parent
    return repo_root


def _check_contract_decision(repo_root: Path, cd: dict[str, Any]) -> ContractRefFinding:
    cd_id = str(cd.get("id", "<missing>"))
    if cd.get("openapi_ref"):
        ref = str(cd["openapi_ref"])
        source = _openapi_source_path(ref)
        ok, detail = _check_openapi_ref(repo_root, ref)
        return ContractRefFinding(cd_id, "openapi_ref", ref, ok, detail, source)
    if cd.get("pydantic_ref"):
        ref = str(cd["pydantic_ref"])
        source = _pydantic_source_path(ref)
        ok, detail = _check_pydantic_ref(repo_root, ref)
        return ContractRefFinding(cd_id, "pydantic_ref", ref, ok, detail, source)
    if cd.get("json_schema_ref"):
        ref = str(cd["json_schema_ref"])
        source = ref
        ok, detail = _check_json_schema_ref(repo_root, ref)
        return ContractRefFinding(cd_id, "json_schema_ref", ref, ok, detail, source)
    return ContractRefFinding(cd_id, "missing", "", False, "no contract reference declared")


def _openapi_source_path(ref: str) -> str:
    return ref.split("#", 1)[0] if "#" in ref else ref


def _pydantic_source_path(ref: str) -> str | None:
    if ":" not in ref:
        return None
    locator = ref.rsplit(":", 1)[0]
    if locator.endswith(".py") or "/" in locator:
        return locator
    return None


def _resolve_json_pointer(doc: object, pointer: str) -> object | None:
    if pointer in ("", "/"):
        return doc
    if not pointer.startswith("/"):
        return None
    cur: object = doc
    for raw in pointer.lstrip("/").split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(cur, list):
            try:
                cur = cur[int(token)]
            except (ValueError, IndexError):
                return None
        elif isinstance(cur, dict):
            if token not in cur:
                return None
            cur = cur[token]
        else:
            return None
    return cur


def _json_pointer_tokens(pointer: str) -> list[str] | None:
    if pointer in ("", "/"):
        return []
    if not pointer.startswith("/"):
        return None
    return [raw.replace("~1", "/").replace("~0", "~") for raw in pointer.lstrip("/").split("/")]


def _check_openapi_ref(repo_root: Path, ref: str) -> tuple[bool, str]:
    if "#" not in ref:
        return False, f"openapi_ref missing '#<json-pointer>' fragment: {ref!r}"
    file_part, pointer = ref.split("#", 1)
    spec_path = (repo_root / file_part).resolve()
    if not spec_path.is_file():
        return False, f"openapi document not found: {file_part}"
    try:
        with spec_path.open("rb") as fh:
            doc = yaml.safe_load(fh)
    except yaml.YAMLError as exc:
        return False, f"openapi document failed to parse: {exc}"
    if not isinstance(doc, dict):
        return False, f"openapi document root must be an object: {file_part}"
    if "openapi" not in doc:
        return False, f"openapi document missing required 'openapi' field: {file_part}"
    target = _resolve_json_pointer(doc, pointer)
    if target is None:
        return False, f"json pointer '{pointer}' did not resolve in {file_part}"
    if not isinstance(target, dict):
        return False, f"json pointer '{pointer}' resolved to non-object {type(target).__name__}"
    tokens = _json_pointer_tokens(pointer)
    if tokens and tokens[0] == "paths":
        if len(tokens) < 3:
            return (
                False,
                f"openapi_ref under #/paths must point to an operation, not path item: {pointer}",
            )
        method = tokens[2].lower()
        if method not in HTTP_METHODS:
            return (
                False,
                f"openapi_ref under #/paths must end with an HTTP method, got {tokens[2]!r}",
            )
        responses = target.get("responses")
        if not isinstance(responses, dict):
            return False, f"openapi operation '{pointer}' is missing a responses object"
        return (
            True,
            f"resolved OpenAPI {method.upper()} operation with {len(responses)} response(s)",
        )
    return True, f"resolved to {type(target).__name__} with {len(target)} key(s)"


def _check_pydantic_ref(repo_root: Path, ref: str) -> tuple[bool, str]:
    if ":" not in ref:
        return False, f"pydantic_ref must be '<file-or-module>:<ClassName>': {ref!r}"
    locator, class_name = ref.rsplit(":", 1)
    try:
        import pydantic  # noqa: F401
        from pydantic import BaseModel
    except ImportError:
        return False, "pydantic not installed in this environment"
    except Exception as exc:
        return False, f"failed to import pydantic: {exc}"

    module: object | None
    if locator.endswith(".py") or "/" in locator:
        path = (repo_root / locator).resolve()
        if not path.is_file():
            return False, f"pydantic source file not found: {locator}"
        loader = SourceFileLoader(f"_woof_check_cd_{path.stem}", str(path))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        if spec is None:
            return False, f"failed to build module spec for {locator}"
        module = importlib.util.module_from_spec(spec)
        try:
            loader.exec_module(module)
        except Exception as exc:
            return _check_pydantic_source_static(path, class_name, import_error=str(exc))
    else:
        try:
            module = importlib.import_module(locator)
        except Exception as exc:
            return False, f"failed to import {locator}: {exc}"

    cls = getattr(module, class_name, None)
    if cls is None:
        return False, f"class '{class_name}' not found in {locator}"
    try:
        if not issubclass(cls, BaseModel):
            return False, f"'{class_name}' is not a pydantic.BaseModel subclass"
    except TypeError:
        return False, f"'{class_name}' is not a class"
    return True, f"BaseModel subclass with {len(cls.model_fields)} field(s)"


def _check_pydantic_source_static(
    path: Path,
    class_name: str,
    *,
    import_error: str,
) -> tuple[bool, str]:
    """Fallback for file refs whose project dependencies are outside Woof's venv."""

    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as exc:
        return False, f"failed to import {path}: {import_error}; source parse failed: {exc}"

    aliases = _pydantic_base_aliases(tree)
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            base = _matching_pydantic_base(node, aliases)
            if base is None:
                return False, f"'{class_name}' is not a pydantic.BaseModel subclass"
            field_count = _annotated_field_count(node)
            return (
                True,
                f"static source check found {class_name} subclassing {base} "
                f"with {field_count} annotated field(s); import skipped because {import_error}",
            )
    return False, f"class '{class_name}' not found in {path}"


def _pydantic_base_aliases(tree: ast.Module) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module in {"pydantic", "pydantic_settings"}:
            for alias in node.names:
                if alias.name in PYDANTIC_BASE_NAMES:
                    aliases[alias.asname or alias.name] = alias.name
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in {"pydantic", "pydantic_settings"}:
                    aliases[alias.asname or alias.name] = alias.name
    return aliases


def _matching_pydantic_base(node: ast.ClassDef, aliases: dict[str, str]) -> str | None:
    for base in node.bases:
        name = _base_name(base)
        if name is None:
            continue
        if name in aliases and aliases[name] in PYDANTIC_BASE_NAMES:
            return aliases[name]
        qualifier, _, attr = name.rpartition(".")
        if attr in PYDANTIC_BASE_NAMES and qualifier in aliases:
            return attr
    return None


def _base_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _base_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return None


def _annotated_field_count(node: ast.ClassDef) -> int:
    count = 0
    for statement in node.body:
        if (
            isinstance(statement, ast.AnnAssign)
            and isinstance(statement.target, ast.Name)
            and not statement.target.id.startswith("_")
        ):
            count += 1
    return count


def _check_json_schema_ref(repo_root: Path, ref: str) -> tuple[bool, str]:
    if shutil.which("ajv") is None:
        raise ContractRefUsageError(AJV_MISSING_HINT)

    schema_path = (repo_root / ref).resolve()
    if not schema_path.is_file():
        return False, f"json_schema file not found: {ref}"
    proc = subprocess.run(
        [
            "ajv",
            "compile",
            "--spec=draft2020",
            "-c",
            "ajv-formats",
            "-s",
            str(schema_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        msg = (proc.stdout + proc.stderr).strip().splitlines()
        first = msg[0] if msg else "(no output)"
        return False, f"ajv compile rejected schema: {first}"
    try:
        schema_doc = json.loads(schema_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return False, f"json_schema failed to parse after compile: {exc}"
    examples = schema_doc.get("examples") if isinstance(schema_doc, dict) else None
    if examples is None:
        return True, "ajv compile passed"
    if not isinstance(examples, list):
        return False, "json_schema top-level examples must be an array when present"
    if not examples:
        return True, "ajv compile passed"

    for index, example in enumerate(examples):
        ok, detail = _validate_json_schema_example(schema_path, example)
        if not ok:
            return False, f"json_schema examples[{index}] failed validation: {detail}"
    return True, f"ajv compile passed; {len(examples)} top-level example(s) validated"


def _validate_json_schema_example(schema_path: Path, example: object) -> tuple[bool, str]:
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        json.dump(example, fh)
        data_path = Path(fh.name)
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
                str(data_path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        data_path.unlink(missing_ok=True)

    if proc.returncode == 0:
        return True, "ok"
    msg = (proc.stdout + proc.stderr).strip().splitlines()
    return False, msg[0] if msg else "(no output)"
