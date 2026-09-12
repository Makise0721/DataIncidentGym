"""The canonical asset identifier contract for confirmed decisions.

Pins the four acceptance criteria of the seq14 contract fix: full node_id
values supported by the cited evidence are accepted, bare node names are
rejected with a recovery-oriented correction that only names a replacement
when the cited evidence legally maps to exactly one node, the same kernel
recovers by resubmitting with the identifier, and no value is ever rewritten.
"""

import json
from datetime import UTC, datetime

import pytest

from data_incident_gym.diagnosis import (
    AffectedAssetClaim,
    Diagnosis,
    DiagnosisMetrics,
    DiagnosisRunResult,
    DiagnosisStatus,
    DiagnosisTerminalTraceEvent,
    KernelStateTraceEvent,
    PolicyIdentity,
    RootCauseClaim,
    ToolTraceEvent,
)
from data_incident_gym.diagnostic_agent import (
    CONTROLLER_PROTOCOL_VERSION,
    KERNEL_PROMPT_VERSION,
    DiagnosticStrategy,
    _kernel_retry_message,
)
from data_incident_gym.diagnostic_contracts import (
    ClaimEvidence,
    EvidenceGapKind,
    Hypothesis,
    HypothesisAssessment,
    HypothesisVerdict,
    InvestigationIntent,
    KernelDecision,
    KernelError,
)
from data_incident_gym.diagnostic_kernel import DiagnosticKernel
from data_incident_gym.diagnostic_validation import validate_asset_claims
from data_incident_gym.evaluation import DeterministicEvaluator
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
from data_incident_gym.lab_verifier import (
    ScenarioVerification,
    ScenarioVerificationStatus,
)
from data_incident_gym.scenarios import parse_scenario_spec

RUN_ID = "b" * 32
FAILED_NODE = "model.fixture.customers"
STG_PAYMENTS_ID = "model.fixture.stg_payments"
EXPORT_ID = "model.fixture.customers_export"


def _record(
    evidence_type: EvidenceType, source: EvidenceSource, subject: str, content
) -> EvidenceRecord:
    return EvidenceRecord.create(
        run_id=RUN_ID,
        evidence_type=evidence_type,
        source=source,
        subject=subject,
        observed_at=datetime(2026, 9, 12, tzinfo=UTC),
        content=content,
    )


def _run_results() -> EvidenceRecord:
    return _record(
        EvidenceType.DBT_RUN_RESULTS,
        EvidenceSource.DBT_RUN_RESULTS,
        RUN_ID,
        DbtRunResultsFact(
            kind="DBT_RUN_RESULTS",
            run_id=RUN_ID,
            run_status="FAILED",
            dbt_exit_code=1,
            failed_nodes=(FAILED_NODE,),
            skipped_nodes=(),
        ),
    )


def _node_error() -> EvidenceRecord:
    return _record(
        EvidenceType.DBT_NODE_ERROR,
        EvidenceSource.DBT_RUN_RESULTS,
        FAILED_NODE,
        DbtNodeErrorFact(
            kind="DBT_NODE_ERROR",
            run_id=RUN_ID,
            node_id=FAILED_NODE,
            resource_type="model",
            status="error",
            message="Database Error in model customers: operator does not exist: integer = text",
        ),
    )


def _upstream_lineage() -> EvidenceRecord:
    return _record(
        EvidenceType.DBT_LINEAGE,
        EvidenceSource.DBT_MANIFEST,
        FAILED_NODE,
        DbtLineageFact(
            kind="DBT_LINEAGE",
            run_id=RUN_ID,
            node_id=FAILED_NODE,
            direction="upstream",
            related_nodes=(
                DbtLineageNode(
                    node_id="model.fixture.stg_orders",
                    resource_type="model",
                    name="stg_orders",
                    distance=1,
                ),
                DbtLineageNode(
                    node_id="seed.fixture.raw_orders",
                    resource_type="seed",
                    name="raw_orders",
                    distance=2,
                ),
                # Unique name, but upstream distance 2: it never satisfies the
                # original support conditions and must not be offered as a fix.
                DbtLineageNode(
                    node_id="model.fixture.late_orders",
                    resource_type="model",
                    name="late_orders",
                    distance=2,
                ),
            ),
        ),
    )


