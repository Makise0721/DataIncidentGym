"""Versioned privacy projection and offline review for kernel refusals."""

from __future__ import annotations

import hashlib
import json

import pytest
from pydantic import ValidationError

from data_incident_gym.artifacts import (
    ARTIFACT_FILENAMES,
    BudgetSummary,
    TraceEnvelopeV3,
    trace_schema_for_policy_identity,
    trace_schema_for_run,
    validate_trace_envelope,
)
from data_incident_gym.diagnosis import (
    Diagnosis,
    DiagnosisMetrics,
    DiagnosisRunResultV3,
    DiagnosisStatus,
    DiagnosisTerminalTraceEvent,
    DiagnosticStrategy,
    EvidenceGateTraceEventV2,
    KernelStateTraceEvent,
    PolicyIdentity,
    RejectedDecisionSummaryV2,
    ToolTraceEventV2,
    UnresolvedEvidence,
    refusal_witnessed,
)
from data_incident_gym.diagnostic_agent import _rejected_decision_summary
from data_incident_gym.diagnostic_contracts import (
    EvidenceGap,
    EvidenceGapKind,
    EvidenceGapStatus,
    Hypothesis,
    InvestigationState,
    KernelDecision,
    KernelFinalStatus,
)
from data_incident_gym.diagnostic_kernel import DiagnosticKernel
from data_incident_gym.evaluation_inputs import (
    ArtifactDigest,
    EvaluationInputBundleV3,
    EvaluatorIdentity,
    RecoveryProof,
    VerificationPayload,
    _finalize_bundle,
    _payload_digest,
)
from data_incident_gym.refusal_review import (
    BASIS_DECISION_SCOPE_MISMATCH_ESTABLISHED,
    BASIS_KERNEL_FINALIZED_ESTABLISHED,
    BASIS_KERNEL_SUBJECT_UNAVAILABLE,
    BASIS_KERNEL_UNRESOLVED_DUPLICATE,
    BASIS_KERNEL_UNRESOLVED_ESTABLISHED,
    BASIS_KERNEL_UNRESOLVED_TRUNCATED,
    RefusalReviewStatus,
    review_refusal_events,
)
from data_incident_gym.scenarios import load_scenario_spec

RUN_ID = "b" * 32
FINGERPRINT = "a" * 64


def _policy() -> PolicyIdentity:
    return PolicyIdentity(
        strategy=DiagnosticStrategy.DIAGNOSTIC_KERNEL,
        base_prompt_version="p1.base.v1",
        base_prompt_sha256="1" * 64,
        strategy_prompt_version="p1.kernel.v1",
        strategy_prompt_sha256="2" * 64,
        controller_protocol_version="p1.controller.v22",
        controller_protocol_sha256="3" * 64,
        tool_schema_sha256="4" * 64,
    )


def _state(
    *,
    run_id: str = RUN_ID,
    gap_status: EvidenceGapStatus = EvidenceGapStatus.OPEN,
    gap_error_code: str | None = None,
    final_status: KernelFinalStatus | None = None,
    tool_calls_used: int = 0,
) -> InvestigationState:
    return InvestigationState(
        schema_version="p1.investigation.v1",
        run_id=run_id,
        revision=0,
        allowed_root_cause_codes=(
            "SOURCE_PAYMENT_INGESTION_LOSS",
            "NORMAL_LATE_ARRIVING_ORDER",
        ),
        hypotheses=(
            Hypothesis(
                hypothesis_id="h_ingestion_loss",
                root_cause_code="SOURCE_PAYMENT_INGESTION_LOSS",
            ),
            Hypothesis(
                hypothesis_id="h_late_order",
                root_cause_code="NORMAL_LATE_ARRIVING_ORDER",
            ),
        ),
        gaps=(
            EvidenceGap(
                gap_id="g_profile",
                gap_kind=EvidenceGapKind.PROFILE_RELATION,
                hypothesis_ids=("h_ingestion_loss", "h_late_order"),
                tool_name="get_relation_data_profile",
                subject="raw_payments",
                status=gap_status,
                error_code=gap_error_code,
            ),
        ),
        assessments=(),
        claims=(),
        evidence_inventory=(),
        tool_fingerprints=(),
        model_request_limit=8,
        model_requests_used=1,
        model_requests_remaining=7,
        tool_call_limit=8,
        tool_calls_used=tool_calls_used,
        tool_calls_remaining=8 - tool_calls_used,
        final_status=final_status,
        gate_reason=(final_status.value if final_status is not None else None),
        selected_hypothesis_id=None,
    )


