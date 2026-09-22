"""P-1 closure: the refusal audit's writer and its offline reader (2026-09-22).

The writer tests pin the projection's counting identities and its
recomputability codes; the reader tests pin the three-way verdict
(CORRECT / FALSE_REFUSAL / INDETERMINABLE) re-derived from the archived
trace prefix, never from the stored ``recomputable`` boolean.
"""

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from data_incident_gym.diagnosis import (
    AuditClaimSummary,
    AuditUnresolvedSummary,
    Diagnosis,
    DiagnosisMetrics,
    DiagnosisRunResult,
    DiagnosisStatus,
    DiagnosisTerminalTraceEvent,
    DiagnosticStrategy,
    EvidenceGateTraceEvent,
    HealthStateClaim,
    PolicyIdentity,
    RefusalAudit,
    RejectedDecisionSummary,
    RootCauseClaim,
    ToolTraceEvent,
    UnresolvedEvidence,
)
from data_incident_gym.diagnostic_agent import _refusal_audit
from data_incident_gym.evidence import (
    DbtNodeErrorFact,
    EvidenceRecord,
    EvidenceSource,
    EvidenceType,
)
from data_incident_gym.refusal_review import (
    BASIS_ALL_APPLICABLE_CLAIMS_SUPPORTED,
    BASIS_AUDIT_ABSENT,
    BASIS_AUDIT_EVIDENCE_OUTSIDE_PREFIX,
    BASIS_CLAIMS_TRUNCATED,
    BASIS_COUNT_IDENTITY_VIOLATED,
    BASIS_GATE_INTERNAL_ERROR,
    BASIS_I1_NOT_APPLICABLE,
    BASIS_KERNEL_CONTRACT_PATHWAY,
    BASIS_REASON_CODE_MISMATCH,
    BASIS_UNREGISTERED_REF_ESTABLISHED,
    BASIS_UNSUPPORTED_CLAIM_ESTABLISHED,
    RefusalReviewStatus,
    review_refusal_events,
)
from data_incident_gym.scenarios import load_scenario_spec
from data_incident_gym.submission_policy import CLAIM_SUPPORT_REQUIRED, GAP_RECEIPT_REQUIRED

RUN_ID = "b" * 32
OBSERVED_AT = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)
CONFIRMED_CASE = "required_null_order_customer_a"
INSUFFICIENT_CASE = "duplicate_payment_coupon_b"
NO_INCIDENT_CASE = "order_volume_pattern_a"
FINGERPRINT = "a" * 64


# ---------------------------------------------------------------------------
# Writer helpers and tests
# ---------------------------------------------------------------------------


def _node_error_record(node_id: str) -> EvidenceRecord:
    return EvidenceRecord.create(
        run_id=RUN_ID,
        evidence_type=EvidenceType.DBT_NODE_ERROR,
        source=EvidenceSource.DBT_RUN_RESULTS,
        subject=node_id,
        observed_at=OBSERVED_AT,
        content=DbtNodeErrorFact(
            kind="DBT_NODE_ERROR",
            run_id=RUN_ID,
            node_id=node_id,
            resource_type="test",
            status="failed",
            message="failed",
        ),
    )


def _audit_state(records: tuple[EvidenceRecord, ...]) -> Any:
    return SimpleNamespace(
        kernel=None,
        evidence_records=list(records),
        submission_policy=None,
        usage=SimpleNamespace(requests=1),
    )


def _submission_with(claims: tuple[Any, ...]) -> Any:
    return SimpleNamespace(status="CONFIRMED", claims=claims, unresolved_evidence=())


def test_audit_counts_survive_cap_with_unregistered_refs() -> None:
    """Twenty registered plus twenty unregistered citations: the identity
    ``total = kept + unregistered + truncated`` must hold even when the cap
    drops citations (the dropped unregistered ones must not be counted twice)."""

    records = tuple(_node_error_record(f"node_{i}") for i in range(20))
    registered = [record.evidence_id for record in records]
    unregistered = [f"ev_{index:064x}" for index in range(20)]
    citations = tuple(registered + unregistered)
    claim = RootCauseClaim(
        kind="ROOT_CAUSE",
        root_cause_code="SOURCE_PAYMENT_INGESTION_LOSS",
        evidence_ids=citations,
    )

    audit = _refusal_audit(
        _audit_state(records),
        reason_code=CLAIM_SUPPORT_REQUIRED,
        submission=_submission_with((claim,)),
    )

    assert audit is not None
    projected = audit.claims[0]
    assert projected.total_evidence_refs == 40
    assert projected.truncated_evidence_refs == 8
    assert len(projected.evidence_ids) == 20
    assert projected.unregistered_evidence_refs == 12
    assert (
        projected.total_evidence_refs
        == len(projected.evidence_ids)
        + projected.unregistered_evidence_refs
        + projected.truncated_evidence_refs
    )


