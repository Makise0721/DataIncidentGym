from __future__ import annotations

from datetime import UTC, datetime

import pytest

from data_incident_gym.diagnostic_kernel import (
    ClaimEvidence,
    ClaimKind,
    DiagnosticKernel,
    EvidenceGapKind,
    EvidenceGapStatus,
    Hypothesis,
    HypothesisAssessment,
    HypothesisVerdict,
    InvestigationIntent,
    KernelDecision,
    KernelError,
)
from data_incident_gym.evidence import (
    DbtLineageFact,
    DbtLineageNode,
    DbtNodeErrorFact,
    DbtRunResultsFact,
    EvidenceRecord,
    EvidenceSource,
    EvidenceType,
    RelationSchemaColumn,
    RelationSchemaFact,
)

RUN_ID = "a" * 32


def _kernel(*, tool_call_limit: int = 8) -> DiagnosticKernel:
    return DiagnosticKernel.start(
        run_id=RUN_ID,
        allowed_root_cause_codes=(
            "SOURCE_SCHEMA_COLUMN_RENAMED",
            "SOURCE_SCHEMA_COLUMN_TYPE_CHANGED",
        ),
        model_request_limit=8,
        tool_call_limit=tool_call_limit,
        observable_relations=("raw_payments",),
    )


def _run_results() -> EvidenceRecord:
    return EvidenceRecord.create(
        run_id=RUN_ID,
        evidence_type=EvidenceType.DBT_RUN_RESULTS,
        source=EvidenceSource.DBT_RUN_RESULTS,
        subject=RUN_ID,
        observed_at=datetime(2026, 8, 30, tzinfo=UTC),
        content=DbtRunResultsFact(
            kind="DBT_RUN_RESULTS",
            run_id=RUN_ID,
            run_status="FAILED",
            dbt_exit_code=1,
            failed_nodes=("model.jaffle_shop.stg_payments",),
            skipped_nodes=(),
        ),
    )


def test_kernel_state_is_public_case_neutral_and_budgeted() -> None:
    state = _kernel().snapshot(model_requests_used=0)

    assert state.schema_version == "p1.investigation.v1"
    assert state.run_id == RUN_ID
    assert state.tool_call_limit == 8
    assert state.tool_calls_used == 0
    assert state.model_requests_remaining == 8
    assert "incident_case_id" not in state.model_dump()
    assert "expected_status" not in state.model_dump()


def test_kernel_business_tool_arguments_do_not_carry_kernel_intent() -> None:
    kernel = _kernel()
    intent = InvestigationIntent(gap_id="g_locate", gap_kind=EvidenceGapKind.LOCATE_FAILURE)

    with pytest.raises(KernelError) as error:
        kernel.prepare_tool(
            intent=intent,
            tool_name="get_dbt_run_results",
            arguments={"run_id": RUN_ID, "gap_id": "g_locate"},
        )

    assert error.value.code == "ARGUMENTS_INVALID"
    assert kernel.snapshot(model_requests_used=0).tool_calls_used == 0


def test_kernel_closes_a_gap_only_after_compatible_evidence() -> None:
    kernel = _kernel()
    intent = InvestigationIntent(gap_id="g_locate", gap_kind=EvidenceGapKind.LOCATE_FAILURE)
    prepared = kernel.prepare_tool(
        intent=intent,
        tool_name="get_dbt_run_results",
        arguments={"run_id": RUN_ID},
    )

    record = _run_results()
    assert kernel.record_tool_result(prepared, (record,)) == (record,)
    state = kernel.snapshot(model_requests_used=1)

    assert state.gaps[0].status is EvidenceGapStatus.CLOSED
    assert state.evidence_inventory == (record.evidence_id,)
    assert state.tool_calls_used == 1


def test_kernel_rejects_unproven_node_and_relation_arguments() -> None:
    kernel = _kernel()
    node_intent = InvestigationIntent(gap_id="g_explain", gap_kind=EvidenceGapKind.EXPLAIN_FAILURE)
    with pytest.raises(KernelError, match="NODE_ARGUMENT_NOT_PROVEN"):
        kernel.prepare_tool(
            intent=node_intent,
            tool_name="get_dbt_node_error",
            arguments={"run_id": RUN_ID, "node_id": "model.unknown"},
        )

    relation_intent = InvestigationIntent(
        gap_id="g_schema",
        gap_kind=EvidenceGapKind.DISCRIMINATE_SCHEMA,
    )
    with pytest.raises(KernelError, match="RELATION_ARGUMENT_NOT_PROVEN"):
        kernel.prepare_tool(
            intent=relation_intent,
            tool_name="get_relation_schema",
            arguments={"relation_name": "raw_orders"},
        )


