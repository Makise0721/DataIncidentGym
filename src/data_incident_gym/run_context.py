from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from data_incident_gym.config import PROJECT_ROOT

RUN_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
CASE_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]{2,63}$")
ACTIVE_RUN_SCHEMA_VERSION = "m4.active_fault_run.v1"
ACTIVE_RUN_PATH = Path(".dig/lab/active_fault_run.json")
ACTIVE_RUN_TEMP_PATH = Path(".dig/lab/active_fault_run.json.tmp")
_ACTIVE_RUN_KEYS = {
    "incident_case_id",
    "run_id",
    "schema_version",
    "verification_status",
}
_METADATA_KEYS = {
    "schema_version",
    "run_id",
    "incident_case_id",
    "dbt_exit_code",
    "ground_truth_digest",
    "artifacts",
}
_ARTIFACT_KEYS = {"manifest", "run_results", "dbt_log", "schema"}
_EXPECTED_ARTIFACTS = {
    "manifest": "dbt/target/manifest.json",
    "run_results": "dbt/target/run_results.json",
    "dbt_log": "dbt/logs/dbt.log",
    "schema": "schema.json",
}


class RunContextError(RuntimeError):
    code = "INVALID_RUN_CONTEXT"

    def __init__(self, message: str = "Invalid run context") -> None:
        super().__init__(message)
        self.__cause__ = None
        self.__context__ = None


@dataclass(frozen=True)
class ActiveRun:
    incident_case_id: str
    run_id: str
    schema_version: Literal["m4.active_fault_run.v1"]
    verification_status: Literal["EXPECTED_FAILURE"]


@dataclass(frozen=True)
class RunContext:
    incident_case_id: str
    run_id: str
    metadata: dict[str, Any]


def _fail(message: str = "Invalid run context") -> None:
    raise RunContextError(message)


def _validate_case_id(case_id: object) -> str:
    if not isinstance(case_id, str) or CASE_ID_PATTERN.fullmatch(case_id) is None:
        _fail()
    return case_id


def _validate_run_id(run_id: object) -> str:
    if not isinstance(run_id, str) or RUN_ID_PATTERN.fullmatch(run_id) is None:
        _fail()
    return run_id


def _base(project_root: Path) -> Path:
    try:
        root = Path(project_root).resolve(strict=True)
    except (OSError, RuntimeError):
        _fail()
    return root


def _reject_symlink(path: Path) -> None:
    try:
        if path.is_symlink():
            _fail()
    except OSError:
        _fail()


def _lab_root(project_root: Path, *, create: bool) -> Path:
    root = _base(project_root)
    dig = root / ".dig"
    lab = dig / "lab"
    runs = lab / "runs"
    for path in (dig, lab, runs):
        _reject_symlink(path)
    try:
        if create:
            runs.mkdir(parents=True, exist_ok=True)
        elif not lab.exists() or not runs.exists():
            return lab
        elif not lab.is_dir() or not runs.is_dir():
            _fail()
    except OSError:
        _fail()
    return lab


def _fixed_path(project_root: Path, relative: Path, *, create: bool) -> Path:
    lab = _lab_root(project_root, create=create)
    path = lab / relative.name
    _reject_symlink(path)
    return path


class _DuplicateKey(ValueError):
    pass


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKey
        result[key] = value
    return result


def _read_json(path: Path) -> dict[str, Any]:
    _reject_symlink(path)
    try:
        text = path.read_bytes().decode("utf-8")
        value = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )
    except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError):
        _fail()
    if not isinstance(value, dict):
        _fail()
    return value


def _validate_active_payload(payload: dict[str, Any]) -> ActiveRun:
    if set(payload) != _ACTIVE_RUN_KEYS:
        _fail()
    case_id = _validate_case_id(payload["incident_case_id"])
    run_id = _validate_run_id(payload["run_id"])
    if payload["schema_version"] != ACTIVE_RUN_SCHEMA_VERSION:
        _fail()
    if payload["verification_status"] != "EXPECTED_FAILURE":
        _fail()
    return ActiveRun(case_id, run_id, ACTIVE_RUN_SCHEMA_VERSION, "EXPECTED_FAILURE")


