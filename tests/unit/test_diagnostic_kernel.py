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
    RelationDataProfileFact,
    RelationHistoryFact,
    RelationSchemaColumn,
    RelationSchemaFact,
)
from data_incident_gym.profiles import (
    DuplicateProfileFact,
    GroupProfileFact,
    HistoryPoint,
    HistorySeries,
    RelationHistorySnapshot,
    RelationProfileSnapshot,
    RelationshipViolationFact,
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


def _duplicate_kernel() -> DiagnosticKernel:
    return DiagnosticKernel.start(
        run_id=RUN_ID,
        allowed_root_cause_codes=(
            "SOURCE_EXACT_PAYMENT_DUPLICATE",
            "SOURCE_SEMANTIC_PAYMENT_DUPLICATE",
            "LEGITIMATE_SPLIT_PAYMENT",
        ),
        model_request_limit=8,
        tool_call_limit=8,
        observable_schema_relations=("raw_payments",),
        observable_profile_relations=("raw_payments",),
        incident_subjects=("seed.jaffle_shop.raw_payments", "raw_payments"),
    )


def _duplicate_records(
    *,
    id_duplicates: int = 0,
    fingerprint_duplicates: int = 3,
    run_status: str = "SUCCEEDED",
) -> tuple[EvidenceRecord, EvidenceRecord, EvidenceRecord, EvidenceRecord]:
    run = _record(
        EvidenceType.DBT_RUN_RESULTS,
        EvidenceSource.DBT_RUN_RESULTS,
        RUN_ID,
        DbtRunResultsFact(
            kind="DBT_RUN_RESULTS",
            run_id=RUN_ID,
            run_status=run_status,
            dbt_exit_code=0 if run_status == "SUCCEEDED" else 1,
            failed_nodes=(),
            skipped_nodes=(),
        ),
    )
    lineage = _record(
        EvidenceType.DBT_LINEAGE,
        EvidenceSource.DBT_MANIFEST,
        "seed.jaffle_shop.raw_payments",
        DbtLineageFact(
            kind="DBT_LINEAGE",
            run_id=RUN_ID,
            node_id="seed.jaffle_shop.raw_payments",
            direction="downstream",
            related_nodes=(
                DbtLineageNode(
                    node_id="model.jaffle_shop.stg_payments",
                    resource_type="model",
                    name="stg_payments",
                    distance=1,
                ),
                DbtLineageNode(
                    node_id="model.jaffle_shop.customers",
                    resource_type="model",
                    name="customers",
                    distance=2,
                ),
                DbtLineageNode(
                    node_id="model.jaffle_shop.orders",
                    resource_type="model",
                    name="orders",
                    distance=2,
                ),
            ),
        ),
    )
    schema = _record(
        EvidenceType.RELATION_SCHEMA,
        EvidenceSource.POSTGRES_CATALOG,
        "raw_payments",
        RelationSchemaFact(
            kind="RELATION_SCHEMA",
            run_id=RUN_ID,
            schema_name="analytics",
            relation_name="raw_payments",
            columns=(
                RelationSchemaColumn(
                    name="id",
                    data_type="integer",
                    nullable=True,
                    ordinal_position=1,
                ),
            ),
        ),
    )
    profile = _record(
        EvidenceType.RELATION_DATA_PROFILE,
        EvidenceSource.POSTGRES_PROFILE_SNAPSHOT,
        "raw_payments",
        RelationDataProfileFact(
            kind="RELATION_DATA_PROFILE",
            run_id=RUN_ID,
            relation_name="raw_payments",
            profile_spec_version="profile_spec.v1",
            profile_spec_sha256="b" * 64,
            snapshot=RelationProfileSnapshot(
                relation_name="raw_payments",
                row_count=116,
                columns=(),
                business_key_duplicates=(
                    DuplicateProfileFact(name="id", duplicate_count=id_duplicates),
                ),
                business_fingerprint_duplicates=(
                    DuplicateProfileFact(
                        name="order_payment_amount",
                        duplicate_count=fingerprint_duplicates,
                    ),
                ),
                groups=(
                    GroupProfileFact(
                        name="payment_method",
                        columns=("payment_method",),
                        values=(("coupon",),),
                        counts=(16,),
                    ),
                ),
            ),
        ),
    )
    return run, lineage, schema, profile


def _close_duplicate_records(
    kernel: DiagnosticKernel,
    records: tuple[EvidenceRecord, EvidenceRecord, EvidenceRecord, EvidenceRecord],
    *,
    include_run: bool = True,
) -> tuple[EvidenceRecord, EvidenceRecord, EvidenceRecord, EvidenceRecord]:
    run, lineage, schema, profile = records
    if include_run:
        _close(
            kernel,
            gap_id="g_run_duplicate",
            gap_kind=EvidenceGapKind.LOCATE_FAILURE,
            tool_name="get_dbt_run_results",
            arguments={"run_id": RUN_ID},
            record=run,
            new_hypotheses=(
                Hypothesis(
                    hypothesis_id="h_semantic_duplicate",
                    root_cause_code="SOURCE_SEMANTIC_PAYMENT_DUPLICATE",
                ),
                Hypothesis(
                    hypothesis_id="h_legitimate_split",
                    root_cause_code="LEGITIMATE_SPLIT_PAYMENT",
                ),
            ),
        )
    else:
        _close(
            kernel,
            gap_id="g_profile_duplicate",
            gap_kind=EvidenceGapKind.PROFILE_RELATION,
            tool_name="get_relation_data_profile",
            arguments={"relation_name": "raw_payments"},
            record=profile,
            new_hypotheses=(
                Hypothesis(
                    hypothesis_id="h_semantic_duplicate",
                    root_cause_code="SOURCE_SEMANTIC_PAYMENT_DUPLICATE",
                ),
                Hypothesis(
                    hypothesis_id="h_legitimate_split",
                    root_cause_code="LEGITIMATE_SPLIT_PAYMENT",
                ),
            ),
        )
        return run, lineage, schema, profile
    _close(
        kernel,
        gap_id="g_lineage_duplicate",
        gap_kind=EvidenceGapKind.MAP_IMPACT,
        tool_name="get_dbt_lineage",
        arguments={"node_id": "seed.jaffle_shop.raw_payments", "direction": "downstream"},
        record=lineage,
    )
    _close(
        kernel,
        gap_id="g_schema_duplicate",
        gap_kind=EvidenceGapKind.DISCRIMINATE_SCHEMA,
        tool_name="get_relation_schema",
        arguments={"relation_name": "raw_payments"},
        record=schema,
    )
    _close(
        kernel,
        gap_id="g_profile_duplicate",
        gap_kind=EvidenceGapKind.PROFILE_RELATION,
        tool_name="get_relation_data_profile",
        arguments={"relation_name": "raw_payments"},
        record=profile,
    )
    return records


def _semantic_duplicate_decision(
    records: tuple[EvidenceRecord, EvidenceRecord, EvidenceRecord, EvidenceRecord],
    *,
    root_code: str = "SOURCE_SEMANTIC_PAYMENT_DUPLICATE",
) -> KernelDecision:
    run, lineage, _, profile = records
    return KernelDecision(
        status="CONFIRMED",
        run_id=RUN_ID,
        selected_hypothesis_id=(
            "h_semantic_duplicate"
            if root_code == "SOURCE_SEMANTIC_PAYMENT_DUPLICATE"
            else "h_legitimate_split"
        ),
        assessments=(
            HypothesisAssessment(
                hypothesis_id="h_semantic_duplicate",
                verdict=(
                    HypothesisVerdict.SUPPORTED
                    if root_code == "SOURCE_SEMANTIC_PAYMENT_DUPLICATE"
                    else HypothesisVerdict.REFUTED
                ),
                evidence_ids=(run.evidence_id, profile.evidence_id),
            ),
            HypothesisAssessment(
                hypothesis_id="h_legitimate_split",
                verdict=(
                    HypothesisVerdict.REFUTED
                    if root_code == "SOURCE_SEMANTIC_PAYMENT_DUPLICATE"
                    else HypothesisVerdict.SUPPORTED
                ),
                evidence_ids=(profile.evidence_id,),
            ),
        ),
        claims=(
            ClaimEvidence(
                kind=ClaimKind.ROOT_CAUSE,
                value=root_code,
                evidence_ids=(run.evidence_id, profile.evidence_id),
            ),
            ClaimEvidence(
                kind=ClaimKind.AFFECTED_ASSET,
                value="model.jaffle_shop.stg_payments",
                evidence_ids=(lineage.evidence_id,),
            ),
            ClaimEvidence(
                kind=ClaimKind.AFFECTED_ASSET,
                value="model.jaffle_shop.customers",
                evidence_ids=(lineage.evidence_id,),
            ),
            ClaimEvidence(
                kind=ClaimKind.AFFECTED_ASSET,
                value="model.jaffle_shop.orders",
                evidence_ids=(lineage.evidence_id,),
            ),
        ),
        summary="The payment aggregate contains repeated business fingerprints.",
        recommended_actions=(),
        confidence=0.9,
    )


def test_kernel_confirms_successful_semantic_duplicate_from_profile_and_lineage() -> None:
    kernel = _duplicate_kernel()
    records = _close_duplicate_records(kernel, _duplicate_records())

    outcome = kernel.finalize(_semantic_duplicate_decision(records))

    assert outcome.status is KernelFinalStatus.CONFIRMED
    assert outcome.root_cause_code == "SOURCE_SEMANTIC_PAYMENT_DUPLICATE"
    assert outcome.affected_assets == (
        "model.jaffle_shop.stg_payments",
        "model.jaffle_shop.customers",
        "model.jaffle_shop.orders",
    )


@pytest.mark.parametrize(
    ("id_duplicates", "fingerprint_duplicates", "run_status"),
    (
        (1, 3, "SUCCEEDED"),
        (0, 0, "SUCCEEDED"),
        (0, 3, "FAILED"),
    ),
)
def test_kernel_rejects_unsupported_successful_duplicate_claims(
    id_duplicates: int,
    fingerprint_duplicates: int,
    run_status: str,
) -> None:
    kernel = _duplicate_kernel()
    records = _close_duplicate_records(
        kernel,
        _duplicate_records(
            id_duplicates=id_duplicates,
            fingerprint_duplicates=fingerprint_duplicates,
            run_status=run_status,
        ),
    )

    with pytest.raises(KernelError, match="ROOT_CLAIM_EVIDENCE_INCOMPATIBLE"):
        kernel.finalize(_semantic_duplicate_decision(records))


def _orphan_kernel() -> DiagnosticKernel:
    return DiagnosticKernel.start(
        run_id=RUN_ID,
        allowed_root_cause_codes=(
            "SOURCE_PERMANENT_ORPHAN_PAYMENT",
            "NORMAL_LATE_ARRIVING_ORDER",
        ),
        model_request_limit=8,
        tool_call_limit=8,
        observable_schema_relations=("raw_payments",),
        observable_profile_relations=("raw_payments",),
        observable_history_relations=("raw_orders",),
        incident_subjects=("seed.jaffle_shop.raw_payments", "raw_payments", "raw_orders"),
    )


def _orphan_records(
    *,
    relationship_count: int = 1,
    watermark: str | None = "2018-04-09",
    points: tuple[HistoryPoint, ...] = (
        HistoryPoint(bucket="2018-04-03", periodic_key="2018-04-03", value=12),
    ),
) -> tuple[EvidenceRecord, EvidenceRecord, EvidenceRecord, EvidenceRecord]:
    run = _record(
        EvidenceType.DBT_RUN_RESULTS,
        EvidenceSource.DBT_RUN_RESULTS,
        RUN_ID,
        DbtRunResultsFact(
            kind="DBT_RUN_RESULTS",
            run_id=RUN_ID,
            run_status="SUCCEEDED",
            dbt_exit_code=0,
            failed_nodes=(),
            skipped_nodes=(),
        ),
    )
    lineage = _record(
        EvidenceType.DBT_LINEAGE,
        EvidenceSource.DBT_MANIFEST,
        "seed.jaffle_shop.raw_payments",
        DbtLineageFact(
            kind="DBT_LINEAGE",
            run_id=RUN_ID,
            node_id="seed.jaffle_shop.raw_payments",
            direction="downstream",
            related_nodes=(
                DbtLineageNode(
                    node_id="model.jaffle_shop.stg_payments",
                    resource_type="model",
                    name="stg_payments",
                    distance=1,
                ),
                DbtLineageNode(
                    node_id="model.jaffle_shop.customers",
                    resource_type="model",
                    name="customers",
                    distance=2,
                ),
                DbtLineageNode(
                    node_id="model.jaffle_shop.orders",
                    resource_type="model",
                    name="orders",
                    distance=2,
                ),
            ),
        ),
    )
    profile = _record(
        EvidenceType.RELATION_DATA_PROFILE,
        EvidenceSource.POSTGRES_PROFILE_SNAPSHOT,
        "raw_payments",
        RelationDataProfileFact(
            kind="RELATION_DATA_PROFILE",
            run_id=RUN_ID,
            relation_name="raw_payments",
            profile_spec_version="profile_spec.v1",
            profile_spec_sha256="b" * 64,
            snapshot=RelationProfileSnapshot(
                relation_name="raw_payments",
                row_count=114,
                columns=(),
                relationship_violations=(
                    RelationshipViolationFact(
                        name="order_id_to_raw_orders_id",
                        violation_count=relationship_count,
                    ),
                ),
            ),
        ),
    )
    history = _record(
        EvidenceType.RELATION_HISTORY,
        EvidenceSource.POSTGRES_PROFILE_SNAPSHOT,
        "raw_orders",
        RelationHistoryFact(
            kind="RELATION_HISTORY",
            run_id=RUN_ID,
            relation_name="raw_orders",
            profile_spec_version="profile_spec.v1",
            profile_spec_sha256="b" * 64,
            snapshot=RelationHistorySnapshot(
                relation_name="raw_orders",
                histories=(
                    HistorySeries(
                        name="order_count_by_day",
                        metric="count",
                        points=points,
                        watermark_column="order_date",
                        watermark_value=watermark,
                    ),
                ),
            ),
        ),
    )
    return run, lineage, profile, history


def _close_orphan_records(
    kernel: DiagnosticKernel,
    records: tuple[EvidenceRecord, EvidenceRecord, EvidenceRecord, EvidenceRecord],
) -> None:
    run, lineage, profile, history = records
    _close(
        kernel,
        gap_id="g_run_orphan",
        gap_kind=EvidenceGapKind.LOCATE_FAILURE,
        tool_name="get_dbt_run_results",
        arguments={"run_id": RUN_ID},
        record=run,
        new_hypotheses=(
            Hypothesis(
                hypothesis_id="h_permanent_orphan",
                root_cause_code="SOURCE_PERMANENT_ORPHAN_PAYMENT",
            ),
            Hypothesis(
                hypothesis_id="h_late_order",
                root_cause_code="NORMAL_LATE_ARRIVING_ORDER",
            ),
        ),
    )
    _close(
        kernel,
        gap_id="g_lineage_orphan",
        gap_kind=EvidenceGapKind.MAP_IMPACT,
        tool_name="get_dbt_lineage",
        arguments={"node_id": "seed.jaffle_shop.raw_payments", "direction": "downstream"},
        record=lineage,
    )
    _close(
        kernel,
        gap_id="g_profile_orphan",
        gap_kind=EvidenceGapKind.PROFILE_RELATION,
        tool_name="get_relation_data_profile",
        arguments={"relation_name": "raw_payments"},
        record=profile,
    )
    _close(
        kernel,
        gap_id="g_history_orphan",
        gap_kind=EvidenceGapKind.COMPARE_HISTORY,
        tool_name="get_relation_history",
        arguments={"relation_name": "raw_orders"},
        record=history,
    )


def _orphan_decision(
    records: tuple[EvidenceRecord, EvidenceRecord, EvidenceRecord, EvidenceRecord],
) -> KernelDecision:
    run, lineage, profile, history = records
    return KernelDecision(
        status="CONFIRMED",
        run_id=RUN_ID,
        selected_hypothesis_id="h_permanent_orphan",
        assessments=(
            HypothesisAssessment(
                hypothesis_id="h_permanent_orphan",
                verdict=HypothesisVerdict.SUPPORTED,
                evidence_ids=(run.evidence_id, profile.evidence_id, history.evidence_id),
            ),
            HypothesisAssessment(
                hypothesis_id="h_late_order",
                verdict=HypothesisVerdict.REFUTED,
                evidence_ids=(profile.evidence_id, history.evidence_id),
            ),
        ),
        claims=(
            ClaimEvidence(
                kind=ClaimKind.ROOT_CAUSE,
                value="SOURCE_PERMANENT_ORPHAN_PAYMENT",
                evidence_ids=(run.evidence_id, profile.evidence_id, history.evidence_id),
            ),
            ClaimEvidence(
                kind=ClaimKind.AFFECTED_ASSET,
                value="model.jaffle_shop.stg_payments",
                evidence_ids=(lineage.evidence_id,),
            ),
            ClaimEvidence(
                kind=ClaimKind.AFFECTED_ASSET,
                value="model.jaffle_shop.customers",
                evidence_ids=(lineage.evidence_id,),
            ),
            ClaimEvidence(
                kind=ClaimKind.AFFECTED_ASSET,
                value="model.jaffle_shop.orders",
                evidence_ids=(lineage.evidence_id,),
            ),
        ),
        summary="A settled payment references an order absent beyond the ingestion boundary.",
        recommended_actions=(),
        confidence=0.9,
    )


def test_kernel_confirms_permanent_orphan_only_with_history_boundary() -> None:
    kernel = _orphan_kernel()
    records = _orphan_records()
    _close_orphan_records(kernel, records)

    outcome = kernel.finalize(_orphan_decision(records))

    assert outcome.status is KernelFinalStatus.CONFIRMED
    assert outcome.root_cause_code == "SOURCE_PERMANENT_ORPHAN_PAYMENT"


@pytest.mark.parametrize(
    ("relationship_count", "watermark", "points"),
    (
        (
            0,
            "2018-04-09",
            (HistoryPoint(bucket="2018-04-03", periodic_key="2018-04-03", value=12),),
        ),
        (
            1,
            None,
            (HistoryPoint(bucket="2018-04-03", periodic_key="2018-04-03", value=12),),
        ),
        (
            1,
            "not-a-date",
            (HistoryPoint(bucket="2018-04-03", periodic_key="2018-04-03", value=12),),
        ),
        (1, "2018-04-09", ()),
    ),
)
def test_kernel_rejects_permanent_orphan_without_compatible_relationship_history(
    relationship_count: int,
    watermark: str | None,
    points: tuple[HistoryPoint, ...],
) -> None:
    kernel = _orphan_kernel()
    records = _orphan_records(
        relationship_count=relationship_count,
        watermark=watermark,
        points=points,
    )
    _close_orphan_records(kernel, records)

    with pytest.raises(KernelError, match="ROOT_CLAIM_EVIDENCE_INCOMPATIBLE"):
        kernel.finalize(_orphan_decision(records))


def test_kernel_binds_blocked_history_and_watermark_unresolved_evidence() -> None:
    kernel = DiagnosticKernel.start(
        run_id=RUN_ID,
        allowed_root_cause_codes=(
            "SOURCE_PERMANENT_ORPHAN_PAYMENT",
            "NORMAL_LATE_ARRIVING_ORDER",
        ),
        model_request_limit=8,
        tool_call_limit=8,
        observable_history_relations=("raw_orders",),
        incident_subjects=("raw_orders",),
    )
    prepared = kernel.prepare_tool(
        intent=InvestigationIntent(
            gap_id="g_blocked_history",
            gap_kind=EvidenceGapKind.COMPARE_HISTORY,
            new_hypotheses=(
                Hypothesis(
                    hypothesis_id="h_permanent_orphan",
                    root_cause_code="SOURCE_PERMANENT_ORPHAN_PAYMENT",
                ),
                Hypothesis(
                    hypothesis_id="h_late_order",
                    root_cause_code="NORMAL_LATE_ARRIVING_ORDER",
                ),
            ),
        ),
        tool_name="get_relation_history",
        arguments={"relation_name": "raw_orders"},
    )
    kernel.record_tool_failure(prepared, "RELATION_NOT_ALLOWED")

    outcome = kernel.finalize(
        KernelDecision(
            status="INSUFFICIENT_EVIDENCE",
            run_id=RUN_ID,
            unresolved_evidence=(
                {
                    "evidence_kind": "RELATION_HISTORY",
                    "subject": "raw_orders",
                    "reason_code": "RELATION_NOT_ALLOWED",
                },
                {
                    "evidence_kind": "INGESTION_WATERMARK",
                    "subject": "raw_orders",
                    "reason_code": "NOT_OBSERVABLE",
                },
            ),
            summary="The order ingestion boundary is unavailable.",
            recommended_actions=(),
            confidence=0.2,
        )
    )

    assert tuple(
        (item.evidence_kind, item.subject, item.reason_code)
        for item in outcome.unresolved_evidence
    ) == (
        ("RELATION_HISTORY", "raw_orders", "RELATION_NOT_ALLOWED"),
        ("INGESTION_WATERMARK", "raw_orders", "NOT_OBSERVABLE"),
    )


def test_kernel_rejects_duplicate_claim_without_successful_run_evidence() -> None:
    kernel = _duplicate_kernel()
    records = _duplicate_records()
    _close_duplicate_records(kernel, records, include_run=False)
    lineage = records[1]
    _close(
        kernel,
        gap_id="g_lineage_without_run",
        gap_kind=EvidenceGapKind.MAP_IMPACT,
        tool_name="get_dbt_lineage",
        arguments={"node_id": "seed.jaffle_shop.raw_payments", "direction": "downstream"},
        record=lineage,
    )

    with pytest.raises(KernelError, match="CLAIM_EVIDENCE_UNBOUND"):
        kernel.finalize(_semantic_duplicate_decision(records))


def test_kernel_rejects_profile_relation_not_named_by_public_incident() -> None:
    kernel = _duplicate_kernel()
    with pytest.raises(KernelError, match="RELATION_ARGUMENT_NOT_PROVEN"):
        kernel.prepare_tool(
            intent=InvestigationIntent(
                gap_id="g_private_profile",
                gap_kind=EvidenceGapKind.PROFILE_RELATION,
            ),
            tool_name="get_relation_data_profile",
            arguments={"relation_name": "raw_orders"},
        )


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