def _schema_raw_orders() -> EvidenceRecord:
    return _record(
        EvidenceType.RELATION_SCHEMA,
        EvidenceSource.POSTGRES_CATALOG,
        "raw_orders",
        RelationSchemaFact(
            kind="RELATION_SCHEMA",
            run_id=RUN_ID,
            schema_name="fixture",
            relation_name="raw_orders",
            columns=(
                RelationSchemaColumn(
                    name="user_id", data_type="text", nullable=True, ordinal_position=2
                ),
            ),
        ),
    )


def _downstream_lineage() -> EvidenceRecord:
    return _record(
        EvidenceType.DBT_LINEAGE,
        EvidenceSource.DBT_MANIFEST,
        FAILED_NODE,
        DbtLineageFact(
            kind="DBT_LINEAGE",
            run_id=RUN_ID,
            node_id=FAILED_NODE,
            direction="downstream",
            related_nodes=(
                DbtLineageNode(
                    node_id=STG_PAYMENTS_ID,
                    resource_type="model",
                    name="stg_payments",
                    distance=1,
                ),
                DbtLineageNode(
                    node_id=EXPORT_ID,
                    resource_type="model",
                    name="customers_export",
                    distance=2,
                ),
            ),
        ),
    )


def _second_downstream_lineage() -> EvidenceRecord:
    """A second record that maps the same bare name to a different node_id."""

    return _record(
        EvidenceType.DBT_LINEAGE,
        EvidenceSource.DBT_MANIFEST,
        STG_PAYMENTS_ID,
        DbtLineageFact(
            kind="DBT_LINEAGE",
            run_id=RUN_ID,
            node_id=STG_PAYMENTS_ID,
            direction="downstream",
            related_nodes=(
                DbtLineageNode(
                    node_id="model.other.customers_export",
                    resource_type="model",
                    name="customers_export",
                    distance=1,
                ),
            ),
        ),
    )


_CALLS = (
    ("g_locate", EvidenceGapKind.LOCATE_FAILURE, "get_dbt_run_results", {"run_id": RUN_ID}),
    (
        "g_explain",
        EvidenceGapKind.EXPLAIN_FAILURE,
        "get_dbt_node_error",
        {"run_id": RUN_ID, "node_id": FAILED_NODE},
    ),
    (
        "g_discover",
        EvidenceGapKind.DISCOVER_SOURCE_RELATION,
        "get_dbt_lineage",
        {"node_id": FAILED_NODE, "direction": "upstream"},
    ),
    (
        "g_schema",
        EvidenceGapKind.DISCRIMINATE_SCHEMA,
        "get_relation_schema",
        {"relation_name": "raw_orders"},
    ),
    (
        "g_impact",
        EvidenceGapKind.MAP_IMPACT,
        "get_dbt_lineage",
        {"node_id": FAILED_NODE, "direction": "downstream"},
    ),
    (
        "g_impact_two",
        EvidenceGapKind.MAP_IMPACT,
        "get_dbt_lineage",
        {"node_id": STG_PAYMENTS_ID, "direction": "downstream"},
    ),
)


def _build_kernel() -> tuple[
    DiagnosticKernel,
    dict[str, EvidenceRecord],
    list[tuple[str, dict[str, str], EvidenceRecord]],
]:
    kernel = DiagnosticKernel.start(
        run_id=RUN_ID,
        allowed_root_cause_codes=(
            "SOURCE_SCHEMA_COLUMN_TYPE_CHANGED",
            "TRANSFORMATION_COLUMN_CAST_CHANGED",
        ),
        model_request_limit=8,
        tool_call_limit=8,
        observable_schema_relations=("raw_orders",),
        incident_subjects=(FAILED_NODE,),
        lineage_node_candidates=(FAILED_NODE,),
    )
    records = {
        "run": _run_results(),
        "error": _node_error(),
        "upstream": _upstream_lineage(),
        "schema": _schema_raw_orders(),
        "downstream": _downstream_lineage(),
        "second": _second_downstream_lineage(),
    }
    record_order = (
        records["run"],
        records["error"],
        records["upstream"],
        records["schema"],
        records["downstream"],
        records["second"],
    )
    hypotheses = (
        Hypothesis(
            hypothesis_id="h_source",
            root_cause_code="SOURCE_SCHEMA_COLUMN_TYPE_CHANGED",
        ),
        Hypothesis(
            hypothesis_id="h_cast",
            root_cause_code="TRANSFORMATION_COLUMN_CAST_CHANGED",
        ),
    )
    trace_calls: list[tuple[str, dict[str, str], EvidenceRecord]] = []
    for index, (gap_id, gap_kind, tool, arguments) in enumerate(_CALLS):
        prepared = kernel.prepare_tool(
            intent=InvestigationIntent(
                gap_id=gap_id,
                gap_kind=gap_kind,
                hypothesis_ids=(),
                new_hypotheses=hypotheses if index == 1 else (),
            ),
            tool_name=tool,
            arguments=arguments,
        )
        record = record_order[index]
        kernel.record_tool_result(prepared, (record,))
        trace_calls.append((tool, arguments, record))
    return kernel, records, trace_calls


