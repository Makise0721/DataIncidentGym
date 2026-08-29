from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

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
    KernelFinalStatus,
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
CASE_ID = "synthetic_case"
ONTOLOGY = (
    "SOURCE_SCHEMA_COLUMN_RENAMED",
    "SOURCE_SCHEMA_COLUMN_TYPE_CHANGED",
)


def test_start_exposes_one_small_frozen_state_interface() -> None:
    kernel = DiagnosticKernel.start(
        incident_case_id=CASE_ID,
        run_id=RUN_ID,
        allowed_root_cause_codes=ONTOLOGY,
        model_request_limit=8,
        tool_call_limit=8,
    )
    state = kernel.snapshot(model_requests_used=0)
    assert state.schema_version == "m6.investigation.v1"
    assert state.incident_case_id == CASE_ID
    assert state.run_id == RUN_ID
    assert state.revision == 0
    assert state.hypotheses == ()
    assert state.gaps == ()
    assert state.evidence_inventory == ()
    assert state.model_requests_remaining == 8
    assert state.tool_calls_remaining == 8
    with pytest.raises(ValidationError):
        state.revision = 1


def test_public_models_forbid_extra_fields_and_coercion() -> None:
    with pytest.raises(ValidationError):
        Hypothesis.model_validate(
            {
                "hypothesis_id": "h_rename",
                "root_cause_code": "SOURCE_SCHEMA_COLUMN_RENAMED",
                "extra": True,
            }
        )
    with pytest.raises(ValidationError):
        InvestigationIntent.model_validate(
            {
                "gap_id": "g_failure",
                "gap_kind": "LOCATE_FAILURE",
                "hypothesis_ids": [],
                "new_hypotheses": [],
                "unexpected": "value",
            }
        )


FAILED_NODE = "model.jaffle_shop.stg_payments"


def _kernel() -> DiagnosticKernel:
    return DiagnosticKernel.start(
        incident_case_id=CASE_ID,
        run_id=RUN_ID,
        allowed_root_cause_codes=ONTOLOGY,
        model_request_limit=8,
        tool_call_limit=8,
    )


def _run_results_record(*, run_id: str = RUN_ID) -> EvidenceRecord:
    return EvidenceRecord.create(
        run_id=run_id,
        evidence_type=EvidenceType.DBT_RUN_RESULTS,
        source=EvidenceSource.DBT_RUN_RESULTS,
        subject=run_id,
        observed_at=datetime(2026, 8, 25, 9, tzinfo=UTC),
        content=DbtRunResultsFact(
            kind="DBT_RUN_RESULTS",
            run_id=run_id,
            run_status="FAILED",
            dbt_exit_code=1,
            failed_nodes=(FAILED_NODE,),
            skipped_nodes=("model.jaffle_shop.orders", "model.jaffle_shop.customers"),
        ),
    )


def _node_error_record(*, run_id: str = RUN_ID, node_id: str = FAILED_NODE) -> EvidenceRecord:
    return EvidenceRecord.create(
        run_id=run_id,
        evidence_type=EvidenceType.DBT_NODE_ERROR,
        source=EvidenceSource.DBT_RUN_RESULTS,
        subject=node_id,
        observed_at=datetime(2026, 8, 25, 9, 1, tzinfo=UTC),
        content=DbtNodeErrorFact(
            kind="DBT_NODE_ERROR",
            run_id=run_id,
            node_id=node_id,
            resource_type="model",
            status="error",
            message='column "amount" does not exist',
        ),
    )


def _lineage_record(
    *,
    direction: str,
    related_nodes: tuple[DbtLineageNode, ...],
    run_id: str = RUN_ID,
    node_id: str = FAILED_NODE,
) -> EvidenceRecord:
    return EvidenceRecord.create(
        run_id=run_id,
        evidence_type=EvidenceType.DBT_LINEAGE,
        source=EvidenceSource.DBT_MANIFEST,
        subject=node_id,
        observed_at=datetime(2026, 8, 25, 9, 2, tzinfo=UTC),
        content=DbtLineageFact(
            kind="DBT_LINEAGE",
            run_id=run_id,
            node_id=node_id,
            direction=direction,
            related_nodes=related_nodes,
        ),
    )