def test_audit_marks_unregistered_ref_recomputable_by_count() -> None:
    """A claim with an unregistered citation is recomputable from the counts
    alone (the refusal condition is "had an unregistered citation"); the fixed
    gate message never names "UNREGISTERED", so the decision must come from
    the projection's shape, not the message text."""

    records = (_node_error_record("node_0"),)
    claim = RootCauseClaim(
        kind="ROOT_CAUSE",
        root_cause_code="SOURCE_PAYMENT_INGESTION_LOSS",
        evidence_ids=(records[0].evidence_id, "ev_" + "0" * 64),
    )

    audit = _refusal_audit(
        _audit_state(records),
        reason_code=CLAIM_SUPPORT_REQUIRED,
        submission=_submission_with((claim,)),
    )

    projected = audit.claims[0]
    assert projected.unregistered_evidence_refs == 1
    assert projected.recomputable is True
    assert projected.recomputability_reason == "RECOMPUTABLE_UNREGISTERED_REF"


def test_audit_marks_health_claim_support_not_recomputable() -> None:
    """The audit projection keeps only relation_name for HEALTH_STATE claims;
    the support rule needs history_name, bucket and current_value too, so an
    otherwise complete health claim is honestly marked not recomputable."""

    records = (_node_error_record("raw_orders"),)
    claim = HealthStateClaim(
        kind="HEALTH_STATE",
        relation_name="raw_orders",
        history_name="order_count",
        bucket="2026-09-22",
        current_value=100,
        evidence_ids=(records[0].evidence_id,),
    )

    audit = _refusal_audit(
        _audit_state(records),
        reason_code=CLAIM_SUPPORT_REQUIRED,
        submission=_submission_with((claim,)),
    )

    projected = audit.claims[0]
    assert projected.recomputable is False
    assert projected.recomputability_reason == "NOT_RECOMPUTABLE_HEALTH_CLAIM_FIELDS"


# ---------------------------------------------------------------------------
# Reader helpers
# ---------------------------------------------------------------------------


def _policy_identity() -> PolicyIdentity:
    return PolicyIdentity(
        strategy=DiagnosticStrategy.STATIC_SKILL,
        base_prompt_version="p1.base.v1",
        base_prompt_sha256="1" * 64,
        strategy_prompt_version="p1.static.v5",
        strategy_prompt_sha256="2" * 64,
        controller_protocol_version="p1.controller.v20",
        controller_protocol_sha256="3" * 64,
        tool_schema_sha256="4" * 64,
    )


def _metrics(tool_prefix: tuple[ToolTraceEvent, ...]) -> DiagnosisMetrics:
    return DiagnosisMetrics(
        provider="synthetic",
        model="synthetic-model",
        model_requests=1,
        input_tokens=0,
        output_tokens=0,
        tool_call_attempts=len(tool_prefix),
        successful_tool_calls=sum(
            event.error_code is None for event in tool_prefix
        ),
        elapsed_ms=1,
    )


def _terminal(records: tuple[EvidenceRecord, ...]) -> DiagnosisTerminalTraceEvent:
    return DiagnosisTerminalTraceEvent(
        event_type="DIAGNOSIS_TERMINAL",
        strategy=DiagnosticStrategy.STATIC_SKILL,
        status=DiagnosisStatus.INSUFFICIENT_EVIDENCE,
        evidence_inventory=tuple(record.evidence_id for record in records),
    )


def _reception_prefix(
    records: tuple[EvidenceRecord, ...],
) -> tuple[ToolTraceEvent, ...]:
    """The tool call that received the given records, before any gate event."""

    return (
        ToolTraceEvent(
            event_type="TOOL_CALL",
            tool_name="get_failed_node_details",
            arguments={"run_id": RUN_ID},
            fingerprint=FINGERPRINT,
            evidence_ids=tuple(record.evidence_id for record in records),
            elapsed_ms=1,
        ),
    )