def _decision(
    records: dict[str, EvidenceRecord], asset_claims: tuple[ClaimEvidence, ...]
) -> KernelDecision:
    return KernelDecision(
        status="CONFIRMED",
        run_id=RUN_ID,
        selected_hypothesis_id="h_source",
        assessments=(
            HypothesisAssessment(
                hypothesis_id="h_source",
                verdict=HypothesisVerdict.SUPPORTED,
                evidence_ids=(records["error"].evidence_id, records["schema"].evidence_id),
            ),
            HypothesisAssessment(
                hypothesis_id="h_cast",
                verdict=HypothesisVerdict.REFUTED,
                evidence_ids=(records["error"].evidence_id, records["upstream"].evidence_id),
            ),
        ),
        claims=(
            ClaimEvidence(
                kind="ROOT_CAUSE",
                value="SOURCE_SCHEMA_COLUMN_TYPE_CHANGED",
                evidence_ids=(records["error"].evidence_id, records["schema"].evidence_id),
                relation_name="raw_orders",
            ),
            *asset_claims,
        ),
        summary="fixture summary",
        recommended_actions=("re-run the failed export",),
        confidence=0.9,
    )


def test_full_node_id_supported_by_cited_evidence_is_accepted() -> None:
    kernel, records, _ = _build_kernel()

    outcome = kernel.finalize(
        _decision(
            records,
            (
                ClaimEvidence(
                    kind="AFFECTED_ASSET",
                    value=FAILED_NODE,
                    evidence_ids=(records["error"].evidence_id,),
                ),
                ClaimEvidence(
                    kind="AFFECTED_ASSET",
                    value=STG_PAYMENTS_ID,
                    evidence_ids=(records["downstream"].evidence_id,),
                ),
            )
        )
    )

    assert outcome.status.value == "CONFIRMED"
    assert outcome.affected_assets == (FAILED_NODE, STG_PAYMENTS_ID)


def test_bare_name_is_rejected_and_feedback_names_the_unique_node_id() -> None:
    kernel, records, _ = _build_kernel()

    with pytest.raises(KernelError) as error:
        kernel.finalize(
            _decision(
                records,
                (
                    ClaimEvidence(
                        kind="AFFECTED_ASSET",
                        value="stg_payments",
                        evidence_ids=(records["downstream"].evidence_id,),
                    ),
                )
            )
        )

    assert error.value.code == "ASSET_CLAIM_NAME_NOT_IDENTIFIER"
    assert error.value.detail == (STG_PAYMENTS_ID,)
    message = _kernel_retry_message(error.value.code, detail=error.value.detail)
    assert "full node identifier" in message
    assert STG_PAYMENTS_ID in message


def test_ambiguous_name_is_rejected_without_a_suggested_value() -> None:
    kernel, records, _ = _build_kernel()

    with pytest.raises(KernelError) as error:
        kernel.finalize(
            _decision(
                records,
                (
                    ClaimEvidence(
                        kind="AFFECTED_ASSET",
                        value="customers_export",
                        evidence_ids=(
                            records["downstream"].evidence_id,
                            records["second"].evidence_id,
                        ),
                    ),
                )
            )
        )

    assert error.value.code == "ASSET_CLAIM_NAME_NOT_IDENTIFIER"
    assert error.value.detail == ()
    message = _kernel_retry_message(error.value.code)
    assert "full node identifier" in message
    assert EXPORT_ID not in message
    assert "model.other.customers_export" not in message


def test_unique_but_unsupported_name_keeps_the_original_rejection() -> None:
    kernel, records, _ = _build_kernel()

    with pytest.raises(KernelError) as error:
        kernel.finalize(
            _decision(
                records,
                (
                    ClaimEvidence(
                        kind="AFFECTED_ASSET",
                        value="late_orders",
                        evidence_ids=(records["upstream"].evidence_id,),
                    ),
                )
            )
        )

    assert error.value.code == "ASSET_CLAIM_EVIDENCE_INCOMPATIBLE"