def _upstream_lineage_record() -> EvidenceRecord:
    return _lineage_record(
        direction="upstream",
        related_nodes=(
            DbtLineageNode(
                node_id="seed.jaffle_shop.raw_payments",
                resource_type="seed",
                name="raw_payments",
                distance=1,
            ),
        ),
    )


def _downstream_lineage_record() -> EvidenceRecord:
    return _lineage_record(
        direction="downstream",
        related_nodes=(
            DbtLineageNode(
                node_id="model.jaffle_shop.orders",
                resource_type="model",
                name="orders",
                distance=1,
            ),
            DbtLineageNode(
                node_id="model.jaffle_shop.customers",
                resource_type="model",
                name="customers",
                distance=1,
            ),
        ),
    )


def _schema_record(
    amount_type: str = "integer", *, amount_name: str = "amount"
) -> EvidenceRecord:
    return EvidenceRecord.create(
        run_id=RUN_ID,
        evidence_type=EvidenceType.RELATION_SCHEMA,
        source=EvidenceSource.POSTGRES_CATALOG,
        subject="analytics.raw_payments",
        observed_at=datetime(2026, 8, 25, 9, 3, tzinfo=UTC),
        content=RelationSchemaFact(
            kind="RELATION_SCHEMA",
            run_id=RUN_ID,
            schema_name="analytics",
            relation_name="raw_payments",
            columns=(
                RelationSchemaColumn(
                    name="id", data_type="integer", nullable=True, ordinal_position=1
                ),
                RelationSchemaColumn(
                    name="order_id", data_type="integer", nullable=True, ordinal_position=2
                ),
                RelationSchemaColumn(
                    name="payment_method", data_type="text", nullable=True, ordinal_position=3
                ),
                RelationSchemaColumn(
                    name=amount_name,
                    data_type=amount_type,
                    nullable=True,
                    ordinal_position=4,
                ),
            ),
        ),
    )


def _record(
    kernel: DiagnosticKernel,
    *,
    intent: InvestigationIntent,
    tool_name: str,
    arguments: dict[str, str],
    record: EvidenceRecord,
) -> None:
    prepared = kernel.prepare_tool(
        intent=intent,
        tool_name=tool_name,
        arguments=arguments,
    )
    kernel.record_tool_result(prepared, (record,))


def _kernel_with_failure_error_and_upstream_lineage() -> DiagnosticKernel:
    kernel = _kernel()
    _record(
        kernel,
        intent=InvestigationIntent(
            gap_id="g_failure",
            gap_kind=EvidenceGapKind.LOCATE_FAILURE,
        ),
        tool_name="get_dbt_run_results",
        arguments={"run_id": RUN_ID},
        record=_run_results_record(),
    )
    _record(
        kernel,
        intent=InvestigationIntent(
            gap_id="g_explain",
            gap_kind=EvidenceGapKind.EXPLAIN_FAILURE,
        ),
        tool_name="get_dbt_node_error",
        arguments={"node_id": FAILED_NODE, "run_id": RUN_ID},
        record=_node_error_record(),
    )
    _record(
        kernel,
        intent=InvestigationIntent(
            gap_id="g_source",
            gap_kind=EvidenceGapKind.DISCOVER_SOURCE_RELATION,
        ),
        tool_name="get_dbt_lineage",
        arguments={"direction": "upstream", "node_id": FAILED_NODE},
        record=_upstream_lineage_record(),
    )
    return kernel


def _kernel_with_run_results_only() -> DiagnosticKernel:
    kernel = _kernel()
    _record(
        kernel,
        intent=InvestigationIntent(
            gap_id="g_failure",
            gap_kind=EvidenceGapKind.LOCATE_FAILURE,
        ),
        tool_name="get_dbt_run_results",
        arguments={"run_id": RUN_ID},
        record=_run_results_record(),
    )
    kernel.prepare_tool(
        intent=InvestigationIntent(
            gap_id="g_explain",
            gap_kind=EvidenceGapKind.EXPLAIN_FAILURE,
        ),
        tool_name="get_dbt_node_error",
        arguments={"node_id": FAILED_NODE, "run_id": RUN_ID},
    )
    return kernel


