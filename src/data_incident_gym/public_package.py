"""Public task package (T11): the leak-free surface an external strategy gets.

One run's public package contains exactly what a strategy is allowed to see —
the incident brief, the observable relations, the granted tool list, the budget
and the protocol version — plus the instructions. It never contains the private
scenario contract, the scoring attachments, the case id, the expected answer, or
anything copied out of ``config/`` and ``.dig/``.

``verify_public_package`` re-reads a built package and fails closed on any leak:
forbidden markers, a byte-identical copy of a private file, or a path that only
exists inside the private planes.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from data_incident_gym.config import PROJECT_ROOT
from data_incident_gym.run_context import resolve_run_context
from data_incident_gym.strategy_adapter import (
    PROTOCOL_TOOL_ALLOWLIST,
    STRATEGY_PROTOCOL_VERSION,
    ProtocolBudget,
)

PUBLIC_PACKAGE_SCHEMA_VERSION = "p1.public_task_package.v1"
PUBLIC_PACKAGE_FILENAMES = ("task.json", "README.md")

#: The exact top-level fields ``task.json`` may carry — nothing else, so a
#: smuggled ``incident_case_id`` or any other private field fails the check.
TASK_CONTEXT_FIELDS = frozenset(
    {
        "schema_version",
        "protocol_version",
        "created_at",
        "run_id",
        "incident_brief",
        "observable_relations",
        "tool_allowlist",
        "budget",
        "submission",
        "isolation",
    }
)

#: Substrings that must never appear anywhere in a public package.
FORBIDDEN_MARKERS = (
    "expected_status",
    "ground_truth",
    "scoring-inputs",
    "config/scenarios",
    ".dig/",
    "acceptable_root_causes",
    "verification.json",
)

#: Private planes whose files may never be copied into a package.
PRIVATE_DIRS = ("config/scenarios", ".dig/scoring-inputs", "artifacts")


class PublicPackageError(RuntimeError):
    def __init__(self, code: str, *, detail: str | None = None) -> None:
        self.code = code
        self.detail = detail
        super().__init__(code if detail is None else f"{code}: {detail}")
        self.__cause__ = None
        self.__context__ = None


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_public_task_package(
    run_id: str,
    output_dir: Path,
    *,
    project_root: Path = PROJECT_ROOT,
    tool_allowlist: frozenset[str] = PROTOCOL_TOOL_ALLOWLIST,
    created_at: datetime | None = None,
) -> Path:
    """Write the public surface of one run; nothing private is read or copied.

    The incident brief and the relation whitelist are the same public facts the
    diagnosis side receives; the case identity is deliberately absent.
    """

    context = resolve_run_context(run_id, project_root=project_root)
    target = Path(output_dir)
    if target.is_symlink():
        raise PublicPackageError("PUBLIC_PACKAGE_PATH_INVALID", detail=str(target))
    target.mkdir(parents=True, exist_ok=True)
    observable = {
        kind: tuple(sorted(relations))
        for kind, relations in (context.runtime.get("observable_relations", {}) or {}).items()
    }
    task = {
        "schema_version": PUBLIC_PACKAGE_SCHEMA_VERSION,
        "protocol_version": STRATEGY_PROTOCOL_VERSION,
        "created_at": (created_at or datetime.now(UTC)).isoformat(),
        "run_id": run_id,
        "incident_brief": context.incident_brief.model_dump(mode="json"),
        "observable_relations": {kind: list(values) for kind, values in observable.items()},
        "tool_allowlist": sorted(tool_allowlist),
        "budget": ProtocolBudget().model_dump(mode="json"),
        "submission": {
            "channel": "p1.strategy_adapter.v1",
            "file": "submission.json",
            "write_to": "the run output directory the harness grants (its --out)",
            "required_fields": ["status", "summary", "confidence"],
            "optional_fields": [
                "root_cause_code",
                "affected_assets",
                "evidence_ids",
                "claims",
                "unresolved_evidence",
                "recommended_actions",
            ],
            "rule": (
                "only evidence ids a tool call returned may be cited; the harness relays this "
                "file to the protocol submit and refuses what the session does not accept"
            ),
            "note": "the final answer is submitted through the protocol, not through MCP",
        },
        "isolation": {
            "expected_mounts": ["<package>", "<package source>"],
            "forbidden": [
                "private scenario contract",
                "scoring attachments",
                "other runs' artifacts",
                "management credentials",
                "database write access",
            ],
            "trusted_development_mode": (
                "an agent that can read this repository's private configuration or scoring "
                "planes is a trusted development setup and must never be used for "
                "leak-free comparison"
            ),
        },
    }
    (target / "task.json").write_text(
        json.dumps(task, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="",
    )
    (target / "README.md").write_text(_readme(run_id), encoding="utf-8", newline="")
    return target


def _readme(run_id: str) -> str:
    return f"""# Public task package `{run_id}`

This directory is everything an external strategy may see for this run.

- `task.json` — the public task context: incident brief, observable relations,
  granted tools, budget ({PROTOCOL_TOOL_BUDGET_TEXT}), protocol version.
