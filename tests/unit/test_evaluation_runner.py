from __future__ import annotations

from pathlib import Path

import pytest

from data_incident_gym.evaluation import (
    EvaluationApplicability,
    EvaluationCheck,
    EvaluationCheckCode,
    EvaluationResult,
    EvaluationStatus,
)
from data_incident_gym.evaluation_runner import EvaluationAttemptResult, _failed_evaluation

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