def _complete_investigation(*, fault_column_name: str) -> tuple[
    DiagnosticKernel, tuple[EvidenceRecord, ...]
]:
    kernel = _kernel_with_failure_error_and_upstream_lineage()
    _record(
        kernel,
        intent=InvestigationIntent(
            gap_id="g_schema",
            gap_kind=EvidenceGapKind.DISCRIMINATE_SCHEMA,
            hypothesis_ids=("h_rename", "h_type"),
            new_hypotheses=(
                Hypothesis(
                    hypothesis_id="h_rename",
                    root_cause_code="SOURCE_SCHEMA_COLUMN_RENAMED",
                ),
                Hypothesis(
                    hypothesis_id="h_type",
                    root_cause_code="SOURCE_SCHEMA_COLUMN_TYPE_CHANGED",
                ),
            ),
        ),
        tool_name="get_relation_schema",
        arguments={"relation_name": "raw_payments"},
        record=_schema_record(amount_name=fault_column_name),
    )
    _record(
        kernel,
        intent=InvestigationIntent(
            gap_id="g_impact",
            gap_kind=EvidenceGapKind.MAP_IMPACT,
        ),
        tool_name="get_dbt_lineage",
        arguments={"direction": "downstream", "node_id": FAILED_NODE},
        record=_downstream_lineage_record(),
    )
    return kernel, kernel.evidence_records


def _rename_decision(records: tuple[EvidenceRecord, ...]) -> KernelDecision:
    by_kind = {record.evidence_type.value: record for record in records}
    node_error = by_kind["DBT_NODE_ERROR"]
    schema = by_kind["RELATION_SCHEMA"]
    lineage = by_kind["DBT_LINEAGE"]
    return KernelDecision(
        status="CONFIRMED",
        incident_case_id=CASE_ID,
        run_id=RUN_ID,
        selected_hypothesis_id="h_rename",
        assessments=(
            HypothesisAssessment(
                hypothesis_id="h_rename",
                verdict=HypothesisVerdict.SUPPORTED,
                evidence_ids=(node_error.evidence_id, schema.evidence_id),
            ),
            HypothesisAssessment(
                hypothesis_id="h_type",
                verdict=HypothesisVerdict.REFUTED,
                evidence_ids=(schema.evidence_id,),
            ),
        ),
        claims=(
            ClaimEvidence(
                kind=ClaimKind.ROOT_CAUSE,
                value="SOURCE_SCHEMA_COLUMN_RENAMED",
                evidence_ids=(node_error.evidence_id, schema.evidence_id),
            ),
            ClaimEvidence(
                kind=ClaimKind.AFFECTED_ASSET,
                value=FAILED_NODE,
                evidence_ids=(node_error.evidence_id,),
            ),
            ClaimEvidence(
                kind=ClaimKind.AFFECTED_ASSET,
                value="orders",
                evidence_ids=(lineage.evidence_id,),
            ),
            ClaimEvidence(
                kind=ClaimKind.AFFECTED_ASSET,
                value="customers",
                evidence_ids=(lineage.evidence_id,),
            ),
        ),
        summary="Evidence supports a renamed source column.",
        recommended_actions=("Restore the source contract.",),
        confidence=0.9,
    )


def _valid_kernel_and_decision() -> tuple[DiagnosticKernel, KernelDecision]:
    kernel, records = _complete_investigation(fault_column_name="total_amount")
    return kernel, _rename_decision(records)