def _summary(
    *,
    reason_status: str = "INSUFFICIENT_EVIDENCE",
    scope_matches: bool = True,
    unresolved: tuple[tuple[str | None, str, str, int], ...] = (),
    truncated_unresolved: int = 0,
) -> RejectedDecisionSummaryV2:
    declarations = tuple(
        {
            "evidence_kind": kind,
            "reason_code": reason,
            "subject": subject,
        }
        for subject, kind, reason, _ in unresolved
    )
    equivalence = tuple(subject_id for _, _, _, subject_id in unresolved)
    redacted = sum(subject is None for subject, _, _, _ in unresolved)
    return RejectedDecisionSummaryV2(
        schema_version="p1.rejected_decision.v2",
        model_request_index=1,
        status=reason_status,
        decision_scope_matches_run=scope_matches,
        unresolved_subject_equivalence=equivalence,
        unresolved_evidence=declarations,
        unknown_subject_count=redacted,
        total_unresolved=len(unresolved) + truncated_unresolved,
        truncated_unresolved_count=truncated_unresolved,
        truncated=bool(truncated_unresolved),
    )


def _run(
    reason_code: str,
    summary: RejectedDecisionSummaryV2,
    *,
    state: InvestigationState | None = None,
    accepted_before: bool = False,
    tool_after: bool = False,
) -> DiagnosisRunResultV3:
    snapshot = state or _state(tool_calls_used=int(tool_after))
    trace: list[object] = []
    if accepted_before:
        trace.append(
            EvidenceGateTraceEventV2(
                schema_version="p1.evidence_gate.v2",
                event_type="EVIDENCE_GATE",
                reason_code="INSUFFICIENT_EVIDENCE",
                accepted=True,
            )
        )
    trace.append(
        EvidenceGateTraceEventV2(
            schema_version="p1.evidence_gate.v2",
            event_type="EVIDENCE_GATE",
            reason_code=reason_code,
            accepted=False,
            rejected_decision=summary,
        )
    )
    if tool_after:
        from data_incident_gym.diagnosis import ToolTraceEventV2

        trace.append(
            ToolTraceEventV2(
                event_type="TOOL_CALL_V2",
                tool_name="get_relation_schema",
                arguments={"relation_name": "raw_payments"},
                fingerprint=FINGERPRINT,
                evidence_ids=(),
                error_code="RELATION_NOT_ALLOWED",
                outcome_origin="EVIDENCE_BACKEND",
                elapsed_ms=1,
            )
        )
    trace.extend(
        (
            KernelStateTraceEvent(event_type="KERNEL_STATE", state=snapshot),
            DiagnosisTerminalTraceEvent(
                event_type="DIAGNOSIS_TERMINAL",
                strategy=DiagnosticStrategy.DIAGNOSTIC_KERNEL,
                status=DiagnosisStatus.INSUFFICIENT_EVIDENCE,
                evidence_inventory=(),
            ),
        )
    )
    return DiagnosisRunResultV3(
        strategy=DiagnosticStrategy.DIAGNOSTIC_KERNEL,
        policy_identity=_policy(),
        diagnosis=Diagnosis(
            status=DiagnosisStatus.INSUFFICIENT_EVIDENCE,
            run_id=RUN_ID,
            summary="insufficient",
            unresolved_evidence=(
                UnresolvedEvidence(
                    evidence_kind="RELATION_DATA_PROFILE",
                    subject="raw_payments",
                    reason_code="RELATION_NOT_ALLOWED",
                ),
            ),
            confidence=0.5,
        ),
        evidence_records=(),
        trace=tuple(trace),
        metrics=DiagnosisMetrics(
            provider="synthetic",
            model="synthetic-model",
            model_requests=1,
            input_tokens=0,
            output_tokens=0,
            tool_call_attempts=int(tool_after),
            successful_tool_calls=0,
            elapsed_ms=1,
        ),
        kernel_state=snapshot,
    )


