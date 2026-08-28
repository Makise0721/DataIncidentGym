from __future__ import annotations

import json
import re
import shutil
import subprocess
from collections.abc import Callable
from datetime import datetime, timedelta
from enum import StrEnum
from importlib import resources
from pathlib import Path
from typing import Annotated, Any, Literal, NoReturn, Self
from urllib.parse import urlsplit
from uuid import uuid4

from jinja2 import Environment, StrictUndefined
from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    model_validator,
)

from data_incident_gym.config import PROJECT_ROOT
from data_incident_gym.diagnosis import (
    CaseId,
    Diagnosis,
    DiagnosisMetrics,
    DiagnosisRunResult,
    RunId,
    TraceEvent,
)
from data_incident_gym.diagnostic_agent import (
    SYSTEM_PROMPT_SHA256,
    SYSTEM_PROMPT_VERSION,
)
from data_incident_gym.evaluation import (
    EvaluationCheckCode,
    EvaluationResult,
    EvaluationStatus,
)
from data_incident_gym.evidence import EvidenceRecord

ARTIFACT_FILENAMES = (
    "metadata.json",
    "trace.jsonl",
    "evidence.json",
    "diagnosis.json",
    "evaluation.json",
    "report.md",
)
_RUN_DIRECTORY_PATTERN = re.compile(r"^\.[0-9a-f]{32}\.[0-9a-f]{32}\.tmp$")
_REVISION_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_RUNTIME_LABEL_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._:/-]{0,127}$")

RunCommand = Callable[..., subprocess.CompletedProcess[str]]