def test_kernel_rejects_duplicate_tool_attempt_before_state_change() -> None:
    kernel = _kernel()
    intent = InvestigationIntent(gap_id="g_locate", gap_kind=EvidenceGapKind.LOCATE_FAILURE)
    arguments = {"run_id": RUN_ID}
    kernel.prepare_tool(intent=intent, tool_name="get_dbt_run_results", arguments=arguments)
    before = kernel.snapshot(model_requests_used=0)

    with pytest.raises(KernelError) as error:
        kernel.prepare_tool(
            intent=InvestigationIntent(gap_id="g_again", gap_kind=EvidenceGapKind.LOCATE_FAILURE),
            tool_name="get_dbt_run_results",
            arguments=arguments,
        )

    assert error.value.code == "DUPLICATE_TOOL_CALL"
    assert kernel.snapshot(model_requests_used=0) == before


def test_kernel_rejects_cross_run_evidence_without_recording_it() -> None:
    kernel = _kernel()
    prepared = kernel.prepare_tool(
        intent=InvestigationIntent(gap_id="g_locate", gap_kind=EvidenceGapKind.LOCATE_FAILURE),
        tool_name="get_dbt_run_results",
        arguments={"run_id": RUN_ID},
    )
    other_run_record = _run_results().model_copy(update={"run_id": "b" * 32})

    with pytest.raises(KernelError) as error:
        kernel.record_tool_result(prepared, (other_run_record,))

    assert error.value.code == "RUN_CONTEXT_MISMATCH"
    assert kernel.evidence_records == ()
    assert kernel.snapshot(model_requests_used=0).evidence_inventory == ()


def test_kernel_rejects_tool_budget_exhaustion_before_state_change() -> None:
    kernel = _kernel(tool_call_limit=1)
    kernel.prepare_tool(
        intent=InvestigationIntent(gap_id="g_locate", gap_kind=EvidenceGapKind.LOCATE_FAILURE),
        tool_name="get_dbt_run_results",
        arguments={"run_id": RUN_ID},
    )
    before = kernel.snapshot(model_requests_used=0)

    with pytest.raises(KernelError) as error:
        kernel.prepare_tool(
            intent=InvestigationIntent(
                gap_id="g_schema",
                gap_kind=EvidenceGapKind.DISCRIMINATE_SCHEMA,
            ),
            tool_name="get_relation_schema",
            arguments={"relation_name": "raw_orders"},
        )

    assert error.value.code == "TOOL_CALL_LIMIT"
    assert kernel.snapshot(model_requests_used=0) == before


def _insufficient_decision(unresolved_evidence: tuple[dict[str, str], ...]) -> KernelDecision:
    return KernelDecision(
        status="INSUFFICIENT_EVIDENCE",
        run_id=RUN_ID,
        unresolved_evidence=unresolved_evidence,
        summary="The decisive evidence is unavailable.",
        recommended_actions=(),
        confidence=0.2,
    )


def test_kernel_binds_blocked_profile_gap_to_profile_unresolved_evidence() -> None:
    kernel = DiagnosticKernel.start(
        run_id=RUN_ID,
        allowed_root_cause_codes=(
            "SOURCE_REQUIRED_FIELD_NULL",
            "TRANSFORMATION_REQUIRED_FIELD_NULL",
        ),
        model_request_limit=8,
        tool_call_limit=8,
        observable_profile_relations=("raw_orders",),
    )
    prepared = kernel.prepare_tool(
        intent=InvestigationIntent(
            gap_id="g_profile",
            gap_kind=EvidenceGapKind.PROFILE_RELATION,
            new_hypotheses=(
                Hypothesis(
                    hypothesis_id="h_source_null",
                    root_cause_code="SOURCE_REQUIRED_FIELD_NULL",
                ),
                Hypothesis(
                    hypothesis_id="h_transform_null",
                    root_cause_code="TRANSFORMATION_REQUIRED_FIELD_NULL",
                ),
            ),
        ),
        tool_name="get_relation_data_profile",
        arguments={"relation_name": "raw_orders"},
    )
    kernel.record_tool_failure(prepared, "RELATION_NOT_ALLOWED")

    outcome = kernel.finalize(
        _insufficient_decision(
            (
                {
                    "evidence_kind": "RELATION_DATA_PROFILE",
                    "subject": "raw_orders",
                    "reason_code": "RELATION_NOT_ALLOWED",
                },
            )
        )
    )

    assert outcome.status.value == "INSUFFICIENT_EVIDENCE"
    assert outcome.unresolved_evidence[0].evidence_kind == "RELATION_DATA_PROFILE"


