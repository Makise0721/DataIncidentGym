from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from data_incident_gym.evaluation import (
    EvaluationApplicability,
    EvaluationCheck,
    EvaluationCheckCode,
    EvaluationResult,
    EvaluationStatus,
)
from data_incident_gym.evaluation_runner import (
    EvaluationAttemptResult,
    EvaluationRunner,
    EvaluationWorkflowError,
    _failed_evaluation,
)

RUN_ID = "a" * 32


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


def test_attempt_result_requires_consistent_run_and_artifact_identity() -> None:
    evaluation = _evaluation()

    result = EvaluationAttemptResult(
        incident_case_id=evaluation.incident_case_id,
        run_id=RUN_ID,
        status=evaluation.status,
        evaluation=evaluation,
        artifact_dir=Path("artifacts") / RUN_ID,
    )

    assert result.artifact_dir.name == RUN_ID


def test_attempt_result_rejects_mismatched_status() -> None:
    evaluation = _evaluation()

    with pytest.raises(ValueError, match="status"):
        EvaluationAttemptResult(
            incident_case_id=evaluation.incident_case_id,
            run_id=RUN_ID,
            status=EvaluationStatus.FAILED,
            evaluation=evaluation,
            artifact_dir=Path("artifacts") / RUN_ID,
        )


def test_private_evaluation_failure_has_a_complete_fail_closed_result() -> None:
    evaluation = _failed_evaluation(
        "schema_type_change_payment_amount",
        RUN_ID,
        "VERIFICATION_LOAD_FAILED",
    )

    assert evaluation.status is EvaluationStatus.FAILED
    assert evaluation.failed_check_codes == tuple(EvaluationCheckCode)
    assert all(check.actual == ("VERIFICATION_LOAD_FAILED",) for check in evaluation.checks)


class _FailingLab:
    def __init__(self, *, reset_error: bool = False, build_error: bool = False) -> None:
        self.reset_error = reset_error
        self.build_error = build_error
        self.restore_calls = 0
        self.restore_error = False

    def reset(self, _: str) -> None:
        if self.reset_error:
            raise RuntimeError("reset failed")

    def prepare(self, _: str) -> None:
        return None

    def build(self, _: str, **__: object) -> None:
        if self.build_error:
            raise RuntimeError("build failed")

    def restore(self, _: str) -> SimpleNamespace:
        self.restore_calls += 1
        if self.restore_error:
            raise RuntimeError("restore failed")
        return SimpleNamespace(state="HEALTHY")


def _runner_for_lab(lab: _FailingLab) -> EvaluationRunner:
    return EvaluationRunner(
        lab=lab,
        diagnostic_settings=object(),
        diagnosis_factory=lambda *_: object(),
        private_scenario_loader=lambda _: object(),
        private_verification_loader=lambda _: object(),
        evaluator=lambda *_, **__: _evaluation(),
        artifact_writer=object(),
        clock=lambda: datetime.now(UTC),
    )


def test_runner_restores_after_initial_reset_failure_and_reports_recovery() -> None:
    lab = _FailingLab(reset_error=True)

    with pytest.raises(EvaluationWorkflowError) as caught:
        asyncio.run(_runner_for_lab(lab).run("schema_type_change_payment_amount"))

    assert caught.value.code == "INITIAL_RESET_FAILED"
    assert caught.value.recovery_succeeded is True
    assert lab.restore_calls == 1


def test_runner_preserves_primary_build_failure_when_restore_fails() -> None:
    lab = _FailingLab(build_error=True)
    lab.restore_error = True

    with pytest.raises(EvaluationWorkflowError) as caught:
        asyncio.run(_runner_for_lab(lab).run("schema_type_change_payment_amount"))

    assert caught.value.code == "BUILD_FAILED"
    assert caught.value.recovery_succeeded is False
    assert lab.restore_calls == 1