def _generated_summary(subjects: tuple[str, ...]) -> RejectedDecisionSummaryV2:
    from data_incident_gym.diagnostic_contracts import KernelDecision

    kernel = DiagnosticKernel.start(
        run_id=RUN_ID,
        allowed_root_cause_codes=(
            "SOURCE_PAYMENT_INGESTION_LOSS",
            "NORMAL_LATE_ARRIVING_ORDER",
        ),
        model_request_limit=8,
        tool_call_limit=8,
    )
    decision = KernelDecision(
        status="INSUFFICIENT_EVIDENCE",
        run_id=RUN_ID,
        unresolved_evidence=tuple(
            UnresolvedEvidence(
                evidence_kind="TRANSFORMATION_DEFINITION",
                subject=subject,
                reason_code="NOT_OBSERVABLE",
            )
            for subject in subjects
        ),
        summary="private summary",
        recommended_actions=(),
        confidence=0.5,
    )
    return _rejected_decision_summary(decision, kernel, model_request_index=1)


def test_refusal_projection_requires_the_kernel_run_id() -> None:
    kernel = DiagnosticKernel.start(
        run_id=RUN_ID,
        allowed_root_cause_codes=(
            "SOURCE_PAYMENT_INGESTION_LOSS",
            "NORMAL_LATE_ARRIVING_ORDER",
        ),
        model_request_limit=8,
        tool_call_limit=8,
    )
    decision = KernelDecision(
        status="INSUFFICIENT_EVIDENCE",
        run_id="f" * 32,
        unresolved_evidence=(),
        summary="private summary",
        recommended_actions=(),
        confidence=0.5,
    )

    class KernelWithoutRunId:
        def __getattr__(self, name: str) -> object:
            if name == "run_id":
                raise AttributeError(name)
            return getattr(kernel, name)

    with pytest.raises(ValueError, match="kernel.run_id is required"):
        _rejected_decision_summary(
            decision,
            KernelWithoutRunId(),  # type: ignore[arg-type]
            model_request_index=1,
        )


def test_scope_is_harness_attested_and_checked_before_business_rules() -> None:
    mismatch = _run(
        "DECISION_SCOPE_MISMATCH",
        _summary(scope_matches=False),
    )
    verdict = review_refusal_events(
        mismatch, load_scenario_spec("duplicate_payment_coupon_b")
    )[0]
    assert verdict.status is RefusalReviewStatus.CORRECT
    assert verdict.basis == (BASIS_DECISION_SCOPE_MISMATCH_ESTABLISHED,)

    wrong_code = _run(
        "UNRESOLVED_EVIDENCE_UNBOUND",
        _summary(scope_matches=False),
    )
    verdict = review_refusal_events(
        wrong_code, load_scenario_spec("duplicate_payment_coupon_b")
    )[0]
    assert verdict.status is RefusalReviewStatus.FALSE_REFUSAL