@pytest.mark.parametrize(
    ("blocked_kind", "declared_kind", "declared_subject"),
    (
        (EvidenceGapKind.DISCRIMINATE_SCHEMA, "RELATION_DATA_PROFILE", "raw_orders"),
        (EvidenceGapKind.PROFILE_RELATION, "RELATION_DATA_PROFILE", "raw_customers"),
    ),
)
def test_kernel_does_not_bind_profile_gap_to_wrong_blocked_evidence(
    blocked_kind: EvidenceGapKind,
    declared_kind: str,
    declared_subject: str,
) -> None:
    kernel = DiagnosticKernel.start(
        run_id=RUN_ID,
        allowed_root_cause_codes=("CAUSE_A", "CAUSE_B"),
        model_request_limit=8,
        tool_call_limit=8,
        observable_schema_relations=("raw_orders",),
        observable_profile_relations=("raw_orders", "raw_customers"),
    )
    tool_name = (
        "get_relation_schema"
        if blocked_kind is EvidenceGapKind.DISCRIMINATE_SCHEMA
        else "get_relation_data_profile"
    )
    prepared = kernel.prepare_tool(
        intent=InvestigationIntent(
            gap_id="g_wrong",
            gap_kind=blocked_kind,
            new_hypotheses=(
                Hypothesis(hypothesis_id="h_a", root_cause_code="CAUSE_A"),
                Hypothesis(hypothesis_id="h_b", root_cause_code="CAUSE_B"),
            ),
        ),
        tool_name=tool_name,
        arguments={"relation_name": "raw_orders"},
    )
    kernel.record_tool_failure(prepared, "RELATION_NOT_ALLOWED")

    with pytest.raises(KernelError, match="UNRESOLVED_EVIDENCE_UNBOUND"):
        kernel.finalize(
            _insufficient_decision(
                (
                    {
                        "evidence_kind": declared_kind,
                        "subject": declared_subject,
                        "reason_code": "RELATION_NOT_ALLOWED",
                    },
                )
            )
        )


def _record(
    evidence_type: EvidenceType,
    source: EvidenceSource,
    subject: str,
    content: object,
) -> EvidenceRecord:
    return EvidenceRecord.create(
        run_id=RUN_ID,
        evidence_type=evidence_type,
        source=source,
        subject=subject,
        observed_at=datetime(2026, 8, 30, tzinfo=UTC),
        content=content,
    )


def _close(
    kernel: DiagnosticKernel,
    *,
    gap_id: str,
    gap_kind: EvidenceGapKind,
    tool_name: str,
    arguments: dict[str, str],
    record: EvidenceRecord,
    hypothesis_ids: tuple[str, ...] = (),
    new_hypotheses: tuple[Hypothesis, ...] = (),
) -> None:
    prepared = kernel.prepare_tool(
        intent=InvestigationIntent(
            gap_id=gap_id,
            gap_kind=gap_kind,
            hypothesis_ids=hypothesis_ids,
            new_hypotheses=new_hypotheses,
        ),
        tool_name=tool_name,
        arguments=arguments,
    )
    kernel.record_tool_result(prepared, (record,))


