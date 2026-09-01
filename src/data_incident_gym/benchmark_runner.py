from __future__ import annotations

import hashlib
import inspect
import json
import os
import re
import subprocess
from collections.abc import Callable, Sequence
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StrictStr,
    model_validator,
)

from data_incident_gym.artifacts import ArtifactRun, ArtifactWriter, RecoveryStatus
from data_incident_gym.benchmark_manifest import (
    BenchmarkManifest,
    verify_manifest,
)
from data_incident_gym.config import PROJECT_ROOT, Settings
from data_incident_gym.diagnosis import (
    KERNEL_STRATEGIES,
    RUN_ID_PATTERN,
    Diagnosis,
    DiagnosisMetrics,
    DiagnosisRunResult,
    DiagnosisStatus,
    DiagnosisTerminalTraceEvent,
    DiagnosticStrategy,
    EvidenceGateTraceEvent,
    KernelStateTraceEvent,
    PolicyIdentity,
)
from data_incident_gym.diagnostic_agent import (
    P1_ROOT_CAUSE_CODES,
    DiagnosisRunner,
    policy_identity_for_strategy,
)
from data_incident_gym.diagnostic_config import DiagnosticSettings
from data_incident_gym.diagnostic_kernel import DiagnosticKernel
from data_incident_gym.doctor import (
    CHECK_ORDER,
    DoctorCheckCode,
    DoctorResult,
    DoctorRunner,
    DoctorStatus,
)
from data_incident_gym.evaluation import (
    DeterministicEvaluator,
    EvaluationApplicability,
    EvaluationCheck,
    EvaluationCheckCode,
    EvaluationResult,
    EvaluationStatus,
)
from data_incident_gym.evaluation_runner import (
    EvaluationAttemptResult,
    EvaluationRunner,
)
from data_incident_gym.fixed_rule import FixedRuleRunner, fixed_rule_policy_identity
from data_incident_gym.lab import IncidentLab
from data_incident_gym.scenarios import load_scenario_spec

_DIGEST_PATTERN = r"^[0-9a-f]{64}$"
_ARTIFACT_PATH_PATTERN = re.compile(r"^artifacts/[0-9a-f]{32}$")
_REASON_CODES = frozenset({"RUN_SETUP_ERROR", "EVALUATION_FAILED"})
_LEDGER_FILENAME = "ledger.jsonl"
_DOCTOR_FILENAME = "doctor.json"
_LOCK_FILENAME = ".lock"


class BenchmarkRunnerError(RuntimeError):
    """Raised when a formal suite cannot be started or resumed safely."""


class BenchmarkLedgerEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["p1.benchmark_ledger.v1"] = "p1.benchmark_ledger.v1"
    manifest_id: StrictStr
    sequence: StrictInt = Field(ge=1)
    run_id: Annotated[StrictStr, Field(pattern=RUN_ID_PATTERN)]
    incident_case_id: StrictStr
    strategy: DiagnosticStrategy
    state: Literal["STARTED", "COMPLETED", "FAILED"]
    recorded_at: datetime
    started_at: datetime
    finished_at: datetime | None = None
    artifact_path: StrictStr
    reason_code: StrictStr | None = None

    @model_validator(mode="after")
    def validate_state(self) -> Self:
        if self.recorded_at.tzinfo is None or self.recorded_at.utcoffset() is None:
            raise ValueError("ledger timestamps must be timezone-aware")
        if self.started_at.tzinfo is None or self.started_at.utcoffset() is None:
            raise ValueError("ledger timestamps must be timezone-aware")
        if self.state == "STARTED":
            if self.finished_at is not None or self.reason_code is not None:
                raise ValueError("STARTED ledger entries cannot have terminal fields")
        elif self.finished_at is None:
            raise ValueError("terminal ledger entries require finished_at")
        elif self.state == "FAILED" and self.reason_code not in _REASON_CODES:
            raise ValueError("failed ledger entries require a fixed reason code")
        elif self.state == "COMPLETED" and self.reason_code is not None:
            raise ValueError("completed ledger entries cannot have a reason code")
        return self

    @classmethod
    def create(
        cls,
        *,
        manifest_id: str,
        sequence: int,
        run_id: str,
        incident_case_id: str,
        strategy: DiagnosticStrategy,
        state: Literal["STARTED", "COMPLETED", "FAILED"],
        now: datetime,
        started_at: datetime,
        reason_code: str | None = None,
    ) -> BenchmarkLedgerEntry:
        return cls(
            manifest_id=manifest_id,
            sequence=sequence,
            run_id=run_id,
            incident_case_id=incident_case_id,
            strategy=strategy,
            state=state,
            recorded_at=now,
            started_at=started_at,
            finished_at=None if state == "STARTED" else now,
            artifact_path=f"artifacts/{run_id}",
            reason_code=reason_code,
        )

