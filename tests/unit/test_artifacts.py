from __future__ import annotations

import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import pytest

from data_incident_gym.artifacts import (
    ARTIFACT_FILENAMES,
    ArtifactRun,
    ArtifactWriteError,
    ArtifactWriter,
    RecoveryStatus,
)
from data_incident_gym.diagnosis import (
    Diagnosis,
    DiagnosisMetrics,
    DiagnosisRunResult,
    DiagnosisStatus,
    DiagnosisTerminalTraceEvent,
    DiagnosticStrategy,
    EvidenceGateTraceEvent,
    PolicyIdentity,
)
from data_incident_gym.evaluation import (
    EvaluationApplicability,
    EvaluationCheck,
    EvaluationCheckCode,
    EvaluationResult,
    EvaluationStatus,
)

RUN_ID = "a" * 32
MODEL_BASE_URL = "http://127.0.0.1:11434/v1"


def _diagnosis_run() -> DiagnosisRunResult:
    strategy = DiagnosticStrategy.STATIC_SKILL
    diagnosis = Diagnosis(
        status=DiagnosisStatus.MODEL_ERROR,
        run_id=RUN_ID,
        summary="MODEL_RUNTIME_ERROR",
        confidence=0.0,
    )
    return DiagnosisRunResult(
        strategy=strategy,
        policy_identity=PolicyIdentity(
            strategy=strategy,
            base_prompt_version="p1.base.v1",
            base_prompt_sha256="b" * 64,
            strategy_prompt_version="p1.static.v1",
            strategy_prompt_sha256="c" * 64,
            controller_protocol_version="p1.controller.v1",
            controller_protocol_sha256="d" * 64,
            tool_schema_sha256="e" * 64,
        ),
        diagnosis=diagnosis,
        evidence_records=(),
        trace=(
            EvidenceGateTraceEvent(
                event_type="EVIDENCE_GATE",
                reason_code="MODEL_RUNTIME_ERROR",
                accepted=True,
            ),
            DiagnosisTerminalTraceEvent(
                event_type="DIAGNOSIS_TERMINAL",
                strategy=strategy,
                status=diagnosis.status,
                evidence_inventory=(),
            ),
        ),
        metrics=DiagnosisMetrics(
            provider="synthetic",
            model="synthetic-model",
            model_requests=1,
            input_tokens=0,
            output_tokens=0,
            tool_call_attempts=0,
            successful_tool_calls=0,
            elapsed_ms=1,
        ),
    )


def _evaluation() -> EvaluationResult:
    checks = tuple(
        EvaluationCheck(
            code=code,
            applicability=EvaluationApplicability.NOT_APPLICABLE,
            passed=True,
            expected=("NOT_APPLICABLE",),
            actual=("NOT_APPLICABLE",),
            reason_code="NOT_APPLICABLE",
        )
        for code in EvaluationCheckCode
    )
    return EvaluationResult(
        incident_case_id="schema_type_change_payment_amount",
        run_id=RUN_ID,
        status=EvaluationStatus.PASSED,
        checks=checks,
        failed_check_codes=(),
        answerability="CONFIRMABLE",
        expected_status="CONFIRMED",
    )


def _artifact_run() -> ArtifactRun:
    started = datetime(2026, 8, 30, tzinfo=UTC)
    return ArtifactRun(
        incident_case_id="schema_type_change_payment_amount",
        run_id=RUN_ID,
        started_at=started,
        finished_at=started.replace(microsecond=1000),
        recovery_status=RecoveryStatus.HEALTHY,
        model_base_url=MODEL_BASE_URL,
        diagnosis_run=_diagnosis_run(),
        evaluation=_evaluation(),
    )


def _git_command(argv: list[str], **_: object) -> subprocess.CompletedProcess[str]:
    stdout = "1" * 40 + "\n" if argv[-2:] == ["rev-parse", "HEAD"] else ""
    return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr="")


def test_static_run_writes_exactly_six_files_without_kernel_state(tmp_path: Path) -> None:
    output = ArtifactWriter(tmp_path, run_command=_git_command).write(_artifact_run())

    assert tuple(path.name for path in sorted(output.iterdir())) == tuple(
        sorted(ARTIFACT_FILENAMES)
    )
    report = (output / "report.md").read_text(encoding="utf-8")
    assert "STATIC_SKILL" in report
    assert "Kernel 调查状态" not in report


def test_writer_refuses_overwrite_and_keeps_the_original_bundle(tmp_path: Path) -> None:
    writer = ArtifactWriter(tmp_path, run_command=_git_command)
    output = writer.write(_artifact_run())
    original = {path.name: path.read_bytes() for path in output.iterdir()}

    with pytest.raises(ArtifactWriteError):
        writer.write(_artifact_run())

    assert {path.name: path.read_bytes() for path in output.iterdir()} == original


def test_validation_failure_never_publishes_partial_bundle(tmp_path: Path, monkeypatch) -> None:
    writer = ArtifactWriter(tmp_path, run_command=_git_command)
    monkeypatch.setattr(
        writer,
        "_validate_complete_bundle",
        lambda *_: (_ for _ in ()).throw(ValueError("synthetic validation failure")),
    )

    with pytest.raises(ArtifactWriteError):
        writer.write(_artifact_run())

    artifact_root = tmp_path / "artifacts"
    assert not (artifact_root / RUN_ID).exists()
    assert not tuple(artifact_root.glob(f".{RUN_ID}.*.tmp"))


def test_competing_writers_publish_at_most_one_complete_bundle(tmp_path: Path) -> None:
    def write() -> Path:
        return ArtifactWriter(tmp_path, run_command=_git_command).write(_artifact_run())

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = tuple(executor.submit(write) for _ in range(2))
    results = []
    errors = []
    for future in futures:
        try:
            results.append(future.result())
        except ArtifactWriteError as error:
            errors.append(error)

    assert len(results) == len(errors) == 1
    assert {path.name for path in results[0].iterdir()} == set(ARTIFACT_FILENAMES)