def _validate_relative_artifact(value: object, expected: str) -> None:
    if value != expected or not isinstance(value, str):
        _fail()
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        _fail()


def _metadata_path(project_root: Path, run_id: str) -> Path:
    lab = _lab_root(project_root, create=False)
    run_root = lab / "runs" / run_id
    _reject_symlink(run_root)
    try:
        if not run_root.is_dir() or run_root.parent != (lab / "runs"):
            _fail()
    except OSError:
        _fail()
    metadata_path = run_root / "metadata.json"
    _reject_symlink(metadata_path)
    return metadata_path


def _validate_metadata(payload: dict[str, Any], run_id: str, case_id: str) -> RunContext:
    if set(payload) != _METADATA_KEYS:
        _fail()
    if payload["schema_version"] != "m2.run.v1":
        _fail()
    if payload["run_id"] != run_id or payload["incident_case_id"] != case_id:
        _fail()
    if type(payload["dbt_exit_code"]) is not int:
        _fail()
    digest = payload["ground_truth_digest"]
    if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        _fail()
    artifacts = payload["artifacts"]
    if not isinstance(artifacts, dict) or set(artifacts) != _ARTIFACT_KEYS:
        _fail()
    for key, expected in _EXPECTED_ARTIFACTS.items():
        _validate_relative_artifact(artifacts[key], expected)
    return RunContext(case_id, run_id, payload)


def resolve_run_context(
    run_id: str,
    incident_case_id: str,
    project_root: Path = PROJECT_ROOT,
) -> RunContext:
    run_id = _validate_run_id(run_id)
    case_id = _validate_case_id(incident_case_id)
    metadata = _read_json(_metadata_path(project_root, run_id))
    return _validate_metadata(metadata, run_id, case_id)


def resolve_active_run(
    project_root: Path = PROJECT_ROOT,
    incident_case_id: str | None = None,
) -> ActiveRun:
    pointer = _fixed_path(project_root, ACTIVE_RUN_PATH, create=False)
    active = _validate_active_payload(_read_json(pointer))
    if (
        incident_case_id is not None
        and active.incident_case_id != _validate_case_id(incident_case_id)
    ):
        _fail()
    resolve_run_context(active.run_id, active.incident_case_id, project_root)
    return active


def publish_active_run(
    project_root: Path = PROJECT_ROOT,
    *,
    incident_case_id: str,
    run_id: str,
    verification_status: str,
) -> ActiveRun:
    case_id = _validate_case_id(incident_case_id)
    valid_run_id = _validate_run_id(run_id)
    if verification_status != "EXPECTED_FAILURE":
        _fail()
    pointer = _fixed_path(project_root, ACTIVE_RUN_PATH, create=True)
    temporary = _fixed_path(project_root, ACTIVE_RUN_TEMP_PATH, create=True)
    payload = {
        "incident_case_id": case_id,
        "run_id": valid_run_id,
        "schema_version": ACTIVE_RUN_SCHEMA_VERSION,
        "verification_status": "EXPECTED_FAILURE",
    }
    try:
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temporary.replace(pointer)
    except OSError:
        try:
            if temporary.exists() and not temporary.is_symlink():
                temporary.unlink()
        except OSError:
            pass
        _fail()
    return ActiveRun(case_id, valid_run_id, ACTIVE_RUN_SCHEMA_VERSION, "EXPECTED_FAILURE")


def clear_active_run(project_root: Path = PROJECT_ROOT) -> None:
    lab = _lab_root(project_root, create=False)
    if not lab.is_dir():
        return
    for relative in (ACTIVE_RUN_PATH, ACTIVE_RUN_TEMP_PATH):
        path = lab / relative.name
        try:
            if path.exists() or path.is_symlink():
                path.unlink()
        except OSError:
            _fail()