def _mutate_decision(
    kernel: DiagnosticKernel, decision: KernelDecision, mutation: str
) -> KernelDecision:
    if mutation == "one_hypothesis":
        kernel._hypotheses.pop()
        return decision
    if mutation == "no_refuted_hypothesis":
        assessments = tuple(
            item.model_copy(update={"verdict": HypothesisVerdict.SUPPORTED})
            for item in decision.assessments
        )
        return decision.model_copy(update={"assessments": assessments})
    if mutation == "selected_is_refuted":
        return decision.model_copy(update={"selected_hypothesis_id": "h_type"})
    if mutation == "unknown_assessment_evidence":
        assessment = decision.assessments[0].model_copy(
            update={"evidence_ids": ("ev_" + "f" * 64,)}
        )
        return decision.model_copy(update={"assessments": (assessment,) + decision.assessments[1:]})
    if mutation == "open_gap":
        kernel.prepare_tool(
            intent=InvestigationIntent(
                gap_id="g_open",
                gap_kind=EvidenceGapKind.DISCOVER_SOURCE_RELATION,
            ),
            tool_name="get_dbt_lineage",
            arguments={
                "direction": "upstream",
                "node_id": "seed.jaffle_shop.raw_payments",
            },
        )
        return decision
    if mutation == "root_claim_missing_schema":
        root_claim = decision.claims[0].model_copy(
            update={"evidence_ids": (decision.claims[0].evidence_ids[0],)}
        )
        return decision.model_copy(update={"claims": (root_claim,) + decision.claims[1:]})
    if mutation == "root_claim_wrong_code":
        root_claim = decision.claims[0].model_copy(
            update={"value": "SOURCE_SCHEMA_COLUMN_TYPE_CHANGED"}
        )
        return decision.model_copy(update={"claims": (root_claim,) + decision.claims[1:]})
    if mutation == "asset_without_lineage":
        asset = decision.claims[2].model_copy(
            update={"evidence_ids": (decision.claims[1].evidence_ids[0],)}
        )
        return decision.model_copy(
            update={"claims": decision.claims[:2] + (asset,) + decision.claims[3:]}
        )
    if mutation == "invented_asset":
        lineage_id = decision.claims[2].evidence_ids[0]
        invented = ClaimEvidence(
            kind=ClaimKind.AFFECTED_ASSET,
            value="payments",
            evidence_ids=(lineage_id,),
        )
        return decision.model_copy(update={"claims": decision.claims + (invented,)})
    if mutation == "duplicate_claim":
        return decision.model_copy(update={"claims": decision.claims + (decision.claims[0],)})
    if mutation == "cross_run_decision":
        return decision.model_copy(update={"run_id": "b" * 32})
    raise AssertionError(f"unknown mutation: {mutation}")


def test_gap_transition_records_hypotheses_and_current_run_evidence() -> None:
    kernel = _kernel()
    run_results = _run_results_record()
    prepared = kernel.prepare_tool(
        intent=InvestigationIntent(
            gap_id="g_failure",
            gap_kind=EvidenceGapKind.LOCATE_FAILURE,
            hypothesis_ids=(),
            new_hypotheses=(),
        ),
        tool_name="get_dbt_run_results",
        arguments={"run_id": RUN_ID},
    )
    accepted = kernel.record_tool_result(prepared, (run_results,))
    state = kernel.snapshot(model_requests_used=1)
    assert accepted == (run_results,)
    assert state.gaps[0].status == EvidenceGapStatus.CLOSED
    assert state.gaps[0].evidence_ids == (run_results.evidence_id,)
    assert state.evidence_inventory == (run_results.evidence_id,)
    assert state.tool_calls_used == 1
    assert state.tool_calls_remaining == 7


def test_schema_gap_can_register_two_competing_hypotheses() -> None:
    kernel = _kernel_with_failure_error_and_upstream_lineage()
    intent = InvestigationIntent(
        gap_id="g_schema",
        gap_kind=EvidenceGapKind.DISCRIMINATE_SCHEMA,
        hypothesis_ids=("h_rename", "h_type"),
        new_hypotheses=(
            Hypothesis(
                hypothesis_id="h_rename",
                root_cause_code="SOURCE_SCHEMA_COLUMN_RENAMED",
            ),
            Hypothesis(
                hypothesis_id="h_type",
                root_cause_code="SOURCE_SCHEMA_COLUMN_TYPE_CHANGED",
            ),
        ),
    )
    prepared = kernel.prepare_tool(
        intent=intent,
        tool_name="get_relation_schema",
        arguments={"relation_name": "raw_payments"},
    )
    kernel.record_tool_result(prepared, (_schema_record("text"),))
    state = kernel.snapshot(model_requests_used=4)
    assert tuple(item.hypothesis_id for item in state.hypotheses) == (
        "h_rename",
        "h_type",
    )
    assert state.gaps[-1].hypothesis_ids == ("h_rename", "h_type")


