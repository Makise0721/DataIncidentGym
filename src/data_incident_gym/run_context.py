from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, StrictStr, field_validator, model_validator

from data_incident_gym.config import PROJECT_ROOT

RUN_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
CASE_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]{2,63}$")
ACTIVE_RUN_SCHEMA_VERSION = "p1.active_run.v1"
RUNTIME_SCHEMA_VERSION = "p1.runtime.v1"
ACTIVE_RUN_PATH = Path(".dig/lab/active_run.json")
ACTIVE_RUN_TEMP_PATH = Path(".dig/lab/active_run.json.tmp")
_ACTIVE_RUN_KEYS = {"run_id", "schema_version"}
_RUNTIME_KEYS = {
    "schema_version",
    "run_id",
    "dbt_exit_code",
    "artifacts",
    "observable_relations",
    "profile_spec_sha256",
}
_OBSERVABLE_RELATION_KEYS = {"schema", "profile", "history"}
_EXPECTED_ARTIFACTS = {
    "manifest": "dbt/target/manifest.json",
    "run_results": "dbt/target/run_results.json",
    "dbt_log": "dbt/logs/dbt.log",
    "schema": "schema.json",
    "profile_snapshot": "profile_snapshot.json",
    "incident_brief": "incident_brief.json",
}


class RunContextError(RuntimeError):
    code = "INVALID_RUN_CONTEXT"

    def __init__(self, message: str = "Invalid run context") -> None:
        super().__init__(message)
        self.__cause__ = None
        self.__context__ = None


class ObservedSignal(BaseModel):
    """Public, sanitized alert fact exposed to either diagnosis policy."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: StrictStr
    subject: StrictStr
    value: StrictStr


class IncidentBrief(BaseModel):
    """The only scenario-derived description that enters the diagnosis side."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["incident_brief.v1"]
    signal_code: StrictStr
    summary: StrictStr
    subjects: tuple[StrictStr, ...]
    logical_observed_at: datetime
    observations: tuple[ObservedSignal, ...]

    @field_validator("logical_observed_at")
    @classmethod
    def _validate_observed_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("logical_observed_at must be timezone-aware")
        return value

    @model_validator(mode="after")
    def validate_subjects(self) -> IncidentBrief:
        if len(self.subjects) != len(set(self.subjects)):
            raise ValueError("subjects must not contain duplicates")
        return self


@dataclass(frozen=True)
class ActiveRun:
    run_id: str
    schema_version: Literal["p1.active_run.v1"]


@dataclass(frozen=True)
class ObservableRunContext:
    run_id: str
    artifact_dir: Path
    runtime: dict[str, Any]
    incident_brief: IncidentBrief

    @property
    def metadata(self) -> dict[str, Any]:
        """Compatibility name for callers that only consume public runtime metadata."""

        return self.runtime

    @property
    def schema_path(self) -> Path:
        return self.artifact_dir / _EXPECTED_ARTIFACTS["schema"]

    @property
    def profile_snapshot_path(self) -> Path:
        return self.artifact_dir / _EXPECTED_ARTIFACTS["profile_snapshot"]

    @property
    def manifest_path(self) -> Path:
        return self.artifact_dir / _EXPECTED_ARTIFACTS["manifest"]

    @property
    def run_results_path(self) -> Path:
        return self.artifact_dir / _EXPECTED_ARTIFACTS["run_results"]

    @property
    def dbt_log_path(self) -> Path:
        return self.artifact_dir / _EXPECTED_ARTIFACTS["dbt_log"]


RunContext = ObservableRunContext


def _fail(message: str = "Invalid run context") -> None:
    raise RunContextError(message)


def _validate_run_id(run_id: object) -> str:
    if not isinstance(run_id, str) or RUN_ID_PATTERN.fullmatch(run_id) is None:
        _fail("非法 run_id")
    return run_id


def _base(project_root: Path) -> Path:
    try:
        root = Path(project_root).resolve(strict=True)
    except (OSError, RuntimeError):
        _fail("项目根目录无效")
    return root


def _reject_symlink(path: Path) -> None:
    try:
        if path.is_symlink():
            _fail("运行上下文不允许符号链接")
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
            _fail("运行目录不是目录")
    except OSError:
        _fail("无法访问运行目录")
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
    except (
        OSError,
        UnicodeDecodeError,
        ValueError,
        json.JSONDecodeError,
        _DuplicateKey,
    ):
        _fail(f"无法读取运行上下文：{path.name}")
    if not isinstance(value, dict):
        _fail(f"运行上下文必须是 JSON object：{path.name}")
    return value


def _validate_relative_artifact(value: object, expected: str) -> None:
    if not isinstance(value, str) or value != expected:
        _fail("运行产物路径不符合固定契约")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        _fail("运行产物路径越界")