- The final answer is submitted through `p1.strategy_adapter.v1`, not through MCP:
  write `submission.json` into the run output directory the harness grants and the
  harness relays it to `submit`. The accepted and required fields are listed in
  `task.json` under `submission`; a citation no tool call returned, or any field
  outside that list, is refused.
- The six read-only evidence tools are served by the harness, which attaches them
  to your stdin/stdout as an MCP server — for a local run:
  `python -m data_incident_gym.mcp_server --run-id {run_id}`.
- Refusals are real: a refused call keeps the backend's error code as its receipt,
  and every call the harness processed is in the harness's log whether or not the
  strategy logs it.

## Isolation expectations

- Mount only this package and the package source read-only; never mount the
  repository's private configuration, scoring-attachment or artifact planes.
- Management credentials (database URLs, passwords, model keys) must not exist in
  the strategy process environment.
- A strategy that can read those private planes is a **trusted development
  setup**: useful for demos, never for leak-free comparison.
"""


PROTOCOL_TOOL_BUDGET_TEXT = "8 model requests / 8 tool calls / 2 retries / 300s"


def verify_public_package(
    package_dir: Path,
    *,
    project_root: Path = PROJECT_ROOT,
) -> dict[str, Any]:
    """Fail closed on any leak in a built package.

    Checks: only the allowed filenames exist, no forbidden marker appears, and
    no file is a byte-identical copy of a private file (contract, attachments or
    run artifacts).
    """

    target = Path(package_dir)
    if target.is_symlink() or not target.is_dir():
        raise PublicPackageError("PUBLIC_PACKAGE_PATH_INVALID", detail=str(target))
    findings: list[dict[str, str]] = []
    files: list[Path] = []
    for path in sorted(target.rglob("*")):
        if path.is_symlink():
            findings.append({"code": "SYMLINK_IN_PACKAGE", "path": path.name})
            continue
        if path.is_dir():
            continue
        files.append(path)
        if path.name not in PUBLIC_PACKAGE_FILENAMES:
            findings.append({"code": "UNEXPECTED_FILE", "path": path.name})
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            findings.append({"code": "UNREADABLE_FILE", "path": path.name})
            continue
        for marker in FORBIDDEN_MARKERS:
            if marker in text:
                findings.append({"code": "FORBIDDEN_MARKER", "path": path.name, "marker": marker})
    task_path = target / "task.json"
    if task_path.is_file():
        try:
            task = json.loads(task_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            findings.append({"code": "TASK_CONTEXT_INVALID", "path": "task.json"})
        else:
            if not isinstance(task, dict):
                findings.append({"code": "TASK_CONTEXT_INVALID", "path": "task.json"})
            else:
                undeclared = sorted(set(task) - TASK_CONTEXT_FIELDS)
                missing = sorted(TASK_CONTEXT_FIELDS - set(task))
                if undeclared:
                    findings.append(
                        {
                            "code": "UNDECLARED_FIELD",
                            "path": "task.json",
                            "field": ", ".join(undeclared),
                        }
                    )
                if missing:
                    findings.append(
                        {
                            "code": "MISSING_FIELD",
                            "path": "task.json",
                            "field": ", ".join(missing),
                        }
                    )
    for case_id in sorted(_private_case_ids(project_root)):
        for path in files:
            try:
                text_body = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                continue
            if case_id in text_body:
                findings.append(
                    {"code": "PRIVATE_CASE_ID", "path": path.name, "case_id": case_id}
                )
    private_digests = {
        digest: str(path)
        for digest, path in _private_file_digests(project_root).items()
    }
    for path in files:
        digest = _digest(path)
        if digest in private_digests:
            findings.append(
                {
                    "code": "PRIVATE_FILE_COPY",
                    "path": path.name,
                    "source": private_digests[digest],
                }
            )
    if not any(path.name == "task.json" for path in files):
        findings.append({"code": "TASK_CONTEXT_MISSING", "path": "task.json"})
    return {
        "schema_version": PUBLIC_PACKAGE_SCHEMA_VERSION,
        "package": target.name,
        "files": sorted(path.name for path in files),
        "findings": findings,
        "clean": not findings,
    }


def _private_case_ids(project_root: Path) -> set[str]:
    """Scenario ids are private; the public package must never mention one."""

    scenarios = project_root / "config" / "scenarios"
    if not scenarios.is_dir():
        return set()
    return {path.stem for path in scenarios.glob("*.json") if path.is_file()}


def _private_file_digests(project_root: Path) -> dict[str, Path]:
    digests: dict[str, Path] = {}
    for relative in PRIVATE_DIRS:
        root = project_root / relative
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if path.is_file() and not path.is_symlink():
                try:
                    digests.setdefault(_digest(path), path)
                except OSError:
                    continue
    return digests


__all__ = [
    "FORBIDDEN_MARKERS",
    "PRIVATE_DIRS",
    "PUBLIC_PACKAGE_FILENAMES",
    "PUBLIC_PACKAGE_SCHEMA_VERSION",
    "PublicPackageError",
    "build_public_task_package",
    "verify_public_package",
]