@pytest.mark.parametrize(
    ("tool_name", "gap_kind"),
    [
        ("get_dbt_node_error", EvidenceGapKind.LOCATE_FAILURE),
        ("get_relation_schema", EvidenceGapKind.MAP_IMPACT),
        ("get_dbt_lineage", EvidenceGapKind.DISCRIMINATE_SCHEMA),
    ],
)
def test_gap_kind_must_match_tool(tool_name: str, gap_kind: EvidenceGapKind) -> None:
    kernel = _kernel()
    with pytest.raises(KernelError, match="GAP_TOOL_MISMATCH"):
        kernel.prepare_tool(
            intent=InvestigationIntent(
                gap_id="g_wrong",
                gap_kind=gap_kind,
                hypothesis_ids=(),
                new_hypotheses=(),
            ),
            tool_name=tool_name,
            arguments={"run_id": RUN_ID},
        )


def test_node_error_argument_requires_prior_failed_node() -> None:
    kernel = _kernel()
    with pytest.raises(KernelError, match="NODE_ARGUMENT_NOT_PROVEN"):
        kernel.prepare_tool(
            intent=InvestigationIntent(
                gap_id="g_explain",
                gap_kind=EvidenceGapKind.EXPLAIN_FAILURE,
            ),
            tool_name="get_dbt_node_error",
            arguments={"node_id": "model.jaffle_shop.orders", "run_id": RUN_ID},
        )


def test_lineage_argument_requires_prior_run_or_lineage_node() -> None:
    kernel = _kernel()
    with pytest.raises(KernelError, match="NODE_ARGUMENT_NOT_PROVEN"):
        kernel.prepare_tool(
            intent=InvestigationIntent(
                gap_id="g_source",
                gap_kind=EvidenceGapKind.DISCOVER_SOURCE_RELATION,
            ),
            tool_name="get_dbt_lineage",
            arguments={"direction": "upstream", "node_id": "model.jaffle_shop.orders"},
        )


def test_relation_argument_requires_prior_upstream_source() -> None:
    kernel = _kernel_with_failure_error_and_upstream_lineage()
    with pytest.raises(KernelError, match="RELATION_ARGUMENT_NOT_PROVEN"):
        kernel.prepare_tool(
            intent=InvestigationIntent(
                gap_id="g_schema",
                gap_kind=EvidenceGapKind.DISCRIMINATE_SCHEMA,
            ),
            tool_name="get_relation_schema",
            arguments={"relation_name": "orders"},
        )


def test_duplicate_attempt_is_audited_and_consumes_budget() -> None:
    kernel = _kernel()
    intent = InvestigationIntent(
        gap_id="g_first",
        gap_kind=EvidenceGapKind.LOCATE_FAILURE,
    )
    first = kernel.prepare_tool(
        intent=intent,
        tool_name="get_dbt_run_results",
        arguments={"run_id": RUN_ID},
    )
    kernel.record_tool_result(first, (_run_results_record(),))

    with pytest.raises(KernelError, match="DUPLICATE_TOOL_CALL") as captured:
        kernel.prepare_tool(
            intent=intent.model_copy(update={"gap_id": "g_duplicate"}),
            tool_name="get_dbt_run_results",
            arguments={"run_id": RUN_ID},
        )

    state = kernel.snapshot(model_requests_used=2)
    assert captured.value.fingerprint == first.fingerprint
    assert state.tool_fingerprints == (first.fingerprint, first.fingerprint)
    assert state.tool_calls_used == 2
    assert state.tool_calls_remaining == 6


def test_ninth_tool_attempt_is_rejected_before_state_change() -> None:
    kernel = _kernel()
    first = kernel.prepare_tool(
        intent=InvestigationIntent(
            gap_id="g_first",
            gap_kind=EvidenceGapKind.LOCATE_FAILURE,
        ),
        tool_name="get_dbt_run_results",
        arguments={"run_id": RUN_ID},
    )
    kernel.record_tool_result(first, (_run_results_record(),))
    for index in range(2, 9):
        with pytest.raises(KernelError, match="DUPLICATE_TOOL_CALL"):
            kernel.prepare_tool(
                intent=InvestigationIntent(
                    gap_id=f"g_attempt_{index}",
                    gap_kind=EvidenceGapKind.LOCATE_FAILURE,
                ),
                tool_name="get_dbt_run_results",
                arguments={"run_id": RUN_ID},
            )
    before = kernel.snapshot(model_requests_used=2)
    with pytest.raises(KernelError, match="TOOL_CALL_LIMIT"):
        kernel.prepare_tool(
            intent=InvestigationIntent(
                gap_id="g_ninth",
                gap_kind=EvidenceGapKind.LOCATE_FAILURE,
            ),
            tool_name="get_dbt_run_results",
            arguments={"run_id": RUN_ID},
        )
    after = kernel.snapshot(model_requests_used=2)
    assert after == before