def _run_result(
    records: tuple[EvidenceRecord, ...],
    gate_events: tuple[EvidenceGateTraceEvent, ...],
    tool_prefix: tuple[ToolTraceEvent, ...] = (),
) -> DiagnosisRunResult:
    diagnosis = Diagnosis(
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
    )
    return DiagnosisRunResult(
        strategy=DiagnosticStrategy.STATIC_SKILL,
        policy_identity=_policy_identity(),
        diagnosis=diagnosis,
        evidence_records=records,
        trace=(*tool_prefix, *gate_events, _terminal(records)),
        metrics=_metrics(tool_prefix),
    )


def _audit_claim(
    *,
    kind: str = "ROOT_CAUSE",
    known_value: str | None = None,
    evidence_ids: tuple[str, ...] = (),
    total: int = 0,
    unregistered: int = 0,
    truncated: int = 0,
    recomputable: bool | None = None,
    recomputability_reason: str | None = None,
    relation_name: str | None = None,
) -> AuditClaimSummary:
    if recomputable is None or recomputability_reason is None:
        from data_incident_gym.diagnosis import derive_claim_recomputability

        recomputable, recomputability_reason = derive_claim_recomputability(
            kind, known_value, unregistered, truncated
        )
    return AuditClaimSummary(
        kind=kind,
        known_value=known_value,
        relation_name=relation_name,
        evidence_ids=evidence_ids,
        total_evidence_refs=total,
        unregistered_evidence_refs=unregistered,
        truncated_evidence_refs=truncated,
        recomputable=recomputable,
        recomputability_reason=recomputability_reason,
    )


def _audit(
    *,
    reason_code: str = CLAIM_SUPPORT_REQUIRED,
    status: str = "CONFIRMED",
    applicable: tuple[str, ...] = ("AFFECTED_ASSET", "ROOT_CAUSE"),
    claims: tuple[AuditClaimSummary, ...] = (),
    unresolved: tuple[AuditUnresolvedSummary, ...] = (),
    truncated_claims: int = 0,
) -> RefusalAudit:
    return RefusalAudit(
        reason_code=reason_code,
        model_request_index=0,
        status=status,
        applicable_claim_kinds=applicable,
        claims=claims,
        unresolved_evidence=unresolved,
        truncated_claim_count=truncated_claims,
        truncated_unresolved_count=0,
    )


def _gate_event(
    audit: RefusalAudit | None,
    *,
    reason_code: str = CLAIM_SUPPORT_REQUIRED,
) -> EvidenceGateTraceEvent:
    return EvidenceGateTraceEvent(
        event_type="EVIDENCE_GATE",
        reason_code=reason_code,
        accepted=False,
        refusal_audit=audit,
    )


def _review(
    records: tuple[EvidenceRecord, ...],
    gate_events: tuple[EvidenceGateTraceEvent, ...],
    case_id: str = CONFIRMED_CASE,
    tool_prefix: tuple[ToolTraceEvent, ...] = (),
):
    scenario = load_scenario_spec(case_id)
    run = _run_result(records, gate_events, tool_prefix)
    return review_refusal_events(run, scenario), scenario


# ---------------------------------------------------------------------------
# Reader: the five regression families
# ---------------------------------------------------------------------------


def test_reader_correct_for_unregistered_ref() -> None:
    records = (_node_error_record("node_0"),)
    claim = _audit_claim(
        kind="ROOT_CAUSE",
        known_value="TRANSFORMATION_REQUIRED_FIELD_NULL",
        evidence_ids=(records[0].evidence_id,),
        total=2,
        unregistered=1,
    )

    verdicts, _ = _review(records, (_gate_event(_audit(claims=(claim,))),))

    assert len(verdicts) == 1
    assert verdicts[0].status is RefusalReviewStatus.CORRECT
    assert BASIS_UNREGISTERED_REF_ESTABLISHED in verdicts[0].basis
    assert verdicts[0].recomputability_disagreements == ()


def test_reader_correct_for_registered_but_unsupported_claim() -> None:
    record = _node_error_record("test.jaffle_shop.unrelated")
    claim = _audit_claim(
        kind="AFFECTED_ASSET",
        known_value="model.jaffle_shop.orders",
        evidence_ids=(record.evidence_id,),
        total=1,
    )

    verdicts, _ = _review(
        (record,),
        (_gate_event(_audit(claims=(claim,))),),
        tool_prefix=_reception_prefix((record,)),
    )

    assert verdicts[0].status is RefusalReviewStatus.CORRECT
    assert BASIS_UNSUPPORTED_CLAIM_ESTABLISHED in verdicts[0].basis