def test_kernel_finalized_is_replayed_before_scope_mismatch() -> None:
    run = _run(
        "KERNEL_FINALIZED",
        _summary(scope_matches=False),
        state=_state(final_status=KernelFinalStatus.INSUFFICIENT_EVIDENCE),
        accepted_before=True,
    )
    verdict = review_refusal_events(
        run, load_scenario_spec("duplicate_payment_coupon_b")
    )[0]
    assert verdict.status is RefusalReviewStatus.CORRECT
    assert verdict.basis == (BASIS_KERNEL_FINALIZED_ESTABLISHED,)


def test_open_gap_refusal_is_replayed_after_scope() -> None:
    run = _run(
        "EVIDENCE_GAP_OPEN",
        _summary(reason_status="NO_INCIDENT"),
    )
    verdict = review_refusal_events(
        run, load_scenario_spec("duplicate_payment_coupon_b")
    )[0]
    assert verdict.status is RefusalReviewStatus.CORRECT


def test_first_seen_equivalence_redacts_values_and_reloads() -> None:
    private_a = "PRIVATE_SUBJECT_ALPHA"
    private_b = "PRIVATE_SUBJECT_BETA"
    foreign_run_id = "f" * 32
    kernel = DiagnosticKernel.start(
        run_id=RUN_ID,
        allowed_root_cause_codes=("SOURCE_PAYMENT_INGESTION_LOSS", "NORMAL_LATE_ARRIVING_ORDER"),
        model_request_limit=8,
        tool_call_limit=8,
    )
    summary = _generated_summary((private_a, private_b, private_a))
    decision = summary.model_copy(
        update={"decision_scope_matches_run": foreign_run_id == RUN_ID}
    )
    assert decision.unresolved_subject_equivalence == (0, 1, 0)
    payload = decision.model_dump_json()
    assert private_a not in payload and private_b not in payload
    assert hashlib.sha256(private_a.encode()).hexdigest() not in payload
    assert foreign_run_id not in payload
    restored = RejectedDecisionSummaryV2.model_validate_json(payload)
    assert json.loads(restored.model_dump_json()) == json.loads(payload)
    assert summary.decision_scope_matches_run is True
    assert kernel.run_id == RUN_ID


def test_redacted_equal_and_distinct_subjects_are_not_conflated() -> None:
    scenario = load_scenario_spec("duplicate_payment_coupon_b")
    duplicate = _summary(
        unresolved=(
            (None, "TRANSFORMATION_DEFINITION", "NOT_OBSERVABLE", 0),
            (None, "TRANSFORMATION_DEFINITION", "NOT_OBSERVABLE", 0),
        )
    )
    duplicate_verdict = review_refusal_events(
        _run("UNRESOLVED_EVIDENCE_UNBOUND", duplicate), scenario
    )[0]
    assert duplicate_verdict.status is RefusalReviewStatus.FALSE_REFUSAL
    assert duplicate_verdict.basis == (BASIS_KERNEL_UNRESOLVED_DUPLICATE,)

    distinct = _summary(
        unresolved=(
            (None, "TRANSFORMATION_DEFINITION", "NOT_OBSERVABLE", 0),
            (None, "TRANSFORMATION_DEFINITION", "NOT_OBSERVABLE", 1),
        )
    )
    distinct_verdict = review_refusal_events(
        _run("UNRESOLVED_EVIDENCE_UNBOUND", distinct), scenario
    )[0]
    assert distinct_verdict.status is RefusalReviewStatus.CORRECT
    assert distinct_verdict.basis == (BASIS_KERNEL_UNRESOLVED_ESTABLISHED,)