def test_hypothesis_registration_requires_prior_node_error() -> None:
    kernel = _kernel()
    with pytest.raises(KernelError, match="HYPOTHESIS_REQUIRES_NODE_ERROR"):
        kernel.prepare_tool(
            intent=InvestigationIntent(
                gap_id="g_schema",
                gap_kind=EvidenceGapKind.DISCRIMINATE_SCHEMA,
                hypothesis_ids=("h_type",),
                new_hypotheses=(
                    Hypothesis(
                        hypothesis_id="h_type",
                        root_cause_code="SOURCE_SCHEMA_COLUMN_TYPE_CHANGED",
                    ),
                ),
            ),
            tool_name="get_relation_schema",
            arguments={"relation_name": "raw_payments"},
        )


def test_unknown_ontology_code_is_rejected_without_partial_gap() -> None:
    kernel = _kernel_with_failure_error_and_upstream_lineage()
    with pytest.raises(KernelError, match="ONTOLOGY_CODE_UNKNOWN"):
        kernel.prepare_tool(
            intent=InvestigationIntent(
                gap_id="g_schema",
                gap_kind=EvidenceGapKind.DISCRIMINATE_SCHEMA,
                hypothesis_ids=("h_bad",),
                new_hypotheses=(
                    Hypothesis(
                        hypothesis_id="h_bad",
                        root_cause_code="UNAPPROVED_CAUSE",
                    ),
                ),
            ),
            tool_name="get_relation_schema",
            arguments={"relation_name": "raw_payments"},
        )
    state = kernel.snapshot(model_requests_used=4)
    assert state.hypotheses == ()
    assert len(state.gaps) == 3


def test_cross_run_evidence_is_rejected() -> None:
    kernel = _kernel()
    prepared = kernel.prepare_tool(
        intent=InvestigationIntent(
            gap_id="g_failure",
            gap_kind=EvidenceGapKind.LOCATE_FAILURE,
        ),
        tool_name="get_dbt_run_results",
        arguments={"run_id": RUN_ID},
    )
    other_run = "b" * 32
    with pytest.raises(KernelError, match="RUN_CONTEXT_MISMATCH"):
        kernel.record_tool_result(prepared, (_run_results_record(run_id=other_run),))


def test_conflicting_known_evidence_is_rejected() -> None:
    kernel = _kernel()
    first = _run_results_record()
    prepared = kernel.prepare_tool(
        intent=InvestigationIntent(
            gap_id="g_failure",
            gap_kind=EvidenceGapKind.LOCATE_FAILURE,
        ),
        tool_name="get_dbt_run_results",
        arguments={"run_id": RUN_ID},
    )
    kernel.record_tool_result(prepared, (first,))
    second = first.model_copy(update={"observed_at": datetime(2026, 8, 25, 9, 4, tzinfo=UTC)})
    prepared_second = kernel.prepare_tool(
        intent=InvestigationIntent(
            gap_id="g_second",
            gap_kind=EvidenceGapKind.EXPLAIN_FAILURE,
        ),
        tool_name="get_dbt_node_error",
        arguments={"node_id": FAILED_NODE, "run_id": RUN_ID},
    )
    with pytest.raises(KernelError, match="EVIDENCE_ID_CONFLICT"):
        kernel.record_tool_result(prepared_second, (second,))


def test_record_tool_failure_blocks_gap_with_safe_code() -> None:
    kernel = _kernel()
    prepared = kernel.prepare_tool(
        intent=InvestigationIntent(
            gap_id="g_failure",
            gap_kind=EvidenceGapKind.LOCATE_FAILURE,
        ),
        tool_name="get_dbt_run_results",
        arguments={"run_id": RUN_ID},
    )
    kernel.record_tool_failure(prepared, "raw database exception")
    state = kernel.snapshot(model_requests_used=1)
    assert state.gaps[0].status == EvidenceGapStatus.BLOCKED
    assert state.gaps[0].error_code == "EVIDENCE_TOOL_ERROR"
    assert state.gaps[0].evidence_ids == ()
    assert state.evidence_inventory == ()