def _validate_runtime(payload: dict[str, Any], run_id: str) -> dict[str, Any]:
    if set(payload) != _RUNTIME_KEYS:
        _fail("runtime 字段集合无效")
    if payload["schema_version"] != RUNTIME_SCHEMA_VERSION:
        _fail("runtime schema_version 无效")
    if payload["run_id"] != run_id:
        _fail("runtime run_id 不一致")
    if type(payload["dbt_exit_code"]) is not int:
        _fail("runtime dbt_exit_code 类型无效")
    digest = payload["profile_spec_sha256"]
    if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        _fail("runtime ProfileSpec hash 无效")
    artifacts = payload["artifacts"]
    if not isinstance(artifacts, dict) or set(artifacts) != set(_EXPECTED_ARTIFACTS):
        _fail("runtime artifacts 清单无效")
    for key, expected in _EXPECTED_ARTIFACTS.items():
        _validate_relative_artifact(artifacts[key], expected)
    observable = payload["observable_relations"]
    if not isinstance(observable, dict) or set(observable) != _OBSERVABLE_RELATION_KEYS:
        _fail("runtime observable_relations 无效")
    for values in observable.values():
        if not isinstance(values, list) or len(values) != len(set(values)):
            _fail("runtime observable relation 列表无效")
        if any(
            not isinstance(value, str)
            or re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", value) is None
            for value in values
        ):
            _fail("runtime observable relation 名称无效")
    return payload


def _run_root(project_root: Path, run_id: str) -> Path:
    lab = _lab_root(project_root, create=False)
    run_root = lab / "runs" / run_id
    _reject_symlink(run_root)
    try:
        if not run_root.is_dir() or run_root.parent != lab / "runs":
            _fail("运行目录不存在")
    except OSError:
        _fail("无法访问运行目录")
    return run_root


def _validate_context(run_id: str, project_root: Path) -> ObservableRunContext:
    run_root = _run_root(project_root, run_id)
    runtime = _validate_runtime(_read_json(run_root / "runtime.json"), run_id)
    brief_payload = _read_json(run_root / "incident_brief.json")
    try:
        brief = IncidentBrief.model_validate(brief_payload)
    except ValueError:
        _fail("incident_brief 无效")
    return ObservableRunContext(run_id, run_root, runtime, brief)


def resolve_run_context(
    run_id: str,
    project_root: Path = PROJECT_ROOT,
) -> ObservableRunContext:
    """Resolve only the public run contract."""

    if isinstance(project_root, str):
        _fail("运行上下文项目根目录无效")
    valid_run_id = _validate_run_id(run_id)
    return _validate_context(valid_run_id, Path(project_root))


def _validate_active_payload(payload: dict[str, Any]) -> ActiveRun:
    if set(payload) != _ACTIVE_RUN_KEYS:
        _fail("active run 字段集合无效")
    run_id = _validate_run_id(payload["run_id"])
    if payload["schema_version"] != ACTIVE_RUN_SCHEMA_VERSION:
        _fail("active run schema_version 无效")
    return ActiveRun(run_id, ACTIVE_RUN_SCHEMA_VERSION)


def resolve_active_run(project_root: Path = PROJECT_ROOT) -> ActiveRun:
    pointer = _fixed_path(project_root, ACTIVE_RUN_PATH, create=False)
    active = _validate_active_payload(_read_json(pointer))
    _validate_context(active.run_id, project_root)
    return active


def publish_active_run(
    project_root: Path = PROJECT_ROOT,
    *,
    run_id: str,
) -> ActiveRun:
    """Publish a pointer after public artifacts are complete."""
    valid_run_id = _validate_run_id(run_id)
    pointer = _fixed_path(project_root, ACTIVE_RUN_PATH, create=True)
    temporary = _fixed_path(project_root, ACTIVE_RUN_TEMP_PATH, create=True)
    _validate_context(valid_run_id, project_root)
    payload = {"run_id": valid_run_id, "schema_version": ACTIVE_RUN_SCHEMA_VERSION}
    try:
        temporary.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(pointer)
    except OSError:
        try:
            if temporary.exists() and not temporary.is_symlink():
                temporary.unlink()
        except OSError:
            pass
        _fail("无法发布 active run")
    return ActiveRun(valid_run_id, ACTIVE_RUN_SCHEMA_VERSION)


def clear_active_run(project_root: Path = PROJECT_ROOT) -> None:
    lab = _lab_root(project_root, create=False)
    if not lab.is_dir():
        return
    for relative in (ACTIVE_RUN_PATH, ACTIVE_RUN_TEMP_PATH):
        path = lab / relative.name
        try:
            if path.exists() or path.is_symlink():
                _reject_symlink(path)
                path.unlink()
        except OSError:
            _fail("无法清理 active run")