def test_redacted_relation_is_indeterminate_only_when_a_blocked_gap_could_bind() -> None:
    scenario = load_scenario_spec("duplicate_payment_coupon_b")
    summary = _summary(
        unresolved=((None, "RELATION_DATA_PROFILE", "RELATION_NOT_ALLOWED", 0),)
    )
    unbound = review_refusal_events(
        _run("UNRESOLVED_EVIDENCE_UNBOUND", summary), scenario
    )[0]
    assert unbound.status is RefusalReviewStatus.CORRECT
    assert unbound.basis == (BASIS_KERNEL_UNRESOLVED_ESTABLISHED,)

    possibly_bound = review_refusal_events(
        _run(
            "UNRESOLVED_EVIDENCE_UNBOUND",
            summary,
            state=_state(
                gap_status=EvidenceGapStatus.BLOCKED,
                gap_error_code="RELATION_NOT_ALLOWED",
            ),
        ),
        scenario,
    )[0]
    assert possibly_bound.status is RefusalReviewStatus.INDETERMINABLE
    assert possibly_bound.basis == (BASIS_KERNEL_SUBJECT_UNAVAILABLE,)


def test_unbound_visible_subject_and_truncation() -> None:
    scenario = load_scenario_spec("duplicate_payment_coupon_b")
    visible_unbound = _summary(
        unresolved=(("private_unknown", "TRANSFORMATION_DEFINITION", "NOT_OBSERVABLE", 0),)
    )
    verdict = review_refusal_events(
        _run("UNRESOLVED_EVIDENCE_UNBOUND", visible_unbound), scenario
    )[0]
    assert verdict.status is RefusalReviewStatus.CORRECT

    truncated = _summary(
        unresolved=((None, "TRANSFORMATION_DEFINITION", "NOT_OBSERVABLE", 0),),
        truncated_unresolved=1,
    )
    verdict = review_refusal_events(
        _run("UNRESOLVED_EVIDENCE_UNBOUND", truncated), scenario
    )[0]
    assert verdict.status is RefusalReviewStatus.INDETERMINABLE
    assert verdict.basis == (BASIS_KERNEL_UNRESOLVED_TRUNCATED,)


def test_snapshot_after_more_tool_calls_is_not_used_for_refusal() -> None:
    run = _run(
        "UNRESOLVED_EVIDENCE_UNBOUND",
        _summary(),
        state=_state(tool_calls_used=1),
        tool_after=True,
    )
    verdict = review_refusal_events(
        run, load_scenario_spec("duplicate_payment_coupon_b")
    )[0]
    assert verdict.status is RefusalReviewStatus.INDETERMINABLE


def test_trace_v3_round_trips_strictly_and_rejects_legacy_gate_shape() -> None:
    event = EvidenceGateTraceEventV2(
        schema_version="p1.evidence_gate.v2",
        event_type="EVIDENCE_GATE",
        reason_code="DECISION_SCOPE_MISMATCH",
        accepted=False,
        rejected_decision=_summary(scope_matches=False),
    )
    envelope = TraceEnvelopeV3(
        schema_version="p1.trace.v3",
        sequence=1,
        event=event,
    )
    payload = envelope.model_dump(mode="json")
    restored = validate_trace_envelope(payload, expected_schema_version="p1.trace.v3")
    assert restored == envelope
    legacy_payload = json.loads(json.dumps(payload))
    del legacy_payload["event"]["schema_version"]
    with pytest.raises(ValidationError):
        validate_trace_envelope(legacy_payload, expected_schema_version="p1.trace.v3")