def test_confirmed_rename_requires_supported_selected_and_refuted_alternative() -> None:
    kernel, records = _complete_investigation(fault_column_name="total_amount")
    decision = _rename_decision(records)

    outcome = kernel.finalize(decision)

    assert outcome.status is KernelFinalStatus.CONFIRMED
    assert outcome.root_cause_code == "SOURCE_SCHEMA_COLUMN_RENAMED"
    assert outcome.affected_assets == (FAILED_NODE, "orders", "customers")
    assert set(outcome.evidence_ids) == {
        evidence_id
        for claim in decision.claims
        for evidence_id in claim.evidence_ids
    }
    state = kernel.snapshot(model_requests_used=5)
    assert state.final_status is KernelFinalStatus.CONFIRMED
    assert state.gate_reason == "CONFIRMED"
    assert state.assessments == decision.assessments
    assert state.claims == decision.claims


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        ("one_hypothesis", "ALTERNATIVE_HYPOTHESIS_REQUIRED"),
        ("no_refuted_hypothesis", "REFUTED_HYPOTHESIS_REQUIRED"),
        ("selected_is_refuted", "SELECTED_HYPOTHESIS_NOT_SUPPORTED"),
        ("unknown_assessment_evidence", "ASSESSMENT_EVIDENCE_UNKNOWN"),
        ("open_gap", "EVIDENCE_GAP_OPEN"),
        ("root_claim_missing_schema", "ROOT_CLAIM_EVIDENCE_INCOMPATIBLE"),
        ("root_claim_wrong_code", "ROOT_CLAIM_MISMATCH"),
        ("asset_without_lineage", "ASSET_CLAIM_EVIDENCE_INCOMPATIBLE"),
        ("invented_asset", "ASSET_CLAIM_EVIDENCE_INCOMPATIBLE"),
        ("duplicate_claim", "DUPLICATE_CLAIM"),
        ("cross_run_decision", "DECISION_SCOPE_MISMATCH"),
    ],
)
def test_confirmed_finalization_fails_closed(mutation: str, code: str) -> None:
    kernel, decision = _valid_kernel_and_decision()
    mutated = _mutate_decision(kernel, decision, mutation)

    with pytest.raises(KernelError, match=code):
        kernel.finalize(mutated)

    assert kernel.snapshot(model_requests_used=5).final_status is None


def test_insufficient_evidence_preserves_open_gap_without_claims() -> None:
    kernel = _kernel_with_run_results_only()
    decision = KernelDecision(
        status="INSUFFICIENT_EVIDENCE",
        incident_case_id=CASE_ID,
        run_id=RUN_ID,
        selected_hypothesis_id=None,
        assessments=(),
        claims=(),
        summary="The source schema gap is still open.",
        recommended_actions=("Collect source schema evidence.",),
        confidence=0.2,
    )

    outcome = kernel.finalize(decision)
    state = kernel.snapshot(model_requests_used=2)
    assert outcome.status is KernelFinalStatus.INSUFFICIENT_EVIDENCE
    assert outcome.root_cause_code is None
    assert outcome.affected_assets == ()
    assert outcome.evidence_ids == ()
    assert state.final_status is KernelFinalStatus.INSUFFICIENT_EVIDENCE
    assert state.gate_reason == "INSUFFICIENT_EVIDENCE"
    assert any(gap.status is EvidenceGapStatus.OPEN for gap in state.gaps)
    assert state.hypotheses == ()
    assert state.claims == ()


def test_model_error_terminates_with_one_fixed_safe_reason() -> None:
    kernel = _kernel_with_run_results_only()

    outcome = kernel.terminate_model_error("MODEL_TIMEOUT")
    state = kernel.snapshot(model_requests_used=2)
    assert outcome.status is KernelFinalStatus.MODEL_ERROR
    assert outcome.summary == "MODEL_TIMEOUT"
    assert outcome.root_cause_code is None
    assert outcome.affected_assets == ()
    assert outcome.evidence_ids == ()
    assert state.final_status is KernelFinalStatus.MODEL_ERROR
    assert state.gate_reason == "MODEL_TIMEOUT"

    with pytest.raises(KernelError, match="MODEL_ERROR_REASON_INVALID"):
        _kernel().terminate_model_error("raw exception text")