def test_reader_correct_for_mixed_citations() -> None:
    record = _node_error_record("test.jaffle_shop.unrelated")
    unsupported = _audit_claim(
        kind="AFFECTED_ASSET",
        known_value="model.jaffle_shop.orders",
        evidence_ids=(record.evidence_id,),
        total=1,
    )
    unregistered = _audit_claim(
        kind="ROOT_CAUSE",
        known_value="TRANSFORMATION_REQUIRED_FIELD_NULL",
        evidence_ids=(),
        total=1,
        unregistered=1,
    )

    verdicts, _ = _review(
        (record,),
        (_gate_event(_audit(claims=(unsupported, unregistered))),),
        tool_prefix=_reception_prefix((record,)),
    )

    assert verdicts[0].status is RefusalReviewStatus.CORRECT
    assert BASIS_UNSUPPORTED_CLAIM_ESTABLISHED in verdicts[0].basis
    assert BASIS_UNREGISTERED_REF_ESTABLISHED in verdicts[0].basis


def test_reader_false_refusal_for_compliant_submission() -> None:
    record = _node_error_record("model.jaffle_shop.orders")
    claim = _audit_claim(
        kind="AFFECTED_ASSET",
        known_value="model.jaffle_shop.orders",
        evidence_ids=(record.evidence_id,),
        total=1,
    )

    verdicts, _ = _review(
        (record,),
        (_gate_event(_audit(claims=(claim,))),),
        tool_prefix=_reception_prefix((record,)),
    )

    assert verdicts[0].status is RefusalReviewStatus.FALSE_REFUSAL
    assert BASIS_ALL_APPLICABLE_CLAIMS_SUPPORTED in verdicts[0].basis


def test_reader_verdict_survives_serialization_round_trip() -> None:
    record = _node_error_record("model.jaffle_shop.orders")
    claim = _audit_claim(
        kind="AFFECTED_ASSET",
        known_value="model.jaffle_shop.orders",
        evidence_ids=(record.evidence_id,),
        total=1,
    )
    event = _gate_event(_audit(claims=(claim,)))
    reloaded = EvidenceGateTraceEvent.model_validate_json(event.model_dump_json())

    assert reloaded.refusal_audit is not None
    original, _ = _review(
        (record,), (event,), tool_prefix=_reception_prefix((record,))
    )
    round_tripped, _ = _review(
        (record,), (reloaded,), tool_prefix=_reception_prefix((record,))
    )

    assert original == round_tripped


# ---------------------------------------------------------------------------
# Reader: I2 gap receipts
# ---------------------------------------------------------------------------


def _gap_witness() -> ToolTraceEvent:
    return ToolTraceEvent(
        event_type="TOOL_CALL",
        tool_name="get_relation_data_profile",
        arguments={"relation_name": "raw_payments"},
        fingerprint=FINGERPRINT,
        evidence_ids=(),
        error_code="RELATION_NOT_ALLOWED",
        elapsed_ms=1,
    )


def _gap_audit(unresolved: tuple[AuditUnresolvedSummary, ...]) -> RefusalAudit:
    return _audit(
        reason_code=GAP_RECEIPT_REQUIRED,
        status="INSUFFICIENT_EVIDENCE",
        applicable=(),
        unresolved=unresolved,
    )


def _declared_gap() -> AuditUnresolvedSummary:
    return AuditUnresolvedSummary(
        evidence_kind="RELATION_DATA_PROFILE",
        reason_code="RELATION_NOT_ALLOWED",
        subject="raw_payments",
    )


def test_reader_i2_correct_when_receipt_missing() -> None:
    verdicts, _ = _review(
        (),
        (_gate_event(_gap_audit((_declared_gap(),)), reason_code=GAP_RECEIPT_REQUIRED),),
        case_id=INSUFFICIENT_CASE,
    )

    assert verdicts[0].status is RefusalReviewStatus.CORRECT
    assert "GAP_WITHOUT_RECEIPT_ESTABLISHED" in verdicts[0].basis


def test_reader_i2_false_refusal_when_receipt_witnessed() -> None:
    verdicts, _ = _review(
        (),
        (_gate_event(_gap_audit((_declared_gap(),)), reason_code=GAP_RECEIPT_REQUIRED),),
        case_id=INSUFFICIENT_CASE,
        tool_prefix=(_gap_witness(),),
    )

    assert verdicts[0].status is RefusalReviewStatus.FALSE_REFUSAL
    assert "ALL_CONTRACT_GAPS_WITNESSED" in verdicts[0].basis


