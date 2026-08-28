from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from data_incident_gym.artifacts import ArtifactWriteError, RecoveryStatus
from data_incident_gym.diagnosis import (
    Diagnosis,
    DiagnosisMetrics,
    DiagnosisRunResult,
    DiagnosisStatus,
)
from data_incident_gym.evaluation import (
    EvaluationCheck,
    EvaluationCheckCode,
    EvaluationResult,
    EvaluationStatus,
)
from data_incident_gym.evaluation_runner import (
    EvaluationAttemptResult,
    EvaluationRunner,
    EvaluationWorkflowError,
)
from data_incident_gym.lab import FaultRun
from data_incident_gym.lab_verifier import LabVerification

CASE_ID = "schema_rename_payment_amount"
RUN_ID = "a" * 32
OTHER_RUN_ID = "b" * 32
STARTED_AT = datetime(2026, 8, 28, 0, 0, tzinfo=UTC)
FINISHED_AT = STARTED_AT + timedelta(seconds=1)


def _diagnosis(status: DiagnosisStatus) -> Diagnosis:
    if status is DiagnosisStatus.CONFIRMED:
        return Diagnosis(
            status=status,
            incident_case_id=CASE_ID,
            run_id=RUN_ID,
            root_cause_code="SOURCE_SCHEMA_COLUMN_RENAMED",
            summary="The source schema changed before the dbt consumer.",
            affected_assets=("model.jaffle_shop.stg_payments",),
            evidence_ids=("ev_" + "b" * 64,),
            recommended_actions=("Restore the source contract before the next build.",),
            confidence=0.9,
        )
    summary = (
        "INSUFFICIENT_EVIDENCE"
        if status is DiagnosisStatus.INSUFFICIENT_EVIDENCE
        else "MODEL_RUNTIME_ERROR"
    )
    return Diagnosis(
        status=status,
        incident_case_id=CASE_ID,
        run_id=RUN_ID,
        root_cause_code=None,
        summary=summary,
        affected_assets=(),
        evidence_ids=(),
        recommended_actions=("Collect additional evidence before making a change.",),
        confidence=0.0,
    )


def _diagnosis_run(status: DiagnosisStatus = DiagnosisStatus.CONFIRMED) -> DiagnosisRunResult:
    return DiagnosisRunResult(
        diagnosis=_diagnosis(status),
        evidence_records=(),
        trace=(),
        metrics=DiagnosisMetrics(
            provider="openai-compatible",
            model="qwen3.5:9b",
            model_requests=1,
            input_tokens=0,
            output_tokens=0,
            tool_call_attempts=0,
            successful_tool_calls=0,
            elapsed_ms=1,
        ),
    )


def _evaluation(failed_code: EvaluationCheckCode | None = None) -> EvaluationResult:
    checks = tuple(
        EvaluationCheck(
            code=code,
            passed=code is not failed_code,
            expected=(),
            actual=(),
            reason_code=f"{code.value}_{'FAILED' if code is failed_code else 'PASSED'}",
        )
        for code in EvaluationCheckCode
    )
    return EvaluationResult(
        schema_version="m5.evaluation.v1",
        incident_case_id=CASE_ID,
        run_id=RUN_ID,
        status=EvaluationStatus.FAILED if failed_code is not None else EvaluationStatus.PASSED,
        checks=checks,
        failed_check_codes=() if failed_code is None else (failed_code,),
    )


def _fault_run() -> FaultRun:
    return FaultRun(
        case_id=CASE_ID,
        run_id=RUN_ID,
        artifact_dir=Path(".dig/lab/runs") / RUN_ID,
        dbt_exit_code=1,
        verification=LabVerification(
            status="EXPECTED_FAILURE",
            incident_case_id=CASE_ID,
            run_id=RUN_ID,
            failed_nodes=("model.jaffle_shop.stg_payments",),
            affected_assets=(
                "model.jaffle_shop.stg_payments",
                "model.jaffle_shop.orders",
                "model.jaffle_shop.customers",
            ),
            error_category="DBT_MODEL_ERROR",
            schema_fingerprint="c" * 64,
            ground_truth_digest="d" * 64,
        ),
    )