def test_v3_scoring_inputs_are_strictly_versioned_and_round_trip() -> None:
    run = _run("DECISION_SCOPE_MISMATCH", _summary(scope_matches=False))
    assert trace_schema_for_run(run) == "p1.trace.v3"
    assert trace_schema_for_policy_identity(run.policy_identity) == "p1.trace.v3"
    scenario = load_scenario_spec("duplicate_payment_coupon_b")
    verification = VerificationPayload(
        status="EXPECTED_FAILURE",
        incident_case_id=scenario.incident_case_id,
        run_id=RUN_ID,
        dbt_exit_code=1,
        failed_nodes=(),
        skipped_nodes=(),
        affected_assets=(),
        schema_fingerprint="a" * 64,
        profile_spec_sha256="b" * 64,
    )
    bundle = EvaluationInputBundleV3(
        run_id=RUN_ID,
        incident_case_id=scenario.incident_case_id,
        strategy=run.strategy,
        scenario=scenario,
        scenario_digest=scenario.digest(),
        verification=verification,
        verification_digest=_payload_digest(verification),
        diagnosis_run=run,
        diagnosis_run_digest=run.digest(),
        recovery=RecoveryProof(
            source="LAB_RESTORE",
            incident_case_id=scenario.incident_case_id,
            state="HEALTHY",
            fingerprint="c" * 64,
        ),
        budget=BudgetSummary(
            model_request_limit=8,
            tool_call_limit=8,
            output_retry_limit=2,
            timeout_seconds=300,
        ),
        original_evaluator=EvaluatorIdentity(
            name="DETERMINISTIC",
            version="p1.evaluator.v5",
            source_digest="d" * 64,
            dependencies_digest="e" * 64,
        ),
        artifact_digests=tuple(
            ArtifactDigest(name=name, sha256="f" * 64) for name in ARTIFACT_FILENAMES
        ),
    )
    restored = _finalize_bundle(bundle.model_dump(mode="json"))
    assert isinstance(restored, EvaluationInputBundleV3)
    assert restored.inputs_digest() == bundle.inputs_digest()

    old_evaluator_payload = bundle.model_dump(mode="python")
    old_evaluator_payload["original_evaluator"]["version"] = "p1.evaluator.v4"
    with pytest.raises(ValidationError):
        EvaluationInputBundleV3.model_validate(old_evaluator_payload)

    old_controller_payload = run.model_dump(mode="python")
    old_controller_payload["policy_identity"]["controller_protocol_version"] = (
        "p1.controller.v21"
    )
    with pytest.raises(ValidationError):
        DiagnosisRunResultV3.model_validate(old_controller_payload)


def test_v3_refusal_witness_preserves_i2_provenance_rules() -> None:
    event = ToolTraceEventV2(
        event_type="TOOL_CALL_V2",
        tool_name="get_relation_schema",
        arguments={"relation_name": "raw_payments"},
        fingerprint=FINGERPRINT,
        evidence_ids=(),
        error_code="RELATION_NOT_ALLOWED",
        outcome_origin="EVIDENCE_BACKEND",
        elapsed_ms=1,
    )
    result = refusal_witnessed(
        (event,),
        tool_name="get_relation_schema",
        target="raw_payments",
        code="RELATION_NOT_ALLOWED",
        diagnosis_run_schema_version="p1.diagnosis_run.v3",
    )
    assert result.witnessed is True


def test_equivalence_ids_must_be_canonical_and_aligned() -> None:
    with pytest.raises(ValidationError):
        _summary(
            unresolved=(
                (None, "TRANSFORMATION_DEFINITION", "NOT_OBSERVABLE", 1),
            )
        )
    payload = _summary(
        unresolved=((None, "TRANSFORMATION_DEFINITION", "NOT_OBSERVABLE", 0),)
    ).model_dump(mode="python")
    payload["unresolved_subject_equivalence"] = ()
    with pytest.raises(ValidationError):
        RejectedDecisionSummaryV2.model_validate(payload)


def test_visible_subjects_must_agree_with_equivalence_ids() -> None:
    with pytest.raises(ValidationError, match="visible subjects"):
        _summary(
            unresolved=(
                ("raw_payments", "RELATION_DATA_PROFILE", "NOT_OBSERVABLE", 0),
                ("raw_payments", "RELATION_DATA_PROFILE", "NOT_OBSERVABLE", 1),
            )
        )
    with pytest.raises(ValidationError, match="visible subjects"):
        _summary(
            unresolved=(
                ("raw_payments", "RELATION_DATA_PROFILE", "NOT_OBSERVABLE", 0),
                ("raw_orders", "RELATION_DATA_PROFILE", "NOT_OBSERVABLE", 0),
            )
        )