def _strict_aware_datetime(value: object) -> datetime:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError as error:
            raise ValueError("timestamp must be an ISO datetime") from error
    if type(value) is not datetime:
        raise ValueError("timestamp must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    if value.utcoffset() != timedelta(0):
        raise ValueError("timestamp must be UTC")
    return value


class RecoveryStatus(StrEnum):
    HEALTHY = "HEALTHY"
    FAILED = "FAILED"


class ArtifactWriteError(RuntimeError):
    code = "ARTIFACT_WRITE_FAILED"

    def __init__(self) -> None:
        super().__init__(self.code)
        self.__cause__ = None
        self.__context__ = None


class BudgetSummary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    model_request_limit: Literal[6]
    tool_call_limit: Literal[8]
    output_retry_limit: Literal[2]
    timeout_seconds: Literal[300]


class TraceEnvelope(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["m5.trace.v1"]
    sequence: Annotated[StrictInt, Field(ge=1)]
    event: TraceEvent


class EvidenceArtifact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["m5.evidence.v1"]
    incident_case_id: CaseId
    run_id: RunId
    records: tuple[EvidenceRecord, ...]

    @model_validator(mode="after")
    def validate_record_scope(self) -> Self:
        if any(record.run_id != self.run_id for record in self.records):
            raise ValueError("evidence records must match artifact run")
        return self


class RunMetadata(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["m5.metadata.v1"]
    incident_case_id: CaseId
    run_id: RunId
    code_revision: Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{40}$")]
    workspace_dirty: StrictBool
    provider: StrictStr
    model: StrictStr
    model_base_url: StrictStr
    budget: BudgetSummary
    prompt_version: Literal["m5.diagnosis.v2"]
    prompt_sha256: Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{64}$")]
    started_at: Annotated[datetime, BeforeValidator(_strict_aware_datetime)]
    finished_at: Annotated[datetime, BeforeValidator(_strict_aware_datetime)]
    elapsed_ms: Annotated[StrictInt, Field(ge=0)]
    diagnosis_metrics: DiagnosisMetrics
    evaluation_status: EvaluationStatus
    recovery_status: RecoveryStatus
    artifact_files: tuple[StrictStr, ...]

    @model_validator(mode="after")
    def validate_metadata_contract(self) -> Self:
        if self.finished_at < self.started_at:
            raise ValueError("finished_at must not precede started_at")
        if self.artifact_files != ARTIFACT_FILENAMES:
            raise ValueError("artifact_files must match the canonical six files")
        expected_elapsed = int((self.finished_at - self.started_at).total_seconds() * 1000)
        if self.elapsed_ms != expected_elapsed:
            raise ValueError("elapsed_ms must match timestamps")
        return self


class ArtifactRun(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    incident_case_id: CaseId
    run_id: RunId
    started_at: Annotated[datetime, BeforeValidator(_strict_aware_datetime)]
    finished_at: Annotated[datetime, BeforeValidator(_strict_aware_datetime)]
    recovery_status: RecoveryStatus
    model_base_url: StrictStr
    diagnosis_run: DiagnosisRunResult
    evaluation: EvaluationResult

    @model_validator(mode="after")
    def validate_cross_file_identity(self) -> Self:
        diagnosis = self.diagnosis_run.diagnosis
        parsed_base_url = urlsplit(self.model_base_url)
        if (
            parsed_base_url.scheme not in {"http", "https"}
            or not parsed_base_url.hostname
            or parsed_base_url.username is not None
            or parsed_base_url.password is not None
            or parsed_base_url.query
            or parsed_base_url.fragment
        ):
            raise ValueError("model_base_url must be a safe HTTP(S) URL")
        if self.finished_at < self.started_at:
            raise ValueError("finished_at must not precede started_at")
        if diagnosis.incident_case_id != self.incident_case_id:
            raise ValueError("diagnosis case must match artifact run")
        if diagnosis.run_id != self.run_id:
            raise ValueError("diagnosis run_id must match artifact run")
        if self.evaluation.incident_case_id != self.incident_case_id:
            raise ValueError("evaluation case must match artifact run")
        if self.evaluation.run_id != self.run_id:
            raise ValueError("evaluation run_id must match artifact run")
        recovery_check = next(
            check
            for check in self.evaluation.checks
            if check.code == EvaluationCheckCode.RECOVERY_HEALTHY
        )
        if recovery_check.passed != (self.recovery_status == RecoveryStatus.HEALTHY):
            raise ValueError("recovery status must match evaluation")
        return self


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key, value in pairs:
        if key in payload:
            raise ValueError("duplicate JSON object key")
        payload[key] = value
    return payload


def _load_json(text: str) -> Any:
    return json.loads(text, object_pairs_hook=_reject_duplicate_json_keys)


def _safe_runtime_label(value: object) -> str:
    if isinstance(value, str) and _RUNTIME_LABEL_PATTERN.fullmatch(value):
        return value
    return "[redacted]"


class ArtifactWriter:
    def __init__(
        self,
        project_root: Path = PROJECT_ROOT,
        *,
        run_command: RunCommand = subprocess.run,
    ) -> None:
        self._project_root = project_root
        self._run_command = run_command

    def write(self, run: ArtifactRun) -> Path:
        artifact_root: Path | None = None
        temporary: Path | None = None
        temporary_owned = False
        try:
            artifact_root = self._validated_artifact_root()
            final = artifact_root / run.run_id
            self._reject_existing_or_symlink(final, artifact_root)
            temporary = artifact_root / f".{run.run_id}.{uuid4().hex}.tmp"
            temporary.mkdir(exist_ok=False)
            temporary_owned = True
            payloads = self._build_payloads(run)
            self._write_payloads(temporary, payloads)
            self._validate_complete_bundle(temporary, run, payloads)
            self._reject_existing_or_symlink(final, artifact_root)
            temporary.rename(final)
        except Exception:
            if temporary_owned and artifact_root is not None and temporary is not None:
                self._remove_owned_temporary_directory(artifact_root, temporary)
            self._raise_write_error()
        return final

    def _validated_artifact_root(self) -> Path:
        project_root = self._project_root.resolve(strict=True)
        if not project_root.is_dir():
            raise ValueError("project root must be a directory")
        artifact_root = project_root / "artifacts"
        if artifact_root.is_symlink():
            raise ValueError("artifact root must not be a symlink")
        artifact_root.mkdir(parents=True, exist_ok=True)
        if artifact_root.is_symlink() or not artifact_root.is_dir():
            raise ValueError("artifact root must be a directory")
        resolved_root = artifact_root.resolve(strict=True)
        if resolved_root.parent != project_root:
            raise ValueError("artifact root escaped project root")
        return resolved_root

    @staticmethod
    def _reject_existing_or_symlink(final: Path, artifact_root: Path) -> None:
        if final.is_symlink() or final.exists():
            raise ValueError("artifact run already exists")
        if final.resolve(strict=False).parent != artifact_root.resolve(strict=True):
            raise ValueError("artifact run escaped artifact root")

    def _git_state(self) -> tuple[str, bool]:
        revision_result = self._run_command(
            ["git", "-C", str(self._project_root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
        status_result = self._run_command(
            ["git", "-C", str(self._project_root), "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
        revision = revision_result.stdout.strip()
        if _REVISION_PATTERN.fullmatch(revision) is None:
            raise ValueError("git revision is invalid")
        return revision, bool(status_result.stdout)

    def _build_metadata(self, run: ArtifactRun) -> RunMetadata:
        revision, workspace_dirty = self._git_state()
        metrics = run.diagnosis_run.metrics
        safe_provider = _safe_runtime_label(metrics.provider)
        safe_model = _safe_runtime_label(metrics.model)
        safe_metrics = metrics.model_copy(
            update={"provider": safe_provider, "model": safe_model}
        )
        elapsed_ms = int((run.finished_at - run.started_at).total_seconds() * 1000)
        return RunMetadata(
            schema_version="m5.metadata.v1",
            incident_case_id=run.incident_case_id,
            run_id=run.run_id,
            code_revision=revision,
            workspace_dirty=workspace_dirty,
            provider=safe_provider,
            model=safe_model,
            model_base_url=run.model_base_url,
            budget=BudgetSummary(
                model_request_limit=6,
                tool_call_limit=8,
                output_retry_limit=2,
                timeout_seconds=300,
            ),
            prompt_version=SYSTEM_PROMPT_VERSION,
            prompt_sha256=SYSTEM_PROMPT_SHA256,
            started_at=run.started_at,
            finished_at=run.finished_at,
            elapsed_ms=elapsed_ms,
            diagnosis_metrics=safe_metrics,
            evaluation_status=run.evaluation.status,
            recovery_status=run.recovery_status,
            artifact_files=ARTIFACT_FILENAMES,
        )

    def _build_payloads(self, run: ArtifactRun) -> dict[str, str]:
        metadata = self._build_metadata(run)
        trace = "".join(
            TraceEnvelope(
                schema_version="m5.trace.v1",
                sequence=index,
                event=event,
            ).model_dump_json()
            + "\n"
            for index, event in enumerate(run.diagnosis_run.trace, start=1)
        )
        return {
            "metadata.json": metadata.model_dump_json(indent=2) + "\n",
            "trace.jsonl": trace,
            "evidence.json": EvidenceArtifact(
                schema_version="m5.evidence.v1",
                incident_case_id=run.incident_case_id,
                run_id=run.run_id,
                records=run.diagnosis_run.evidence_records,
            ).model_dump_json(indent=2)
            + "\n",
            "diagnosis.json": run.diagnosis_run.diagnosis.model_dump_json(indent=2) + "\n",
            "evaluation.json": run.evaluation.model_dump_json(indent=2) + "\n",
            "report.md": self._render_report(run, metadata),
        }

    @staticmethod
    def _write_payloads(temporary: Path, payloads: dict[str, str]) -> None:
        if tuple(payloads) != ARTIFACT_FILENAMES:
            raise ValueError("payloads must contain the canonical six files")
        for filename, payload in payloads.items():
            path = temporary / filename
            with path.open("x", encoding="utf-8", newline="") as stream:
                stream.write(payload)

    def _render_report(self, run: ArtifactRun, metadata: RunMetadata) -> str:
        source = (
            resources.files("data_incident_gym")
            .joinpath("templates", "report.md.j2")
            .read_text(encoding="utf-8")
        )
        template = Environment(
            undefined=StrictUndefined,
            autoescape=True,
            keep_trailing_newline=True,
        ).from_string(source)
        evidence_by_id = {
            record.evidence_id: record for record in run.diagnosis_run.evidence_records
        }
        cited_evidence = tuple(
            evidence_by_id[evidence_id]
            for evidence_id in run.diagnosis_run.diagnosis.evidence_ids
            if evidence_id in evidence_by_id
        )
        rendered = template.render(
            incident_case_id=run.incident_case_id,
            run_id=run.run_id,
            model=metadata.model,
            provider=metadata.provider,
            model_base_url=metadata.model_base_url,
            code_revision=metadata.code_revision,
            evaluation=run.evaluation,
            diagnosis=run.diagnosis_run.diagnosis,
            cited_evidence=cited_evidence,
            metrics=run.diagnosis_run.metrics,
            recovery_status=run.recovery_status,
        )
        return rendered.rstrip("\n") + "\n"

    def _validate_complete_bundle(
        self,
        temporary: Path,
        run: ArtifactRun,
        payloads: dict[str, str],
    ) -> None:
        entries = tuple(temporary.iterdir())
        if {path.name for path in entries} != set(ARTIFACT_FILENAMES):
            raise ValueError("artifact bundle must contain exactly six files")
        temporary_root = temporary.resolve(strict=True)
        for path in entries:
            if path.is_symlink() or not path.is_file():
                raise ValueError("artifact bundle contains an invalid path")
            if path.resolve(strict=True).parent != temporary_root:
                raise ValueError("artifact bundle path escaped temporary directory")

        texts = {
            filename: (temporary / filename).read_text(encoding="utf-8")
            for filename in ARTIFACT_FILENAMES
        }
        if texts != payloads:
            raise ValueError("artifact bundle changed during validation")

        metadata = RunMetadata.model_validate(_load_json(texts["metadata.json"]))
        trace_envelopes = tuple(
            TraceEnvelope.model_validate(_load_json(line))
            for line in texts["trace.jsonl"].splitlines()
            if line
        )
        evidence = EvidenceArtifact.model_validate(_load_json(texts["evidence.json"]))
        diagnosis = Diagnosis.model_validate(_load_json(texts["diagnosis.json"]))
        evaluation = EvaluationResult.model_validate(_load_json(texts["evaluation.json"]))

        if metadata.incident_case_id != run.incident_case_id or metadata.run_id != run.run_id:
            raise ValueError("metadata identity does not match artifact run")
        if evidence.incident_case_id != run.incident_case_id or evidence.run_id != run.run_id:
            raise ValueError("evidence identity does not match artifact run")
        if diagnosis != run.diagnosis_run.diagnosis:
            raise ValueError("diagnosis changed during artifact validation")
        if evaluation != run.evaluation:
            raise ValueError("evaluation changed during artifact validation")
        if evidence.records != run.diagnosis_run.evidence_records:
            raise ValueError("evidence changed during artifact validation")
        if tuple(item.event for item in trace_envelopes) != run.diagnosis_run.trace:
            raise ValueError("trace changed during artifact validation")
        if tuple(item.sequence for item in trace_envelopes) != tuple(
            range(1, len(run.diagnosis_run.trace) + 1)
        ):
            raise ValueError("trace sequence is not canonical")
        if not texts["report.md"].endswith("\n") or texts["report.md"].endswith("\n\n"):
            raise ValueError("report must end with one newline")

    @staticmethod
    def _remove_owned_temporary_directory(
        artifact_root: Path,
        temporary: Path,
    ) -> None:
        try:
            if (
                temporary.is_symlink()
                or not _RUN_DIRECTORY_PATTERN.fullmatch(temporary.name)
                or temporary.resolve(strict=False).parent != artifact_root.resolve(strict=True)
                or not temporary.is_dir()
            ):
                return
            shutil.rmtree(temporary)
        except OSError:
            return

    @staticmethod
    def _raise_write_error() -> NoReturn:
        error = ArtifactWriteError()
        error.__cause__ = None
        error.__context__ = None
        raise error from None