def test_unknown_value_keeps_the_original_rejection() -> None:
    kernel, records, _ = _build_kernel()

    with pytest.raises(KernelError) as error:
        validate_asset_claims(
            kernel._validation_context(),
            (
                ClaimEvidence(
                    kind="AFFECTED_ASSET",
                    value="model.fixture.ghost",
                    evidence_ids=(records["error"].evidence_id,),
                ),
            ),
            [records["error"]],
            {records["error"].evidence_id: records["error"]},
        )

    assert error.value.code == "ASSET_CLAIM_EVIDENCE_INCOMPATIBLE"


def test_same_kernel_recovers_by_resubmitting_the_identifier() -> None:
    kernel, records, _ = _build_kernel()
    short_claim = ClaimEvidence(
        kind="AFFECTED_ASSET",
        value="stg_payments",
        evidence_ids=(records["downstream"].evidence_id,),
    )

    with pytest.raises(KernelError) as error:
        kernel.finalize(_decision(records, (short_claim,)))
    assert error.value.code == "ASSET_CLAIM_NAME_NOT_IDENTIFIER"

    outcome = kernel.finalize(
        _decision(
            records,
            (
                ClaimEvidence(
                    kind="AFFECTED_ASSET",
                    value=STG_PAYMENTS_ID,
                    evidence_ids=short_claim.evidence_ids,
                ),
            )
        )
    )

    assert outcome.affected_assets == (STG_PAYMENTS_ID,)


def test_recovered_outcome_passes_the_evaluator_asset_check() -> None:
    kernel, records, trace_calls = _build_kernel()
    failed_node_claim = ClaimEvidence(
        kind="AFFECTED_ASSET",
        value=FAILED_NODE,
        evidence_ids=(records["error"].evidence_id,),
    )
    short_claim = ClaimEvidence(
        kind="AFFECTED_ASSET",
        value="stg_payments",
        evidence_ids=(records["downstream"].evidence_id,),
    )
    # The first submission already contains both asset claims; only one uses a
    # bare name, so the sole rejection reason is the non-canonical identifier.
    with pytest.raises(KernelError) as error:
        kernel.finalize(_decision(records, (failed_node_claim, short_claim)))
    assert error.value.code == "ASSET_CLAIM_NAME_NOT_IDENTIFIER"

    # Recovery replaces only the identifier; every other field is unchanged.
    outcome = kernel.finalize(
        _decision(
            records,
            (
                failed_node_claim,
                ClaimEvidence(
                    kind="AFFECTED_ASSET",
                    value=STG_PAYMENTS_ID,
                    evidence_ids=short_claim.evidence_ids,
                ),
            )
        )
    )
    assert outcome.affected_assets == (FAILED_NODE, STG_PAYMENTS_ID)

    tool_events = tuple(
        ToolTraceEvent(
            event_type="TOOL_CALL",
            tool_name=tool,
            arguments=arguments,
            fingerprint=f"{index:064x}",
            evidence_ids=(record.evidence_id,),
            elapsed_ms=0,
        )
        for index, (tool, arguments, record) in enumerate(trace_calls)
    )
    snapshot = kernel.snapshot(model_requests_used=4)
    trace = (
        *tool_events,
        KernelStateTraceEvent(event_type="KERNEL_STATE", state=snapshot),
        DiagnosisTerminalTraceEvent(
            event_type="DIAGNOSIS_TERMINAL",
            strategy=DiagnosticStrategy.DIAGNOSTIC_KERNEL,
            status=DiagnosisStatus.CONFIRMED,
            evidence_inventory=tuple(record.evidence_id for record in records.values()),
        ),
    )
    diagnosis = Diagnosis(
        status=DiagnosisStatus.CONFIRMED,
        run_id=RUN_ID,
        root_cause_code="SOURCE_SCHEMA_COLUMN_TYPE_CHANGED",
        summary=outcome.summary,
        affected_assets=outcome.affected_assets,
        evidence_ids=tuple(record.evidence_id for record in records.values()),
        claims=(
            RootCauseClaim(
                kind="ROOT_CAUSE",
                root_cause_code="SOURCE_SCHEMA_COLUMN_TYPE_CHANGED",
                evidence_ids=(records["error"].evidence_id, records["schema"].evidence_id),
            ),
            AffectedAssetClaim(
                kind="AFFECTED_ASSET",
                asset=FAILED_NODE,
                evidence_ids=(records["error"].evidence_id,),
            ),
            AffectedAssetClaim(
                kind="AFFECTED_ASSET",
                asset=STG_PAYMENTS_ID,
                evidence_ids=(records["downstream"].evidence_id,),
            ),
        ),
        recommended_actions=outcome.recommended_actions,
        confidence=outcome.confidence,
    )
    run_result = DiagnosisRunResult(
        strategy=DiagnosticStrategy.DIAGNOSTIC_KERNEL,
        policy_identity=PolicyIdentity(
            strategy=DiagnosticStrategy.DIAGNOSTIC_KERNEL,
            base_prompt_version="p1.base.v1",
            base_prompt_sha256="c" * 64,
            strategy_prompt_version=KERNEL_PROMPT_VERSION,
            strategy_prompt_sha256="c" * 64,
            controller_protocol_version=CONTROLLER_PROTOCOL_VERSION,
            controller_protocol_sha256="c" * 64,
            tool_schema_sha256="c" * 64,
        ),
        diagnosis=diagnosis,
        evidence_records=tuple(records.values()),
        trace=trace,
        metrics=DiagnosisMetrics(
            provider="fixture",
            model="fixture",
            model_requests=4,
            input_tokens=0,
            output_tokens=0,
            tool_call_attempts=6,
            successful_tool_calls=6,
            elapsed_ms=0,
        ),
        kernel_state=snapshot,
    )
    scenario = parse_scenario_spec(json.dumps(_fixture_spec()), "fixture-spec.json")
    verification = ScenarioVerification(
        status=ScenarioVerificationStatus.EXPECTED_FAILURE,
        incident_case_id=scenario.incident_case_id,
        run_id=RUN_ID,
        dbt_exit_code=1,
        failed_nodes=(scenario.direct_failure,),
        skipped_nodes=(),
        affected_assets=scenario.affected_assets,
        schema_fingerprint="0" * 64,
        profile_spec_sha256="0" * 64,
    )

    result = DeterministicEvaluator.evaluate(
        scenario, verification, run_result, recovery_succeeded=True
    )

    asset_check = next(
        check for check in result.checks if check.code.value == "AFFECTED_ASSETS_EXACT"
    )
    assert asset_check.passed is True
    assert set(asset_check.actual) == {FAILED_NODE, STG_PAYMENTS_ID}