class BenchmarkDoctorReceipt(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["p1.benchmark_doctor.v1"] = "p1.benchmark_doctor.v1"
    manifest_id: StrictStr
    manifest_sha256: Annotated[StrictStr, Field(pattern=_DIGEST_PATTERN)]
    implementation_revision: Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{40}$")]
    result_inputs_sha256: Annotated[StrictStr, Field(pattern=_DIGEST_PATTERN)]
    checked_at: datetime
    result: DoctorResult


class BenchmarkRunResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    manifest_id: StrictStr
    status: Literal["COMPLETED", "FAILED"]
    total_cells: StrictInt
    terminal_cells: StrictInt
    completed_cells: StrictInt
    failed_cells: StrictInt
    doctor_status: DoctorStatus
    doctor_path: Path
    ledger_path: Path


DoctorFactory = Callable[[], DoctorRunner]
EvaluationRunnerFactory = Callable[[], EvaluationRunner]
CheckoutVerifier = Callable[[BenchmarkManifest], None]
Clock = Callable[[], datetime]


def _bind_manifest_model_configuration(
    settings: DiagnosticSettings,
    manifest: BenchmarkManifest,
) -> DiagnosticSettings:
    return settings.model_copy(
        update={
            "model_base_url": manifest.model_configuration.base_url,
            "model_name": manifest.model_configuration.model,
        }
    )


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise BenchmarkRunnerError("clock must return an aware datetime")
    return value.astimezone(UTC)


def _failed_doctor() -> DoctorResult:
    checks = tuple(
        DoctorRunner._check(DoctorCheckCode(code), False, "UNAVAILABLE")
        for code in CHECK_ORDER
    )
    return DoctorResult(status=DoctorStatus.FAILED, checks=checks)


def _setup_failure_evaluation(
    incident_case_id: str,
    run_id: str,
    *,
    recovery_succeeded: bool,
) -> EvaluationResult:
    checks = tuple(
        EvaluationCheck(
            code=code,
            applicability=EvaluationApplicability.APPLICABLE,
            passed=code is EvaluationCheckCode.RECOVERY_HEALTHY and recovery_succeeded,
            expected=(
                ("RECOVERY_HEALTHY",)
                if code is EvaluationCheckCode.RECOVERY_HEALTHY
                else ("RUN_SETUP_COMPLETE",)
            ),
            actual=(
                ("HEALTHY",)
                if code is EvaluationCheckCode.RECOVERY_HEALTHY and recovery_succeeded
                else ("RUN_SETUP_ERROR",)
            ),
            reason_code=(
                f"{code.value}_PASSED"
                if code is EvaluationCheckCode.RECOVERY_HEALTHY and recovery_succeeded
                else f"{code.value}_FAILED"
            ),
        )
        for code in EvaluationCheckCode
    )
    return EvaluationResult(
        incident_case_id=incident_case_id,
        run_id=run_id,
        status=EvaluationStatus.FAILED,
        checks=checks,
        failed_check_codes=tuple(
            check.code for check in checks if not check.passed
        ),
        answerability="UNAVAILABLE",
        expected_status="UNAVAILABLE",
    )


def _setup_failure_diagnosis(
    *,
    run_id: str,
    strategy: DiagnosticStrategy,
) -> DiagnosisRunResult:
    identity: PolicyIdentity = (
        fixed_rule_policy_identity()
        if strategy is DiagnosticStrategy.FIXED_RULE
        else policy_identity_for_strategy(strategy)
    )
    diagnosis = Diagnosis(
        status=DiagnosisStatus.MODEL_ERROR,
        run_id=run_id,
        summary="RUN_SETUP_ERROR",
        recommended_actions=("Do not retry this manifest cell.",),
        confidence=0.0,
    )
    trace: list[object] = [
        EvidenceGateTraceEvent(
            event_type="EVIDENCE_GATE",
            reason_code="RUN_SETUP_ERROR",
            accepted=True,
        )
    ]
    kernel_state = None
    if strategy in KERNEL_STRATEGIES:
        kernel = DiagnosticKernel.start(
            run_id=run_id,
            allowed_root_cause_codes=P1_ROOT_CAUSE_CODES,
            model_request_limit=8,
            tool_call_limit=8,
        )
        kernel.terminate_model_error("MODEL_RUNTIME_ERROR")
        kernel_state = kernel.snapshot(model_requests_used=0)
        trace.append(KernelStateTraceEvent(event_type="KERNEL_STATE", state=kernel_state))
    trace.append(
        DiagnosisTerminalTraceEvent(
            event_type="DIAGNOSIS_TERMINAL",
            strategy=strategy,
            status=diagnosis.status,
            evidence_inventory=(),
        )
    )
    return DiagnosisRunResult(
        strategy=strategy,
        policy_identity=identity,
        diagnosis=diagnosis,
        evidence_records=(),
        trace=tuple(trace),
        metrics=DiagnosisMetrics(
            provider="fixed-rule" if strategy is DiagnosticStrategy.FIXED_RULE else "benchmark",
            model="none" if strategy is DiagnosticStrategy.FIXED_RULE else "setup-error",
            model_requests=0,
            input_tokens=0,
            output_tokens=0,
            tool_call_attempts=0,
            successful_tool_calls=0,
            elapsed_ms=0,
        ),
        kernel_state=kernel_state,
    )


class _ExclusiveSuiteLock:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._handle = None

    def __enter__(self) -> _ExclusiveSuiteLock:
        if self._path.is_symlink() or self._path.exists():
            raise BenchmarkRunnerError("benchmark suite is already locked")
        try:
            self._handle = self._path.open("x", encoding="utf-8", newline="\n")
            self._handle.write("locked\n")
            self._handle.flush()
        except OSError as exc:
            raise BenchmarkRunnerError("cannot acquire benchmark suite lock") from exc
        return self

    def __exit__(self, *_: object) -> None:
        if self._handle is not None:
            self._handle.close()
        with suppress(OSError):
            self._path.unlink()


class BenchmarkRunner:
    def __init__(
        self,
        manifest: BenchmarkManifest,
        *,
        project_root: Path = PROJECT_ROOT,
        doctor_factory: DoctorFactory,
        evaluation_runner_factory: EvaluationRunnerFactory,
        artifact_writer: ArtifactWriter,
        clock: Clock = lambda: datetime.now(UTC),
        checkout_verifier: CheckoutVerifier | None = None,
    ) -> None:
        self._manifest = manifest
        self._project_root = project_root
        self._doctor_factory = doctor_factory
        self._evaluation_runner_factory = evaluation_runner_factory
        self._artifact_writer = artifact_writer
        self._clock = clock
        self._checkout_verifier = checkout_verifier or self._verify_checkout

    @classmethod
    def for_project(
        cls,
        manifest: BenchmarkManifest,
        *,
        project_root: Path = PROJECT_ROOT,
        settings: Settings | None = None,
        diagnostic_settings: DiagnosticSettings | None = None,
    ) -> BenchmarkRunner:
        settings = settings or Settings()
        diagnostic_settings = _bind_manifest_model_configuration(
            diagnostic_settings or DiagnosticSettings(),
            manifest,
        )
        lab = IncidentLab(settings, project_root)
        artifact_writer = ArtifactWriter(project_root)
        manifest_sha256 = manifest.digest()

        def evaluation_runner_factory() -> EvaluationRunner:
            def diagnosis_factory(
                run_id: str,
                strategy: DiagnosticStrategy,
            ) -> DiagnosisRunner | FixedRuleRunner:
                if strategy is DiagnosticStrategy.FIXED_RULE:
                    return FixedRuleRunner.for_run(
                        run_id,
                        diagnostic_settings,
                        project_root,
                    )
                return DiagnosisRunner.for_run(
                    run_id,
                    diagnostic_settings,
                    strategy,
                    project_root,
                )

            return EvaluationRunner(
                lab=lab,
                diagnostic_settings=diagnostic_settings,
                diagnosis_factory=diagnosis_factory,
                private_scenario_loader=lambda case_id: load_scenario_spec(case_id, project_root),
                private_verification_loader=lab.verifier.load_verification,
                evaluator=DeterministicEvaluator.evaluate,
                artifact_writer=artifact_writer,
                clock=lambda: datetime.now(UTC),
                benchmark_manifest_sha256=manifest_sha256,
            )

        return cls(
            manifest,
            project_root=project_root,
            doctor_factory=lambda: DoctorRunner.for_project(
                diagnostic_settings,
                project_root,
            ),
            evaluation_runner_factory=evaluation_runner_factory,
            artifact_writer=artifact_writer,
        )

    def _suite_root(self) -> Path:
        project_root = self._project_root.resolve(strict=True)
        artifacts_root = project_root / "artifacts"
        if artifacts_root.is_symlink():
            raise BenchmarkRunnerError("artifacts root must not be a symlink")
        artifacts_root.mkdir(parents=True, exist_ok=True)
        benchmarks_root = artifacts_root / "benchmarks"
        if benchmarks_root.is_symlink():
            raise BenchmarkRunnerError("benchmark suite root must not be a symlink")
        benchmarks_root.mkdir(parents=True, exist_ok=True)
        suite_root = benchmarks_root / self._manifest.manifest_id
        if suite_root.is_symlink():
            raise BenchmarkRunnerError("benchmark suite must not be a symlink")
        suite_root.mkdir(parents=True, exist_ok=True)
        resolved = suite_root.resolve(strict=True)
        if not resolved.is_relative_to(artifacts_root.resolve(strict=True)):
            raise BenchmarkRunnerError("benchmark suite escaped artifacts root")
        return resolved

    def _verify_checkout(self, manifest: BenchmarkManifest) -> None:
        verify_manifest(manifest, project_root=self._project_root)
        revision = self._git_output(["rev-parse", "HEAD"])
        status = self._git_output(["status", "--porcelain"])
        if status:
            raise BenchmarkRunnerError("formal benchmark requires a clean checkout")
        if revision == manifest.implementation_revision:
            return
        ancestor = subprocess.run(
            [
                "git",
                "-C",
                str(self._project_root),
                "merge-base",
                "--is-ancestor",
                manifest.implementation_revision,
                revision,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if ancestor.returncode != 0:
            raise BenchmarkRunnerError("HEAD is not based on manifest implementation revision")
        changed = self._git_output(
            ["diff", "--name-only", f"{manifest.implementation_revision}..{revision}"]
        ).splitlines()
        if changed != ["config/benchmark/p1-formal-v1.json"]:
            raise BenchmarkRunnerError("formal checkout contains paths beyond the manifest")

    def _git_output(self, arguments: Sequence[str]) -> str:
        result = subprocess.run(
            ["git", "-C", str(self._project_root), *arguments],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise BenchmarkRunnerError("git checkout verification failed")
        return result.stdout.strip()

    def _read_ledger(self, path: Path) -> dict[str, BenchmarkLedgerEntry]:
        if path.is_symlink():
            raise BenchmarkRunnerError("benchmark ledger must not be a symlink")
        if not path.exists():
            return {}
        entries: dict[str, BenchmarkLedgerEntry] = {}
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise BenchmarkRunnerError("cannot read benchmark ledger") from exc
        for line in lines:
            if not line.strip():
                continue
            try:
                entry = BenchmarkLedgerEntry.model_validate(json.loads(line))
            except Exception as exc:
                raise BenchmarkRunnerError("benchmark ledger is invalid") from exc
            if entry.manifest_id != self._manifest.manifest_id:
                raise BenchmarkRunnerError("benchmark ledger manifest mismatch")
            if not _ARTIFACT_PATH_PATTERN.fullmatch(entry.artifact_path):
                raise BenchmarkRunnerError("benchmark ledger artifact path is invalid")
            cell = next(
                (item for item in self._manifest.cells if item.run_id == entry.run_id),
                None,
            )
            if cell is None or (
                entry.sequence != cell.sequence
                or entry.incident_case_id != cell.incident_case_id
                or entry.strategy is not cell.strategy
                or entry.artifact_path != f"artifacts/{cell.run_id}"
            ):
                raise BenchmarkRunnerError("benchmark ledger cell identity does not match manifest")
            previous = entries.get(entry.run_id)
            if previous is None and entry.state != "STARTED":
                raise BenchmarkRunnerError(
                    "benchmark ledger terminal entry has no STARTED predecessor"
                )
            if previous is not None and (
                previous.state != "STARTED" or entry.state == "STARTED"
            ):
                raise BenchmarkRunnerError("benchmark ledger contains a repeated terminal cell")
            entries[entry.run_id] = entry
        return entries

    @staticmethod
    def _append_ledger(path: Path, entry: BenchmarkLedgerEntry) -> None:
        if path.is_symlink():
            raise BenchmarkRunnerError("benchmark ledger must not be a symlink")
        try:
            with path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(entry.model_dump_json() + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as exc:
            raise BenchmarkRunnerError("cannot append benchmark ledger") from exc

    def _write_doctor_receipt(
        self,
        path: Path,
        result: DoctorResult,
    ) -> BenchmarkDoctorReceipt:
        receipt = BenchmarkDoctorReceipt(
            manifest_id=self._manifest.manifest_id,
            manifest_sha256=self._manifest.digest(),
            implementation_revision=self._manifest.implementation_revision,
            result_inputs_sha256=_digest(self._manifest.result_inputs.model_dump(mode="json")),
            checked_at=_aware_utc(self._clock()),
            result=result,
        )
        if path.is_symlink() or path.exists():
            try:
                existing = BenchmarkDoctorReceipt.model_validate(
                    json.loads(path.read_text(encoding="utf-8"))
                )
            except Exception as exc:
                raise BenchmarkRunnerError("existing doctor receipt is invalid") from exc
            if existing != receipt:
                raise BenchmarkRunnerError("doctor receipt already exists for another run")
            return existing
        try:
            with path.open("x", encoding="utf-8", newline="\n") as handle:
                handle.write(receipt.model_dump_json(indent=2) + "\n")
        except OSError as exc:
            raise BenchmarkRunnerError("cannot write doctor receipt") from exc
        return receipt

    async def _doctor(self, path: Path) -> DoctorResult:
        if path.exists() and not path.is_symlink():
            try:
                receipt = BenchmarkDoctorReceipt.model_validate(
                    json.loads(path.read_text(encoding="utf-8"))
                )
            except Exception as exc:
                raise BenchmarkRunnerError("existing doctor receipt is invalid") from exc
            if (
                receipt.manifest_id != self._manifest.manifest_id
                or receipt.manifest_sha256 != self._manifest.digest()
                or receipt.implementation_revision != self._manifest.implementation_revision
            ):
                raise BenchmarkRunnerError("doctor receipt does not match manifest")
            return receipt.result
        doctor = self._doctor_factory()
        try:
            result = doctor.run()
            if inspect.isawaitable(result):
                result = await result
            if not isinstance(result, DoctorResult):
                raise TypeError("doctor returned an invalid result")
        except Exception:
            result = _failed_doctor()
        self._write_doctor_receipt(path, result)
        return result

    def _materialize_setup_error(
        self,
        cell: object,
        *,
        started_at: datetime,
        recovery_succeeded: bool = False,
    ) -> Path:
        run = ArtifactRun(
            incident_case_id=cell.incident_case_id,
            run_id=cell.run_id,
            started_at=started_at,
            finished_at=_aware_utc(self._clock()),
            recovery_status=(
                RecoveryStatus.HEALTHY if recovery_succeeded else RecoveryStatus.FAILED
            ),
            model_base_url=self._manifest.model_configuration.base_url,
            benchmark_manifest_sha256=self._manifest.digest(),
            diagnosis_run=_setup_failure_diagnosis(
                run_id=cell.run_id,
                strategy=cell.strategy,
            ),
            evaluation=_setup_failure_evaluation(
                cell.incident_case_id,
                cell.run_id,
                recovery_succeeded=recovery_succeeded,
            ),
        )
        return self._artifact_writer.write(run)

    def _materialize_interrupted_cell(
        self,
        cell: object,
        *,
        started_at: datetime,
        ledger_path: Path,
        ledger: dict[str, BenchmarkLedgerEntry],
    ) -> None:
        self._materialize_setup_error(cell, started_at=started_at)
        terminal = BenchmarkLedgerEntry.create(
            manifest_id=self._manifest.manifest_id,
            sequence=cell.sequence,
            run_id=cell.run_id,
            incident_case_id=cell.incident_case_id,
            strategy=cell.strategy,
            state="FAILED",
            now=_aware_utc(self._clock()),
            started_at=started_at,
            reason_code="RUN_SETUP_ERROR",
        )
        self._append_ledger(ledger_path, terminal)
        ledger[cell.run_id] = terminal

    async def run(self) -> BenchmarkRunResult:
        self._checkout_verifier(self._manifest)
        suite_root = self._suite_root()
        ledger_path = suite_root / _LEDGER_FILENAME
        doctor_path = suite_root / _DOCTOR_FILENAME
        lock_path = suite_root / _LOCK_FILENAME
        with _ExclusiveSuiteLock(lock_path):
            ledger = self._read_ledger(ledger_path)
            doctor_result = await self._doctor(doctor_path)
            if doctor_result.status is DoctorStatus.FAILED:
                for cell in self._manifest.cells:
                    previous = ledger.get(cell.run_id)
                    if previous is not None and previous.state == "STARTED":
                        self._materialize_interrupted_cell(
                            cell,
                            started_at=previous.started_at,
                            ledger_path=ledger_path,
                            ledger=ledger,
                        )
                terminal = tuple(entry for entry in ledger.values() if entry.state != "STARTED")
                return BenchmarkRunResult(
                    manifest_id=self._manifest.manifest_id,
                    status="FAILED",
                    total_cells=len(self._manifest.cells),
                    terminal_cells=len(terminal),
                    completed_cells=sum(entry.state == "COMPLETED" for entry in terminal),
                    failed_cells=sum(entry.state == "FAILED" for entry in terminal),
                    doctor_status=doctor_result.status,
                    doctor_path=doctor_path,
                    ledger_path=ledger_path,
                )

            for cell in self._manifest.cells:
                previous = ledger.get(cell.run_id)
                if previous is not None and previous.state in {"COMPLETED", "FAILED"}:
                    continue
                if previous is not None and previous.state == "STARTED":
                    self._materialize_interrupted_cell(
                        cell,
                        started_at=previous.started_at,
                        ledger_path=ledger_path,
                        ledger=ledger,
                    )
                    continue

                started_at = _aware_utc(self._clock())
                started = BenchmarkLedgerEntry.create(
                    manifest_id=self._manifest.manifest_id,
                    sequence=cell.sequence,
                    run_id=cell.run_id,
                    incident_case_id=cell.incident_case_id,
                    strategy=cell.strategy,
                    state="STARTED",
                    now=started_at,
                    started_at=started_at,
                )
                self._append_ledger(ledger_path, started)
                ledger[cell.run_id] = started
                try:
                    attempt: EvaluationAttemptResult = await self._evaluation_runner_factory().run(
                        cell.incident_case_id,
                        cell.strategy,
                        run_id=cell.run_id,
                    )
                    state: Literal["COMPLETED", "FAILED"] = (
                        "COMPLETED" if attempt.status is EvaluationStatus.PASSED else "FAILED"
                    )
                    reason = None if state == "COMPLETED" else "EVALUATION_FAILED"
                except Exception:
                    self._materialize_setup_error(
                        cell,
                        started_at=started_at,
                    )
                    state = "FAILED"
                    reason = "RUN_SETUP_ERROR"
                terminal = BenchmarkLedgerEntry.create(
                    manifest_id=self._manifest.manifest_id,
                    sequence=cell.sequence,
                    run_id=cell.run_id,
                    incident_case_id=cell.incident_case_id,
                    strategy=cell.strategy,
                    state=state,
                    now=_aware_utc(self._clock()),
                    started_at=started_at,
                    reason_code=reason,
                )
                self._append_ledger(ledger_path, terminal)
                ledger[cell.run_id] = terminal

            terminal = tuple(entry for entry in ledger.values() if entry.state != "STARTED")
            completed = sum(entry.state == "COMPLETED" for entry in terminal)
            failed = sum(entry.state == "FAILED" for entry in terminal)
            status = (
                "COMPLETED"
                if len(terminal) == len(self._manifest.cells) and failed == 0
                else "FAILED"
            )
            return BenchmarkRunResult(
                manifest_id=self._manifest.manifest_id,
                status=status,
                total_cells=len(self._manifest.cells),
                terminal_cells=len(terminal),
                completed_cells=completed,
                failed_cells=failed,
                doctor_status=doctor_result.status,
                doctor_path=doctor_path,
                ledger_path=ledger_path,
            )


__all__ = [
    "BenchmarkDoctorReceipt",
    "BenchmarkLedgerEntry",
    "BenchmarkRunResult",
    "BenchmarkRunner",
    "BenchmarkRunnerError",
]