class FakeLab:
    def __init__(self, calls: list[str], failure_stage: str | None = None) -> None:
        self.calls = calls
        self.failure_stage = failure_stage
        self.recovery_failure = False

    def reset(self, case_id: str) -> object:
        phase = "recovery" if "reset:initial" in self.calls else "initial"
        self.calls.append(f"reset:{phase}")
        if phase == "initial" and self.failure_stage == "initial_reset":
            raise RuntimeError("provider=synthetic C:\\secret\\reset.log")
        if phase == "recovery" and self.recovery_failure:
            raise RuntimeError("provider=synthetic C:\\secret\\recovery.log")
        return object()

    def inject(self, case_id: str) -> object:
        self.calls.append("inject")
        if self.failure_stage == "inject":
            raise RuntimeError("provider=synthetic C:\\secret\\inject.log")
        return object()

    def build(self, case_id: str) -> FaultRun:
        self.calls.append("build")
        if self.failure_stage == "build":
            raise RuntimeError("provider=synthetic C:\\secret\\build.log")
        return _fault_run()


class FakeDiagnosisRunner:
    def __init__(self, calls: list[str], failure: bool, diagnosis_status: DiagnosisStatus) -> None:
        self.calls = calls
        self.failure = failure
        self.diagnosis_status = diagnosis_status

    async def diagnose(self, case_id: str) -> DiagnosisRunResult:
        self.calls.append("diagnose")
        if self.failure:
            raise RuntimeError("provider=synthetic C:\\secret\\model.log")
        return _diagnosis_run(self.diagnosis_status)


class FakeArtifactWriter:
    def __init__(self, output_path: Path, calls: list[str], failure: bool = False) -> None:
        self.output_path = output_path
        self.calls = calls
        self.failure = failure
        self.received: Any = None

    def write(self, artifact_run: Any) -> Path:
        self.calls.append("write_artifacts")
        self.received = artifact_run
        if self.failure:
            raise ArtifactWriteError()
        return self.output_path


class RunnerDeps:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.truth = object()
        self.output_path = Path("artifacts") / RUN_ID
        self.writer = FakeArtifactWriter(self.output_path, self.calls)
        self.lab = FakeLab(self.calls)
        self.diagnosis_status = DiagnosisStatus.CONFIRMED
        self.diagnosis_failure = False
        self.evaluation_calls: list[tuple[object, object, object, bool]] = []
        self.loader_failure = False
        self.factory_failure = False

    def runner(self) -> EvaluationRunner:
        def load_truth(case_id: str) -> object:
            self.calls.append("load_ground_truth")
            if self.loader_failure:
                raise OSError("TEST_REDACTED_VALUE C:\\secret\\truth.json")
            return self.truth

        def diagnosis_factory(run_id: str) -> FakeDiagnosisRunner:
            self.calls.append("diagnosis_factory")
            assert run_id == RUN_ID
            if self.factory_failure:
                raise RuntimeError("provider=synthetic C:\\secret\\factory.log")
            return FakeDiagnosisRunner(self.calls, self.diagnosis_failure, self.diagnosis_status)

        def evaluate(truth: object, verification: object, diagnosis: object, **kwargs: bool):
            self.calls.append("evaluate")
            self.evaluation_calls.append(
                (truth, verification, diagnosis, kwargs["recovery_succeeded"])
            )
            if kwargs["recovery_succeeded"] is False:
                return _evaluation(EvaluationCheckCode.RECOVERY_HEALTHY)
            if diagnosis.diagnosis.status is not DiagnosisStatus.CONFIRMED:
                return _evaluation(EvaluationCheckCode.DIAGNOSIS_CONFIRMED)
            return _evaluation()

        return EvaluationRunner(
            lab=self.lab,
            diagnostic_settings=type(
                "DiagnosticSettingsDouble", (), {"model_base_url": "http://127.0.0.1:11434/v1"}
            )(),
            diagnosis_factory=diagnosis_factory,
            ground_truth_loader=load_truth,
            evaluator=evaluate,
            artifact_writer=self.writer,
            clock=lambda: STARTED_AT,
        )


@pytest.mark.asyncio
async def test_successful_attempt_is_fresh_recovered_evaluated_and_persisted() -> None:
    deps = RunnerDeps()

    result = await deps.runner().run(CASE_ID)

    assert deps.calls == [
        "load_ground_truth",
        "reset:initial",
        "inject",
        "build",
        "diagnosis_factory",
        "diagnose",
        "reset:recovery",
        "evaluate",
        "write_artifacts",
    ]
    assert result.run_id == RUN_ID
    assert result.status == EvaluationStatus.PASSED
    assert result.artifact_dir == deps.output_path
    assert deps.evaluation_calls[0][0] is deps.truth


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "diagnosis_status",
    [DiagnosisStatus.INSUFFICIENT_EVIDENCE, DiagnosisStatus.MODEL_ERROR],
)
async def test_nonpassing_diagnosis_is_still_evaluated_and_persisted(
    diagnosis_status: DiagnosisStatus,
) -> None:
    deps = RunnerDeps()
    deps.diagnosis_status = diagnosis_status

    result = await deps.runner().run(CASE_ID)

    assert result.status == EvaluationStatus.FAILED
    assert deps.writer.received.diagnosis_run.diagnosis.status is diagnosis_status
    assert deps.writer.received.evaluation.status is EvaluationStatus.FAILED