def test_kernel_accepts_distance_one_model_for_failed_test_asset_claim() -> None:
    test_id = "test.jaffle_shop.not_null_orders_customer_id.c5f02694af"
    model_id = "model.jaffle_shop.orders"
    kernel = DiagnosticKernel.start(
        run_id=RUN_ID,
        allowed_root_cause_codes=("SOURCE_REQUIRED_FIELD_NULL", "OTHER_CAUSE"),
        model_request_limit=8,
        tool_call_limit=8,
        observable_schema_relations=("raw_orders",),
        incident_subjects=(test_id,),
    )
    run_record = _record(
        EvidenceType.DBT_RUN_RESULTS,
        EvidenceSource.DBT_RUN_RESULTS,
        RUN_ID,
        DbtRunResultsFact(
            kind="DBT_RUN_RESULTS",
            run_id=RUN_ID,
            run_status="FAILED",
            dbt_exit_code=1,
            failed_nodes=(test_id,),
            skipped_nodes=(),
        ),
    )
    _close(
        kernel,
        gap_id="g_locate_test",
        gap_kind=EvidenceGapKind.LOCATE_FAILURE,
        tool_name="get_dbt_run_results",
        arguments={"run_id": RUN_ID},
        record=run_record,
    )
    node_error_record = _record(
        EvidenceType.DBT_NODE_ERROR,
        EvidenceSource.DBT_RUN_RESULTS,
        test_id,
        DbtNodeErrorFact(
            kind="DBT_NODE_ERROR",
            run_id=RUN_ID,
            node_id=test_id,
            resource_type="test",
            status="fail",
            message="required field is null",
        ),
    )
    _close(
        kernel,
        gap_id="g_explain_test",
        gap_kind=EvidenceGapKind.EXPLAIN_FAILURE,
        tool_name="get_dbt_node_error",
        arguments={"run_id": RUN_ID, "node_id": test_id},
        record=node_error_record,
    )
    lineage_record = _record(
        EvidenceType.DBT_LINEAGE,
        EvidenceSource.DBT_MANIFEST,
        test_id,
        DbtLineageFact(
            kind="DBT_LINEAGE",
            run_id=RUN_ID,
            node_id=test_id,
            direction="upstream",
            related_nodes=(
                DbtLineageNode(
                    node_id=model_id,
                    resource_type="model",
                    name="orders",
                    distance=1,
                ),
                DbtLineageNode(
                    node_id="source.jaffle_shop.raw_orders",
                    resource_type="source",
                    name="raw_orders",
                    distance=2,
                ),
            ),
        ),
    )
    _close(
        kernel,
        gap_id="g_source_test",
        gap_kind=EvidenceGapKind.DISCOVER_SOURCE_RELATION,
        tool_name="get_dbt_lineage",
        arguments={"node_id": test_id, "direction": "upstream"},
        record=lineage_record,
    )
    schema_record = _record(
        EvidenceType.RELATION_SCHEMA,
        EvidenceSource.POSTGRES_CATALOG,
        "raw_orders",
        RelationSchemaFact(
            kind="RELATION_SCHEMA",
            run_id=RUN_ID,
            schema_name="staging",
            relation_name="raw_orders",
            columns=(
                RelationSchemaColumn(
                    name="user_id",
                    data_type="integer",
                    nullable=True,
                    ordinal_position=1,
                ),
            ),
        ),
    )
    _close(
        kernel,
        gap_id="g_schema_test",
        gap_kind=EvidenceGapKind.DISCRIMINATE_SCHEMA,
        tool_name="get_relation_schema",
        arguments={"relation_name": "raw_orders"},
        record=schema_record,
        hypothesis_ids=("h_source", "h_other"),
        new_hypotheses=(
            Hypothesis(
                hypothesis_id="h_source",
                root_cause_code="SOURCE_REQUIRED_FIELD_NULL",
            ),
            Hypothesis(hypothesis_id="h_other", root_cause_code="OTHER_CAUSE"),
        ),
    )

    outcome = kernel.finalize(
        KernelDecision(
            status="CONFIRMED",
            run_id=RUN_ID,
            selected_hypothesis_id="h_source",
            assessments=(
                HypothesisAssessment(
                    hypothesis_id="h_source",
                    verdict=HypothesisVerdict.SUPPORTED,
                    evidence_ids=(node_error_record.evidence_id, schema_record.evidence_id),
                ),
                HypothesisAssessment(
                    hypothesis_id="h_other",
                    verdict=HypothesisVerdict.REFUTED,
                    evidence_ids=(schema_record.evidence_id,),
                ),
            ),
            claims=(
                ClaimEvidence(
                    kind=ClaimKind.ROOT_CAUSE,
                    value="SOURCE_REQUIRED_FIELD_NULL",
                    evidence_ids=(node_error_record.evidence_id, schema_record.evidence_id),
                ),
                ClaimEvidence(
                    kind=ClaimKind.AFFECTED_ASSET,
                    value=model_id,
                    evidence_ids=(lineage_record.evidence_id,),
                ),
            ),
            summary="The required source field is null.",
            recommended_actions=(),
            confidence=0.9,
        )
    )

    assert outcome.affected_assets == (model_id,)
