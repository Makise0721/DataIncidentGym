from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from data_incident_gym.artifacts import ARTIFACT_FILENAMES, ArtifactRun, ArtifactWriter
from data_incident_gym.benchmark_manifest import build_manifest
from data_incident_gym.benchmark_runner import BenchmarkLedgerEntry, BenchmarkRunner
from data_incident_gym.diagnosis import (
    Diagnosis,
    DiagnosisMetrics,
    DiagnosisRunResult,
    DiagnosisStatus,
    DiagnosisTerminalTraceEvent,
    DiagnosticStrategy,
    EvidenceGateTraceEvent,
    KernelStateTraceEvent,
    UnresolvedEvidence,
)
from data_incident_gym.diagnostic_agent import P1_ROOT_CAUSE_CODES, policy_identity_for_strategy
from data_incident_gym.diagnostic_kernel import DiagnosticKernel
from data_incident_gym.doctor import (
    CHECK_ORDER,
    DoctorCheckCode,
    DoctorResult,
    DoctorRunner,
    DoctorStatus,
)
from data_incident_gym.evaluation import (
    EvaluationApplicability,
    EvaluationCheck,
    EvaluationCheckCode,
    EvaluationResult,
    EvaluationStatus,
)
from data_incident_gym.evaluation_runner import EvaluationAttemptResult
from data_incident_gym.fixed_rule import fixed_rule_policy_identity

NOW = datetime(2026, 9, 2, tzinfo=UTC)


def _doctor() -> DoctorResult:
    return DoctorResult(
        status=DoctorStatus.PASSED,
        checks=tuple(
            DoctorRunner._check(DoctorCheckCode(code), True, "OK")
            for code in CHECK_ORDER
        ),
    )


def _evaluation(case_id: str, run_id: str) -> EvaluationResult:
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
        incident_case_id=case_id,
        run_id=run_id,
        status=EvaluationStatus.PASSED,
        checks=checks,
        failed_check_codes=(),
        answerability="UNAVAILABLE",
        expected_status="UNAVAILABLE",
    )


def _git_command(argv: list[str], **_: object) -> subprocess.CompletedProcess[str]:
    stdout = "a" * 40 + "\n" if argv[-2:] == ["rev-parse", "HEAD"] else ""
    return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr="")


class _SyntheticEvaluationRunner:
    def __init__(
        self,
        project_root: Path,
        manifest_sha256: str,
        writer: ArtifactWriter,
        calls: list[str],
    ) -> None:
        self._project_root = project_root
        self._manifest_sha256 = manifest_sha256
        self._writer = writer
        self._calls = calls

    async def run(
        self,
        incident_case_id: str,
        strategy: DiagnosticStrategy,
        *,
        run_id: str,
    ) -> EvaluationAttemptResult:
        self._calls.append(run_id)
        fixed_rule = strategy is DiagnosticStrategy.FIXED_RULE
        diagnosis = Diagnosis(
            status=(
                DiagnosisStatus.INSUFFICIENT_EVIDENCE
                if fixed_rule
                else DiagnosisStatus.MODEL_ERROR
            ),
            run_id=run_id,
            summary=(
                "synthetic deterministic result"
                if fixed_rule
                else "MODEL_RUNTIME_ERROR"
            ),
            unresolved_evidence=(
                (
                    UnresolvedEvidence(
                        evidence_kind="TRANSFORMATION_DEFINITION",
                        subject="synthetic",
                        reason_code="NOT_OBSERVABLE",
                    ),
                )
                if fixed_rule
                else ()
            ),
            recommended_actions=("No action.",),
            confidence=0.0,
        )
        policy = (
            fixed_rule_policy_identity()
            if strategy is DiagnosticStrategy.FIXED_RULE
            else policy_identity_for_strategy(strategy)
        )
        metrics = DiagnosisMetrics(
            provider="fixed-rule" if strategy is DiagnosticStrategy.FIXED_RULE else "synthetic",
            model="none" if strategy is DiagnosticStrategy.FIXED_RULE else "synthetic",
            model_requests=0,
            input_tokens=0,
            output_tokens=0,
            tool_call_attempts=0,
            successful_tool_calls=0,
            elapsed_ms=1000,
        )
        trace: list[object] = [
            EvidenceGateTraceEvent(
                event_type="EVIDENCE_GATE",
                reason_code=diagnosis.status.value,
                accepted=True,
            )
        ]
        kernel_state = None
        if strategy in {
            DiagnosticStrategy.DIAGNOSTIC_KERNEL,
            DiagnosticStrategy.KERNEL_NO_LINEAGE,
            DiagnosticStrategy.KERNEL_NO_SCHEMA,
        }:
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
        diagnosis_run = DiagnosisRunResult(
            strategy=strategy,
            policy_identity=policy,
            diagnosis=diagnosis,
            evidence_records=(),
            trace=tuple(trace),
            metrics=metrics,
            kernel_state=kernel_state,
        )
        evaluation = _evaluation(incident_case_id, run_id)
        artifact_dir = self._writer.write(
            ArtifactRun(
                incident_case_id=incident_case_id,
                run_id=run_id,
                started_at=NOW,
                finished_at=NOW + timedelta(seconds=1),
                recovery_status="HEALTHY",
                model_base_url="https://synthetic.example/v1",
                benchmark_manifest_sha256=self._manifest_sha256,
                diagnosis_run=diagnosis_run,
                evaluation=evaluation,
            )
        )
        return EvaluationAttemptResult(
            incident_case_id=incident_case_id,
            run_id=run_id,
            status=EvaluationStatus.PASSED,
            evaluation=evaluation,
            artifact_dir=artifact_dir,
        )