def _fixture_spec() -> dict:
    """A synthetic schema-type-change contract; no real scenario data."""

    return {
        "schema_version": "scenario.v1",
        "incident_case_id": "asset_identifier_contract_fixture",
        "suite": "P1",
        "fault_family": "SCHEMA_TYPE_CHANGE",
        "variant_role": "TEST_CONFIRMABLE",
        "answerability": "CONFIRMABLE",
        "seed": {
            "schema_version": "seed.v1",
            "fixture_path": "third_party/jaffle_shop",
            "fixture_commit": "a" * 40,
            "seed_names": ("raw_customers", "raw_orders", "raw_payments"),
            "refresh": "FULL_REFRESH",
        },
        "incident_brief": {
            "schema_version": "incident_brief.v1",
            "signal_code": "SCHEMA_TYPE_CHANGE_ALERT",
            "summary": "A fixture alert for the canonical asset identifier contract.",
            "subjects": (FAILED_NODE,),
            "logical_observed_at": "2026-09-12T00:00:00+00:00",
            "observations": [],
        },
        "reset_and_injection_contract": {
            "schema_version": "reset_injection.v1",
            "mutations": (
                {
                    "kind": "COLUMN_TYPE_CHANGE",
                    "relation": "raw_orders",
                    "column": "user_id",
                    "from_type": "integer",
                    "to_type": "text",
                },
            ),
            "restore_strategy": "FULL_REFRESH_BASELINE",
        },
        "ground_truth_or_acceptable_root_causes": ("SOURCE_SCHEMA_COLUMN_TYPE_CHANGED",),
        "direct_failure": FAILED_NODE,
        "affected_assets": (FAILED_NODE, STG_PAYMENTS_ID),
        "observable_evidence_contract": {
            "schema_version": "observable_evidence.v1",
            "schema_relations": ("raw_orders",),
            "profile_relations": (),
            "history_relations": (),
            "unresolved_gaps": (),
        },
        "required_evidence_types": ("DBT_NODE_ERROR", "RELATION_SCHEMA"),
        "forbidden_leakage": (
            "SCENARIO_SPEC",
            "GROUND_TRUTH",
            "VARIANT_ROLE",
            "ANSWERABILITY",
            "EXPECTED_STATUS",
        ),
        "distractors": (),
        "expected_status": "CONFIRMED",
    }