# ---------------------------------------------------------------------------
# Reader: indeterminable families (never counted as correct)
# ---------------------------------------------------------------------------


def test_reader_indeterminable_when_audit_absent() -> None:
    legacy_payload = {
        "event_type": "EVIDENCE_GATE",
        "reason_code": "CLAIM_SUPPORT_REQUIRED",
        "accepted": False,
    }
    event = EvidenceGateTraceEvent.model_validate(legacy_payload)
    assert event.refusal_audit is None

    verdicts, _ = _review((), (event,))

    assert verdicts[0].status is RefusalReviewStatus.INDETERMINABLE
    assert BASIS_AUDIT_ABSENT in verdicts[0].basis


def test_reader_indeterminable_for_kernel_contract_rejection() -> None:
    event = EvidenceGateTraceEvent(
        event_type="EVIDENCE_GATE",
        reason_code="CLAIMS_INCOMPLETE",
        accepted=False,
        rejected_decision=RejectedDecisionSummary(
            model_request_index=0,
            status="CONFIRMED",
        ),
    )

    verdicts, _ = _review((), (event,))

    assert verdicts[0].status is RefusalReviewStatus.INDETERMINABLE
    assert BASIS_KERNEL_CONTRACT_PATHWAY in verdicts[0].basis


def test_reader_indeterminable_when_count_identity_violated() -> None:
    claim = _audit_claim(
        kind="ROOT_CAUSE",
        known_value="TRANSFORMATION_REQUIRED_FIELD_NULL",
        evidence_ids=(),
        total=5,
        unregistered=0,
        truncated=0,
    )

    verdicts, _ = _review((), (_gate_event(_audit(claims=(claim,))),))

    assert verdicts[0].status is RefusalReviewStatus.INDETERMINABLE
    assert BASIS_COUNT_IDENTITY_VIOLATED in verdicts[0].basis


def test_reader_indeterminable_when_claims_truncated() -> None:
    record = _node_error_record("model.jaffle_shop.orders")
    claim = _audit_claim(
        kind="AFFECTED_ASSET",
        known_value="model.jaffle_shop.orders",
        evidence_ids=(record.evidence_id,),
        total=1,
    )

    verdicts, _ = _review(
        (record,),
        (_gate_event(_audit(claims=(claim,), truncated_claims=1)),),
    )

    assert verdicts[0].status is RefusalReviewStatus.INDETERMINABLE
    assert BASIS_CLAIMS_TRUNCATED in verdicts[0].basis


def test_reader_indeterminable_for_health_claims() -> None:
    record = _node_error_record("raw_orders")
    claim = _audit_claim(
        kind="HEALTH_STATE",
        relation_name="raw_orders",
        evidence_ids=(record.evidence_id,),
        total=1,
    )

    verdicts, _ = _review(
        (record,),
        (_gate_event(_audit(applicable=("HEALTH_STATE",), claims=(claim,))),),
        case_id=NO_INCIDENT_CASE,
    )

    assert verdicts[0].status is RefusalReviewStatus.INDETERMINABLE


def test_reader_indeterminable_when_audit_evidence_outside_prefix() -> None:
    record = _node_error_record("model.jaffle_shop.orders")
    claim = _audit_claim(
        kind="AFFECTED_ASSET",
        known_value="model.jaffle_shop.orders",
        evidence_ids=("ev_" + "9" * 64,),
        total=1,
    )

    verdicts, _ = _review(
        (record,),
        (_gate_event(_audit(claims=(claim,))),),
        tool_prefix=_reception_prefix((record,)),
    )

    assert verdicts[0].status is RefusalReviewStatus.INDETERMINABLE
    assert BASIS_AUDIT_EVIDENCE_OUTSIDE_PREFIX in verdicts[0].basis


def test_reader_indeterminable_for_gate_internal_error() -> None:
    audit = _audit(reason_code="GATE_INTERNAL_ERROR")

    verdicts, _ = _review((), (_gate_event(audit, reason_code="GATE_INTERNAL_ERROR"),))

    assert verdicts[0].status is RefusalReviewStatus.INDETERMINABLE
    assert BASIS_GATE_INTERNAL_ERROR in verdicts[0].basis


