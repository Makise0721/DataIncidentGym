from __future__ import annotations

import json
import re
import shutil
import subprocess
from collections.abc import Callable
from datetime import datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Literal, NoReturn
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
    KERNEL_STRATEGIES,
    Diagnosis,
    DiagnosisMetrics,
    DiagnosisRunResult,
    DiagnosticStrategy,
    KernelStateTraceEvent,
    PolicyIdentity,
    TraceEvent,
)
from data_incident_gym.diagnostic_agent import (
    MODEL_REQUEST_LIMIT,
    OUTPUT_RETRY_LIMIT,
    TIMEOUT_SECONDS,
    TOOL_CALL_LIMIT,
)
from data_incident_gym.evaluation import EvaluationResult, EvaluationStatus
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
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
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

    model_request_limit: Literal[8]
    tool_call_limit: Literal[8]
    output_retry_limit: Literal[2]
    timeout_seconds: Literal[300]


class TraceEnvelope(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["p1.trace.v1"]
    sequence: Annotated[StrictInt, Field(ge=1)]
    event: TraceEvent


class EvidenceArtifact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["p1.evidence.v1"]
    incident_case_id: StrictStr
    run_id: StrictStr
    records: tuple[EvidenceRecord, ...]

    @model_validator(mode="after")
    def validate_record_scope(self) -> EvidenceArtifact:
        if any(record.run_id != self.run_id for record in self.records):
            raise ValueError("evidence records must match artifact run")
        return self


class RunMetadata(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["p1.metadata.v1"]
    incident_case_id: StrictStr
    run_id: StrictStr
    strategy: DiagnosticStrategy
    code_revision: Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{40}$")]
    workspace_dirty: StrictBool
    provider: StrictStr
    model: StrictStr
    model_base_url: StrictStr
    budget: BudgetSummary
    base_prompt_version: StrictStr
    base_prompt_sha256: Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{64}$")]
    strategy_prompt_version: StrictStr
    strategy_prompt_sha256: Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{64}$")]
    controller_protocol_version: StrictStr
    controller_protocol_sha256: Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{64}$")]
    tool_schema_sha256: Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{64}$")]
    benchmark_manifest_sha256: Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{64}$")] | None
    variant_role: StrictStr | None
    answerability: StrictStr
    expected_status: StrictStr
    started_at: Annotated[datetime, BeforeValidator(_strict_aware_datetime)]
    finished_at: Annotated[datetime, BeforeValidator(_strict_aware_datetime)]
    elapsed_ms: Annotated[StrictInt, Field(ge=0)]
    diagnosis_metrics: DiagnosisMetrics
    evaluation_status: EvaluationStatus
    recovery_status: RecoveryStatus
    artifact_files: tuple[StrictStr, ...]

    @model_validator(mode="after")
    def validate_contract(self) -> RunMetadata:
        if self.finished_at < self.started_at:
            raise ValueError("finished_at must not precede started_at")
        expected_elapsed = int((self.finished_at - self.started_at).total_seconds() * 1000)
        if self.elapsed_ms != expected_elapsed:
            raise ValueError("elapsed_ms must match timestamps")
        if self.artifact_files != ARTIFACT_FILENAMES:
            raise ValueError("artifact_files must match the canonical six files")
        return self


class ArtifactRun(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    incident_case_id: StrictStr
    run_id: StrictStr
    started_at: Annotated[datetime, BeforeValidator(_strict_aware_datetime)]
    finished_at: Annotated[datetime, BeforeValidator(_strict_aware_datetime)]
    recovery_status: RecoveryStatus
    model_base_url: StrictStr
    benchmark_manifest_sha256: Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{64}$")] | None = None
    diagnosis_run: DiagnosisRunResult
    evaluation: EvaluationResult

    @model_validator(mode="after")
    def validate_cross_file_identity(self) -> ArtifactRun:
        parsed = urlsplit(self.model_base_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("model_base_url must be a safe HTTP(S) URL")
        if self.finished_at < self.started_at:
            raise ValueError("finished_at must not precede started_at")
        if self.diagnosis_run.diagnosis.run_id != self.run_id:
            raise ValueError("diagnosis run_id must match artifact run")
        if self.evaluation.incident_case_id != self.incident_case_id:
            raise ValueError("evaluation case must match artifact run")
        if self.evaluation.run_id != self.run_id:
            raise ValueError("evaluation run_id must match artifact run")
        recovery_check = next(
            check
            for check in self.evaluation.checks
            if check.code.value == "RECOVERY_HEALTHY"
        )
        if recovery_check.passed != (self.recovery_status is RecoveryStatus.HEALTHY):
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
        owned = False
        try:
            artifact_root = self._validated_artifact_root()
            final = artifact_root / run.run_id
            self._reject_existing_or_symlink(final, artifact_root)
            temporary = artifact_root / f".{run.run_id}.{uuid4().hex}.tmp"
            temporary.mkdir(exist_ok=False)
            owned = True
            payloads = self._build_payloads(run)
            self._write_payloads(temporary, payloads)
            self._validate_complete_bundle(temporary, run, payloads)
            self._reject_existing_or_symlink(final, artifact_root)
            temporary.rename(final)
            return final
        except Exception:
            if owned and artifact_root is not None and temporary is not None:
                self._remove_owned_temporary_directory(artifact_root, temporary)
            self._raise_write_error()

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
        resolved = artifact_root.resolve(strict=True)
        if resolved.parent != project_root:
            raise ValueError("artifact root escaped project root")
        return resolved

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
        diagnosis_run = run.diagnosis_run
        identity: PolicyIdentity = diagnosis_run.policy_identity
        metrics = diagnosis_run.metrics
        provider = _safe_runtime_label(metrics.provider)
        model = _safe_runtime_label(metrics.model)
        safe_metrics = metrics.model_copy(update={"provider": provider, "model": model})
        elapsed_ms = int((run.finished_at - run.started_at).total_seconds() * 1000)
        return RunMetadata(
            schema_version="p1.metadata.v1",
            incident_case_id=run.incident_case_id,
            run_id=run.run_id,
            strategy=diagnosis_run.strategy,
            code_revision=revision,
            workspace_dirty=workspace_dirty,
            provider=provider,
            model=model,
            model_base_url=run.model_base_url,
            budget=BudgetSummary(
                model_request_limit=MODEL_REQUEST_LIMIT,
                tool_call_limit=TOOL_CALL_LIMIT,
                output_retry_limit=OUTPUT_RETRY_LIMIT,
                timeout_seconds=TIMEOUT_SECONDS,
            ),
            base_prompt_version=identity.base_prompt_version,
            base_prompt_sha256=identity.base_prompt_sha256,
            strategy_prompt_version=identity.strategy_prompt_version,
            strategy_prompt_sha256=identity.strategy_prompt_sha256,
            controller_protocol_version=identity.controller_protocol_version,
            controller_protocol_sha256=identity.controller_protocol_sha256,
            tool_schema_sha256=identity.tool_schema_sha256,
            benchmark_manifest_sha256=run.benchmark_manifest_sha256,
            variant_role=run.evaluation.variant_role,
            answerability=run.evaluation.answerability,
            expected_status=run.evaluation.expected_status,
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
                schema_version="p1.trace.v1",
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
                schema_version="p1.evidence.v1",
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
            with (temporary / filename).open("x", encoding="utf-8", newline="") as stream:
                stream.write(payload)

    def _render_report(self, run: ArtifactRun, metadata: RunMetadata) -> str:
        source = (
            Path(__file__).parent / "templates" / "report.md.j2"
        ).read_text(encoding="utf-8")
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
        kernel_state = run.diagnosis_run.kernel_state
        verdict_by_id = {
            assessment.hypothesis_id: assessment.verdict.value
            for assessment in (kernel_state.assessments if kernel_state is not None else ())
        }
        rendered = template.render(
            incident_case_id=run.incident_case_id,
            run_id=run.run_id,
            strategy=run.diagnosis_run.strategy,
            model=metadata.model,
            provider=metadata.provider,
            model_base_url=metadata.model_base_url,
            code_revision=metadata.code_revision,
            evaluation=run.evaluation,
            diagnosis=run.diagnosis_run.diagnosis,
            cited_evidence=cited_evidence,
            metrics=run.diagnosis_run.metrics,
            recovery_status=run.recovery_status,
            kernel_state=kernel_state,
            verdict_by_id=verdict_by_id,
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
            invalid_path = (
                path.is_symlink()
                or not path.is_file()
                or path.resolve(strict=True).parent != temporary_root
            )
            if invalid_path:
                raise ValueError("artifact bundle contains an invalid path")
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
        if (
            metadata.incident_case_id != run.incident_case_id
            or metadata.run_id != run.run_id
            or metadata.strategy is not run.diagnosis_run.strategy
        ):
            raise ValueError("metadata identity does not match artifact run")
        if evidence.incident_case_id != run.incident_case_id or evidence.run_id != run.run_id:
            raise ValueError("evidence identity does not match artifact run")
        if diagnosis != run.diagnosis_run.diagnosis or evaluation != run.evaluation:
            raise ValueError("structured artifact changed during validation")
        if evidence.records != run.diagnosis_run.evidence_records:
            raise ValueError("evidence changed during artifact validation")
        persisted_trace = tuple(
            item.event.model_dump(mode="json") for item in trace_envelopes
        )
        expected_trace = tuple(
            event.model_dump(mode="json") for event in run.diagnosis_run.trace
        )
        if persisted_trace != expected_trace:
            raise ValueError("trace changed during artifact validation")
        if not trace_envelopes or trace_envelopes[-1].event.event_type != "DIAGNOSIS_TERMINAL":
            raise ValueError("trace must end with diagnosis terminal")
        kernel_events = tuple(
            item.event for item in trace_envelopes if isinstance(item.event, KernelStateTraceEvent)
        )
        if run.diagnosis_run.strategy in KERNEL_STRATEGIES:
            if (
                len(trace_envelopes) < 2
                or len(kernel_events) != 1
                or trace_envelopes[-2].event != kernel_events[0]
            ):
                raise ValueError("Kernel trace must end with Kernel state before terminal")
        elif kernel_events or run.diagnosis_run.kernel_state is not None:
            raise ValueError("Static trace cannot contain Kernel state")
        if tuple(item.sequence for item in trace_envelopes) != tuple(
            range(1, len(run.diagnosis_run.trace) + 1)
        ):
            raise ValueError("trace sequence is not canonical")
        if not texts["report.md"].endswith("\n") or texts["report.md"].endswith("\n\n"):
            raise ValueError("report must end with one newline")

    @staticmethod
    def _remove_owned_temporary_directory(artifact_root: Path, temporary: Path) -> None:
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
        raise ArtifactWriteError() from None


__all__ = [
    "ARTIFACT_FILENAMES",
    "ArtifactRun",
    "ArtifactWriteError",
    "ArtifactWriter",
    "BudgetSummary",
    "EvidenceArtifact",
    "RecoveryStatus",
    "RunMetadata",
    "TraceEnvelope",
]