def _runner(
    project_root: Path,
    calls: list[str],
) -> tuple[BenchmarkRunner, tuple]:
    manifest = build_manifest("a" * 40)
    writer = ArtifactWriter(project_root, run_command=_git_command)
    evaluation_runner = _SyntheticEvaluationRunner(project_root, manifest.digest(), writer, calls)

    class _Doctor:
        async def run(self) -> DoctorResult:
            return _doctor()

    runner = BenchmarkRunner(
        manifest,
        project_root=project_root,
        doctor_factory=_Doctor,
        evaluation_runner_factory=lambda: evaluation_runner,  # type: ignore[arg-type]
        artifact_writer=writer,
        clock=lambda: NOW,
        checkout_verifier=lambda _manifest: None,
        checkout_revision_reader=lambda: "a" * 40,
    )
    return runner, manifest.cells


def _artifact_dirs(project_root: Path) -> list[Path]:
    return sorted(
        path
        for path in (project_root / "artifacts").iterdir()
        if path.is_dir() and len(path.name) == 32
    )


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_synthetic_benchmark_runs_all_106_cells_once(tmp_path: Path) -> None:
    calls: list[str] = []
    runner, cells = _runner(tmp_path, calls)

    await runner.preflight()
    result = await runner.run()

    assert result.status == "COMPLETED"
    assert result.terminal_cells == result.completed_cells == 106
    assert result.failed_cells == 0
    assert calls == [cell.run_id for cell in cells]
    ledger = tmp_path / "artifacts" / "benchmarks" / "p1-formal-v1" / "ledger.jsonl"
    assert len(ledger.read_text(encoding="utf-8").splitlines()) == 212
    assert {path.name for path in _artifact_dirs(tmp_path)} == {cell.run_id for cell in cells}
    for path in _artifact_dirs(tmp_path):
        assert {child.name for child in path.iterdir()} == set(ARTIFACT_FILENAMES)
    fixed_cells = [cell for cell in cells if cell.strategy is DiagnosticStrategy.FIXED_RULE]
    for cell in fixed_cells:
        metadata = json.loads(
            (tmp_path / "artifacts" / cell.run_id / "metadata.json").read_text(encoding="utf-8")
        )
        assert metadata["provider"] == "fixed-rule"
        assert metadata["model"] == "none"
        assert metadata["diagnosis_metrics"]["model_requests"] == 0
        trace = [
            json.loads(line)["event"]
            for line in (tmp_path / "artifacts" / cell.run_id / "trace.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        assert not any(event.get("event_type") == "TOOL_CALL" for event in trace)


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_synthetic_resume_materializes_started_once_and_runs_remaining_cells(
    tmp_path: Path,
) -> None:
    calls: list[str] = []
    runner, cells = _runner(tmp_path, calls)
    await runner.preflight()
    suite_root = runner._suite_root()
    first = cells[0]
    started = BenchmarkLedgerEntry.create(
        manifest_id="p1-formal-v1",
        sequence=first.sequence,
        run_id=first.run_id,
        incident_case_id=first.incident_case_id,
        strategy=first.strategy,
        state="STARTED",
        now=NOW,
        started_at=NOW,
    )
    runner._append_ledger(suite_root / "ledger.jsonl", started)

    result = await runner.run()

    assert result.status == "FAILED"
    assert result.terminal_cells == 106
    assert result.completed_cells == 105
    assert result.failed_cells == 1
    assert calls == [cell.run_id for cell in cells[1:]]
    assert len((suite_root / "ledger.jsonl").read_text(encoding="utf-8").splitlines()) == 212
    assert len(_artifact_dirs(tmp_path)) == 106


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_synthetic_resume_preserves_started_artifact_without_model_retry(
    tmp_path: Path,
) -> None:
    calls: list[str] = []
    runner, cells = _runner(tmp_path, calls)
    await runner.preflight()
    first = cells[0]
    await runner._evaluation_runner_factory().run(
        first.incident_case_id,
        first.strategy,
        run_id=first.run_id,
    )
    calls.clear()
    started = BenchmarkLedgerEntry.create(
        manifest_id="p1-formal-v1",
        sequence=first.sequence,
        run_id=first.run_id,
        incident_case_id=first.incident_case_id,
        strategy=first.strategy,
        state="STARTED",
        now=NOW,
        started_at=NOW,
    )
    runner._append_ledger(runner._suite_root() / "ledger.jsonl", started)

    result = await runner.run()

    assert result.status == "FAILED"
    assert result.failed_cells == 1
    assert result.completed_cells == 105
    assert calls == [cell.run_id for cell in cells[1:]]
    artifact = tmp_path / "artifacts" / first.run_id
    preserved = (
        runner._suite_root() / "interrupted-artifacts" / first.run_id
    )
    assert {path.name for path in artifact.iterdir()} == set(ARTIFACT_FILENAMES)
    assert {path.name for path in preserved.iterdir()} == set(ARTIFACT_FILENAMES)
    diagnosis = json.loads((artifact / "diagnosis.json").read_text(encoding="utf-8"))
    assert diagnosis["status"] == "MODEL_ERROR"
    assert diagnosis["summary"] == "RUN_SETUP_ERROR"
