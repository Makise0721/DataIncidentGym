from __future__ import annotations

import pytest
from pydantic import ValidationError

from data_incident_gym.diagnosis import (
    AffectedAssetClaim,
    Diagnosis,
    DiagnosisMetrics,
    DiagnosisRunResult,
    DiagnosisStatus,
    DiagnosisTerminalTraceEvent,
    DiagnosticStrategy,
    HealthStateClaim,
    KernelStateTraceEvent,
    PolicyIdentity,
    RootCauseClaim,
)
from data_incident_gym.diagnostic_kernel import KernelDecision

RUN_ID = "a" * 32
EVIDENCE_ID = "ev_" + "b" * 64


def _policy(strategy: DiagnosticStrategy) -> PolicyIdentity:
    return PolicyIdentity(
        strategy=strategy,
        base_prompt_version="p1.base.v1",
        base_prompt_sha256="b" * 64,
        strategy_prompt_version=(
            "p1.static.v1"
            if strategy is DiagnosticStrategy.STATIC_SKILL
            else "p1.kernel.v2"
        ),
        strategy_prompt_sha256="c" * 64,
        controller_protocol_version="p1.controller.v1",
        controller_protocol_sha256="d" * 64,
        tool_schema_sha256="e" * 64,
    )


def _metrics(*, tool_calls: int = 0, successful: int = 0) -> DiagnosisMetrics:
    return DiagnosisMetrics(
        provider="synthetic",
        model="synthetic-model",
        model_requests=1,
        input_tokens=0,
        output_tokens=0,
        tool_call_attempts=tool_calls,
        successful_tool_calls=successful,
        elapsed_ms=1,
    )


def _terminal(strategy: DiagnosticStrategy, status: DiagnosisStatus, evidence_ids=()):
    return DiagnosisTerminalTraceEvent(
        event_type="DIAGNOSIS_TERMINAL",
        strategy=strategy,
        status=status,
        evidence_inventory=tuple(evidence_ids),
    )


def test_common_diagnosis_supports_all_four_terminal_statuses() -> None:
    root = RootCauseClaim(
        kind="ROOT_CAUSE",
        value="SOURCE_SCHEMA_COLUMN_TYPE_CHANGED",
        evidence_ids=(EVIDENCE_ID,),
    )
    asset = AffectedAssetClaim(
        kind="AFFECTED_ASSET",
        value="model.jaffle_shop.stg_payments",
        evidence_ids=(EVIDENCE_ID,),
    )
    confirmed = Diagnosis(
        status=DiagnosisStatus.CONFIRMED,
        run_id=RUN_ID,
        root_cause_code=root.root_cause_code,
        summary="The source type changed.",
        affected_assets=(asset.asset,),
        evidence_ids=(EVIDENCE_ID,),
        claims=(root, asset),
        confidence=0.9,
    )
    insufficient = Diagnosis(
        status=DiagnosisStatus.INSUFFICIENT_EVIDENCE,
        run_id=RUN_ID,
        summary="The decisive observation is unavailable.",
        unresolved_evidence=(
            {
                "evidence_kind": "RELATION_SCHEMA",
                "subject": "raw_orders",
                "reason_code": "RELATION_NOT_ALLOWED",
            },
        ),
        confidence=0.2,
    )
    health_claim = HealthStateClaim(
        kind="HEALTH_STATE",
        relation_name="raw_orders",
        history_name="order_count_by_day",
        bucket="2018-04-02",
        current_value=1,
        evidence_ids=(EVIDENCE_ID,),
    )
    no_incident = Diagnosis(
        status=DiagnosisStatus.NO_INCIDENT,
        run_id=RUN_ID,
        summary="The observed health metric is within its baseline range.",
        evidence_ids=(EVIDENCE_ID,),
        claims=(health_claim,),
        confidence=0.95,
    )
    model_error = Diagnosis(
        status=DiagnosisStatus.MODEL_ERROR,
        run_id=RUN_ID,
        summary="MODEL_PROTOCOL_ERROR",
        confidence=0.0,
    )

    assert {
        item.status
        for item in (confirmed, insufficient, no_incident, model_error)
    } == set(DiagnosisStatus)


def test_diagnosis_run_result_has_strategy_specific_trace_shape() -> None:
    static_diagnosis = Diagnosis(
        status=DiagnosisStatus.MODEL_ERROR,
        run_id=RUN_ID,
        summary="MODEL_RUNTIME_ERROR",
        confidence=0.0,
    )
    static_terminal = _terminal(DiagnosticStrategy.STATIC_SKILL, static_diagnosis.status)
    static = DiagnosisRunResult(
        strategy=DiagnosticStrategy.STATIC_SKILL,
        policy_identity=_policy(DiagnosticStrategy.STATIC_SKILL),
        diagnosis=static_diagnosis,
        evidence_records=(),
        trace=(static_terminal,),
        metrics=_metrics(),
    )

    kernel_terminal = _terminal(DiagnosticStrategy.DIAGNOSTIC_KERNEL, static_diagnosis.status)
    kernel_state = {"run_id": RUN_ID, "status": "MODEL_ERROR"}
    kernel = DiagnosisRunResult(
        strategy=DiagnosticStrategy.DIAGNOSTIC_KERNEL,
        policy_identity=_policy(DiagnosticStrategy.DIAGNOSTIC_KERNEL),
        diagnosis=static_diagnosis,
        evidence_records=(),
        trace=(
            KernelStateTraceEvent(event_type="KERNEL_STATE", state=kernel_state),
            kernel_terminal,
        ),
        metrics=_metrics(),
        kernel_state=kernel_state,
    )

    assert static.kernel_state is None
    assert kernel.trace[-2].event_type == "KERNEL_STATE"
    assert kernel.investigation_state == kernel_state


def test_kernel_decision_rejects_unresolved_evidence_for_terminal_statuses() -> None:
    with pytest.raises(ValueError, match="only INSUFFICIENT_EVIDENCE"):
        KernelDecision(
            status="NO_INCIDENT",
            run_id=RUN_ID,
            summary="healthy",
            recommended_actions=(),
            confidence=0.5,
            unresolved_evidence=(
                {
                    "evidence_kind": "RELATION_SCHEMA",
                    "subject": "raw_orders",
                    "reason_code": "NOT_OBSERVABLE",
                },
            ),
        )


def test_diagnosis_is_frozen_and_rejects_extra_fields() -> None:
    diagnosis = Diagnosis(
        status=DiagnosisStatus.MODEL_ERROR,
        run_id=RUN_ID,
        summary="MODEL_DECLINED",
        confidence=0.0,
    )

    with pytest.raises(ValidationError):
        diagnosis.summary = "changed"  # type: ignore[misc]
    with pytest.raises(ValueError):
        Diagnosis(
            status=DiagnosisStatus.MODEL_ERROR,
            run_id=RUN_ID,
            summary="MODEL_DECLINED",
            confidence=0.0,
            extra="nope",
        )