@pytest.mark.asyncio
async def test_recovery_failure_is_saved_after_a_real_diagnosis() -> None:
    deps = RunnerDeps()
    deps.lab.recovery_failure = True

    result = await deps.runner().run(CASE_ID)

    assert result.status == EvaluationStatus.FAILED
    assert deps.writer.received.recovery_status is RecoveryStatus.FAILED
    assert EvaluationCheckCode.RECOVERY_HEALTHY in result.evaluation.failed_check_codes


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["ground_truth_load", "initial_reset", "inject", "build"])
async def test_prediagnosis_failure_never_fabricates_diagnosis_or_sample(stage: str) -> None:
    deps = RunnerDeps()
    if stage == "ground_truth_load":
        deps.loader_failure = True
    else:
        deps.lab.failure_stage = stage

    with pytest.raises(EvaluationWorkflowError) as captured:
        await deps.runner().run(CASE_ID)

    assert captured.value.code == f"{stage.upper()}_FAILED"
    assert "write_artifacts" not in deps.calls
    assert "diagnosis_factory" not in deps.calls
    assert "diagnose" not in deps.calls
    if stage == "ground_truth_load":
        assert deps.calls == ["load_ground_truth"]
        return
    if stage == "initial_reset":
        assert deps.calls == ["load_ground_truth", "reset:initial"]
    else:
        assert deps.calls[-1] == "reset:recovery"


@pytest.mark.asyncio
async def test_ground_truth_is_not_injected_into_diagnosis_and_is_evaluated_last() -> None:
    deps = RunnerDeps()
    result = await deps.runner().run(CASE_ID)

    assert result.status is EvaluationStatus.PASSED
    assert deps.evaluation_calls
    truth, _, diagnosis, _ = deps.evaluation_calls[0]
    assert truth is deps.truth
    assert diagnosis is deps.writer.received.diagnosis_run
    assert all(call != "ground_truth" for call in deps.calls)


@pytest.mark.asyncio
async def test_diagnosis_failure_is_not_overwritten_by_recovery_failure() -> None:
    deps = RunnerDeps()
    deps.diagnosis_failure = True
    deps.lab.recovery_failure = True

    with pytest.raises(EvaluationWorkflowError) as captured:
        await deps.runner().run(CASE_ID)

    assert captured.value.code == "DIAGNOSIS_FAILED"
    assert deps.calls[-1] == "reset:recovery"


@pytest.mark.asyncio
async def test_artifact_writer_failure_is_stable_and_does_not_expose_cause() -> None:
    deps = RunnerDeps()
    deps.writer.failure = True

    with pytest.raises(EvaluationWorkflowError) as captured:
        await deps.runner().run(CASE_ID)

    error = captured.value
    assert error.code == "ARTIFACT_WRITE_FAILED"
    assert error.__cause__ is None
    assert error.__context__ is None
    assert "TEST_REDACTED_VALUE" not in str(error)
    assert "C:\\secret" not in str(error)


@pytest.mark.asyncio
async def test_diagnosis_setup_failure_is_stable_and_does_not_expose_cause() -> None:
    deps = RunnerDeps()
    deps.factory_failure = True

    with pytest.raises(EvaluationWorkflowError) as captured:
        await deps.runner().run(CASE_ID)

    error = captured.value
    assert error.code == "DIAGNOSIS_SETUP_FAILED"
    assert error.__cause__ is None
    assert error.__context__ is None
    assert "provider=synthetic" not in str(error)


def test_attempt_result_has_strict_identity_contract() -> None:
    evaluation = _evaluation()
    result = EvaluationAttemptResult(
        incident_case_id=CASE_ID,
        run_id=RUN_ID,
        status=EvaluationStatus.PASSED,
        evaluation=evaluation,
        artifact_dir=Path("artifacts") / RUN_ID,
    )

    assert result.status is EvaluationStatus.PASSED
    with pytest.raises((TypeError, ValueError)):
        result.status = EvaluationStatus.FAILED  # type: ignore[misc]