def test_reader_indeterminable_when_reason_codes_disagree() -> None:
    audit = _audit(reason_code=GAP_RECEIPT_REQUIRED)

    verdicts, _ = _review((), (_gate_event(audit),))

    assert verdicts[0].status is RefusalReviewStatus.INDETERMINABLE
    assert BASIS_REASON_CODE_MISMATCH in verdicts[0].basis


def test_reader_indeterminable_when_applicable_kinds_disagree() -> None:
    record = _node_error_record("model.jaffle_shop.orders")
    claim = _audit_claim(
        kind="AFFECTED_ASSET",
        known_value="model.jaffle_shop.orders",
        evidence_ids=(record.evidence_id,),
        total=1,
    )
    audit = _audit(claims=(claim,), applicable=())

    verdicts, _ = _review((record,), (_gate_event(audit),))

    assert verdicts[0].status is RefusalReviewStatus.INDETERMINABLE
    assert "APPLICABLE_KINDS_MISMATCH" in verdicts[0].basis


def test_reader_false_refusal_when_i1_not_applicable() -> None:
    claim = _audit_claim(
        kind="ROOT_CAUSE",
        known_value="SOURCE_PAYMENT_INGESTION_LOSS",
        evidence_ids=(),
        total=1,
        unregistered=1,
    )
    audit = _audit(
        claims=(claim,),
        status="INSUFFICIENT_EVIDENCE",
        applicable=(),
    )

    verdicts, _ = _review((), (_gate_event(audit),), case_id=INSUFFICIENT_CASE)

    assert verdicts[0].status is RefusalReviewStatus.FALSE_REFUSAL
    assert BASIS_I1_NOT_APPLICABLE in verdicts[0].basis


# ---------------------------------------------------------------------------
# Reader: the stored boolean is re-derived, never trusted
# ---------------------------------------------------------------------------


def test_reader_records_recomputability_disagreement_without_trusting_it() -> None:
    record = _node_error_record("model.jaffle_shop.orders")
    claim = _audit_claim(
        kind="AFFECTED_ASSET",
        known_value="model.jaffle_shop.orders",
        evidence_ids=(record.evidence_id,),
        total=1,
        recomputable=False,
        recomputability_reason="RECOMPUTABLE_PROJECTED_CLAIM",
    )

    verdicts, _ = _review(
        (record,),
        (_gate_event(_audit(claims=(claim,))),),
        tool_prefix=_reception_prefix((record,)),
    )

    assert verdicts[0].recomputability_disagreements == (0,)
    assert verdicts[0].status is RefusalReviewStatus.FALSE_REFUSAL


# ---------------------------------------------------------------------------
# Historical compatibility: strict loads, no silent zero defaults
# ---------------------------------------------------------------------------


def test_audit_claim_missing_count_fails_strict_load() -> None:
    payload = json.loads(
        _audit_claim(
            kind="ROOT_CAUSE",
            known_value="TRANSFORMATION_REQUIRED_FIELD_NULL",
            total=1,
        ).model_dump_json()
    )
    del payload["total_evidence_refs"]
    del payload["unregistered_evidence_refs"]
    del payload["truncated_evidence_refs"]

    with pytest.raises(ValidationError):
        AuditClaimSummary.model_validate(payload)


def test_reader_i2_indeterminable_when_unresolved_truncated() -> None:
    audit = _gap_audit(())  # all declared gaps dropped by the item cap
    audit = audit.model_copy(update={"truncated_unresolved_count": 1})

    verdicts, _ = _review(
        (),
        (_gate_event(audit, reason_code=GAP_RECEIPT_REQUIRED),),
        case_id=INSUFFICIENT_CASE,
    )

    assert verdicts[0].status is RefusalReviewStatus.INDETERMINABLE
    assert "UNRESOLVED_TRUNCATED" in verdicts[0].basis


def test_reader_i2_indeterminable_when_subject_unavailable() -> None:
    hidden = AuditUnresolvedSummary(
        evidence_kind="RELATION_DATA_PROFILE",
        reason_code="RELATION_NOT_ALLOWED",
        subject=None,
    )

    verdicts, _ = _review(
        (),
        (_gate_event(_gap_audit((hidden,)), reason_code=GAP_RECEIPT_REQUIRED),),
        case_id=INSUFFICIENT_CASE,
    )

    assert verdicts[0].status is RefusalReviewStatus.INDETERMINABLE
    assert "SUBJECT_UNAVAILABLE" in verdicts[0].basis
