from __future__ import annotations

from datetime import UTC, datetime

import pytest

from data_incident_gym.diagnosis import (
    AffectedAssetClaim,
    Diagnosis,
    DiagnosisMetrics,
    DiagnosisRunResult,
    DiagnosisStatus,
    DiagnosisTerminalTraceEvent,
    DiagnosticStrategy,
    HealthStateClaim,
    PolicyIdentity,
    RootCauseClaim,
    ToolTraceEvent,
)
from data_incident_gym.evaluation import (
    DeterministicEvaluator,
    EvaluationApplicability,
    EvaluationCheck,
    EvaluationCheckCode,
    EvaluationStatus,
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
from data_incident_gym.lab_verifier import ScenarioVerification, ScenarioVerificationStatus
from data_incident_gym.profiles import (
    ColumnProfileFact,
    HistoryPoint,
    HistorySeries,
    ProfileSnapshot,
    RelationHistorySnapshot,
    RelationProfileSnapshot,
    load_profile_spec,
)
from data_incident_gym.scenarios import load_scenario_spec

RUN_ID = "a" * 32


def _model_error() -> DiagnosisRunResult:
    strategy = DiagnosticStrategy.STATIC_SKILL
    diagnosis = Diagnosis(
        status=DiagnosisStatus.MODEL_ERROR,
        run_id=RUN_ID,
        summary="MODEL_RUNTIME_ERROR",
        confidence=0.0,
    )
    return DiagnosisRunResult(
        strategy=strategy,
        policy_identity=PolicyIdentity(
            strategy=strategy,
            base_prompt_version="p1.base.v1",
            base_prompt_sha256="b" * 64,
            strategy_prompt_version="p1.static.v1",
            strategy_prompt_sha256="c" * 64,
            controller_protocol_version="p1.controller.v1",
            controller_protocol_sha256="d" * 64,
            tool_schema_sha256="e" * 64,
        ),
        diagnosis=diagnosis,
        evidence_records=(),
        trace=(
            DiagnosisTerminalTraceEvent(
                event_type="DIAGNOSIS_TERMINAL",
                strategy=strategy,
                status=diagnosis.status,
                evidence_inventory=(),
            ),
        ),
        metrics=DiagnosisMetrics(
            provider="synthetic",
            model="synthetic-model",
            model_requests=1,
            input_tokens=0,
            output_tokens=0,
            tool_call_attempts=0,
            successful_tool_calls=0,
            elapsed_ms=1,
        ),
    )


def _verification(case_id: str) -> ScenarioVerification:
    scenario = load_scenario_spec(case_id)
    return ScenarioVerification(
        status=ScenarioVerificationStatus.EXPECTED_FAILURE,
        incident_case_id=case_id,
        run_id=RUN_ID,
        dbt_exit_code=1,
        failed_nodes=(scenario.direct_failure,),
        skipped_nodes=(),
        affected_assets=tuple(sorted(scenario.affected_assets)),
        schema_fingerprint="a" * 64,
        profile_spec_sha256="b" * 64,
    )


def _health_run(bucket: str) -> DiagnosisRunResult:
    spec = load_profile_spec()
    snapshot = ProfileSnapshot.create(
        spec=spec,
        current=(
            RelationProfileSnapshot(
                relation_name="raw_orders",
                row_count=1,
                columns=(ColumnProfileFact(column_name="id", null_count=0, distinct_count=1),),
            ),
        ),
        history=(
            RelationHistorySnapshot(
                relation_name="raw_orders",
                histories=(
                    HistorySeries(
                        name="order_count_by_day",
                        metric="count",
                        points=tuple(
                            HistoryPoint(
                                bucket=day,
                                periodic_key="1",
                                value=1,
                            )
                            for day in (
                                "2018-02-05",
                                "2018-02-12",
                                "2018-02-19",
                                "2018-02-26",
                                "2018-03-05",
                                "2018-03-12",
                                "2018-03-19",
                                "2018-03-26",
                                "2018-04-02",
                            )
                        ),
                    ),
                ),
            ),
        ),
    )
    run_record = EvidenceRecord.create(
        run_id=RUN_ID,
        evidence_type=EvidenceType.DBT_RUN_RESULTS,
        source=EvidenceSource.DBT_RUN_RESULTS,
        subject=RUN_ID,
        observed_at=datetime(2026, 8, 30, tzinfo=UTC),
        content=DbtRunResultsFact(
            kind="DBT_RUN_RESULTS",
            run_id=RUN_ID,
            run_status="SUCCEEDED",
            dbt_exit_code=0,
            failed_nodes=(),
            skipped_nodes=(),
        ),
    )
    profile_record = EvidenceRecord.create(
        run_id=RUN_ID,
        evidence_type=EvidenceType.RELATION_DATA_PROFILE,
        source=EvidenceSource.POSTGRES_PROFILE_SNAPSHOT,
        subject="raw_orders",
        observed_at=datetime(2026, 8, 30, tzinfo=UTC),
        content=RelationDataProfileFact(
            kind="RELATION_DATA_PROFILE",
            run_id=RUN_ID,
            relation_name="raw_orders",
            profile_spec_version=spec.schema_version,
            profile_spec_sha256=spec.digest(),
            snapshot=snapshot.current[0],
        ),
    )
    history_record = EvidenceRecord.create(
        run_id=RUN_ID,
        evidence_type=EvidenceType.RELATION_HISTORY,
        source=EvidenceSource.POSTGRES_PROFILE_SNAPSHOT,
        subject="raw_orders",
        observed_at=datetime(2026, 8, 30, tzinfo=UTC),
        content=RelationHistoryFact(
            kind="RELATION_HISTORY",
            run_id=RUN_ID,
            relation_name="raw_orders",
            profile_spec_version=spec.schema_version,
            profile_spec_sha256=spec.digest(),
            snapshot=snapshot.history[0],
        ),
    )
    records = (run_record, profile_record, history_record)
    evidence_ids = tuple(record.evidence_id for record in records)
    diagnosis = Diagnosis(
        status=DiagnosisStatus.NO_INCIDENT,
        run_id=RUN_ID,
        summary="Synthetic health conclusion.",
        evidence_ids=evidence_ids,
        claims=(
            HealthStateClaim(
                kind="HEALTH_STATE",
                relation_name="raw_orders",
                history_name="order_count_by_day",
                bucket=bucket,
                current_value=1,
                evidence_ids=evidence_ids,
            ),
        ),
        confidence=0.9,
    )
    terminal = DiagnosisTerminalTraceEvent(
        event_type="DIAGNOSIS_TERMINAL",
        strategy=DiagnosticStrategy.STATIC_SKILL,
        status=DiagnosisStatus.NO_INCIDENT,
        evidence_inventory=evidence_ids,
    )
    return _model_error().model_copy(
        update={"diagnosis": diagnosis, "evidence_records": records, "trace": (terminal,)}
    )


def _health_verification() -> ScenarioVerification:
    return ScenarioVerification(
        status=ScenarioVerificationStatus.HEALTHY_CONTROL,
        incident_case_id="order_volume_pattern_a",
        run_id=RUN_ID,
        dbt_exit_code=0,
        failed_nodes=(),
        skipped_nodes=(),
        affected_assets=(),
        schema_fingerprint="a" * 64,
        profile_spec_sha256="b" * 64,
    )


def test_evaluator_keeps_required_confirmation_checks_applicable_on_model_error() -> None:
    case_id = "schema_type_change_payment_amount"
    result = DeterministicEvaluator.evaluate(
        load_scenario_spec(case_id),
        _verification(case_id),
        _model_error(),
        recovery_succeeded=True,
    )

    assert result.status is EvaluationStatus.FAILED
    assert result.run_id == RUN_ID
    assert next(
        check for check in result.checks if check.code is EvaluationCheckCode.STATUS_EXACT
    ).passed is False
    assert all(
        check.applicability is EvaluationApplicability.APPLICABLE and not check.passed
        for check in result.checks
        if check.code
        in {
            EvaluationCheckCode.ROOT_CAUSE_ACCEPTED,
            EvaluationCheckCode.AFFECTED_ASSETS_EXACT,
            EvaluationCheckCode.CLAIM_EVIDENCE_COMPATIBLE,
        }
    )
    assert result.controller_checks == ()


def test_not_applicable_check_has_fixed_safe_payload() -> None:
    check = EvaluationCheck(
        code=EvaluationCheckCode.ROOT_CAUSE_ACCEPTED,
        applicability=EvaluationApplicability.NOT_APPLICABLE,
        passed=True,
        expected=("NOT_APPLICABLE",),
        actual=("NOT_APPLICABLE",),
        reason_code="NOT_APPLICABLE",
    )

    assert check.passed is True
    assert check.expected == check.actual == ("NOT_APPLICABLE",)


def test_evaluator_fails_closed_for_unknown_or_incompatible_claim_evidence() -> None:
    case_id = "schema_type_change_payment_amount"
    scenario = load_scenario_spec(case_id)
    unknown_id = "ev_" + "f" * 64
    diagnosis = Diagnosis(
        status=DiagnosisStatus.CONFIRMED,
        run_id=RUN_ID,
        root_cause_code="SOURCE_SCHEMA_COLUMN_TYPE_CHANGED",
        summary="Synthetic unsupported conclusion.",
        affected_assets=(scenario.direct_failure,),
        evidence_ids=(unknown_id,),
        claims=(
            RootCauseClaim(
                kind="ROOT_CAUSE",
                root_cause_code="SOURCE_SCHEMA_COLUMN_TYPE_CHANGED",
                evidence_ids=(unknown_id,),
            ),
            AffectedAssetClaim(
                kind="AFFECTED_ASSET",
                asset=scenario.direct_failure,
                evidence_ids=(unknown_id,),
            ),
        ),
        confidence=0.5,
    )
    diagnosis_run = _model_error().model_copy(update={"diagnosis": diagnosis})
    result = DeterministicEvaluator.evaluate(
        scenario,
        _verification(case_id),
        diagnosis_run,
        recovery_succeeded=True,
    )

    assert result.status is EvaluationStatus.FAILED
    failed = set(result.failed_check_codes)
    assert EvaluationCheckCode.EVIDENCE_IDS_EXIST in failed
    assert EvaluationCheckCode.CLAIM_EVIDENCE_COMPATIBLE in failed


def test_evaluator_rejects_health_claim_for_a_non_alert_bucket() -> None:
    scenario = load_scenario_spec("order_volume_pattern_a")
    result = DeterministicEvaluator.evaluate(
        scenario,
        _health_verification(),
        _health_run("2018-03-26"),
        recovery_succeeded=True,
    )

    assert result.status is EvaluationStatus.FAILED
    assert EvaluationCheckCode.CLAIM_EVIDENCE_COMPATIBLE in result.failed_check_codes
    assert EvaluationCheckCode.POSITIVE_HEALTH_EVIDENCE in result.failed_check_codes


def test_evaluator_rejects_root_evidence_for_an_unrelated_source_relation() -> None:
    case_id = "schema_type_change_payment_amount"
    scenario = load_scenario_spec(case_id)
    records = (
        EvidenceRecord.create(
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
                failed_nodes=(scenario.direct_failure,),
                skipped_nodes=(),
            ),
        ),
        EvidenceRecord.create(
            run_id=RUN_ID,
            evidence_type=EvidenceType.DBT_NODE_ERROR,
            source=EvidenceSource.DBT_RUN_RESULTS,
            subject=scenario.direct_failure,
            observed_at=datetime(2026, 8, 30, tzinfo=UTC),
            content=DbtNodeErrorFact(
                kind="DBT_NODE_ERROR",
                run_id=RUN_ID,
                node_id=scenario.direct_failure,
                resource_type="model",
                status="error",
                message="Synthetic failure.",
            ),
        ),
        EvidenceRecord.create(
            run_id=RUN_ID,
            evidence_type=EvidenceType.RELATION_SCHEMA,
            source=EvidenceSource.POSTGRES_CATALOG,
            subject="analytics.raw_orders",
            observed_at=datetime(2026, 8, 30, tzinfo=UTC),
            content=RelationSchemaFact(
                kind="RELATION_SCHEMA",
                run_id=RUN_ID,
                schema_name="analytics",
                relation_name="raw_orders",
                columns=(
                    RelationSchemaColumn(
                        name="amount",
                        data_type="text",
                        nullable=True,
                        ordinal_position=1,
                    ),
                ),
            ),
        ),
    )
    evidence_ids = tuple(record.evidence_id for record in records)
    diagnosis = Diagnosis(
        status=DiagnosisStatus.CONFIRMED,
        run_id=RUN_ID,
        root_cause_code="SOURCE_SCHEMA_COLUMN_TYPE_CHANGED",
        summary="Synthetic unsupported conclusion.",
        affected_assets=(scenario.direct_failure,),
        evidence_ids=evidence_ids,
        claims=(
            RootCauseClaim(
                kind="ROOT_CAUSE",
                root_cause_code="SOURCE_SCHEMA_COLUMN_TYPE_CHANGED",
                evidence_ids=evidence_ids,
            ),
            AffectedAssetClaim(
                kind="AFFECTED_ASSET",
                asset=scenario.direct_failure,
                evidence_ids=(records[1].evidence_id,),
            ),
        ),
        confidence=0.5,
    )
    terminal = DiagnosisTerminalTraceEvent(
        event_type="DIAGNOSIS_TERMINAL",
        strategy=DiagnosticStrategy.STATIC_SKILL,
        status=DiagnosisStatus.CONFIRMED,
        evidence_inventory=evidence_ids,
    )
    result = DeterministicEvaluator.evaluate(
        scenario,
        _verification(case_id),
        _model_error().model_copy(
            update={"diagnosis": diagnosis, "evidence_records": records, "trace": (terminal,)}
        ),
        recovery_succeeded=True,
    )

    assert result.status is EvaluationStatus.FAILED
    assert EvaluationCheckCode.CLAIM_EVIDENCE_COMPATIBLE in result.failed_check_codes


def _m8_records(profile_relation: str) -> tuple[EvidenceRecord, ...]:
    scenario = load_scenario_spec("required_null_order_customer_a")
    assert scenario.direct_failure is not None
    profile_spec = load_profile_spec()
    test_id = scenario.direct_failure
    model_id = "model.jaffle_shop.orders"
    return (
        EvidenceRecord.create(
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
                failed_nodes=(test_id,),
                skipped_nodes=(),
            ),
        ),
        EvidenceRecord.create(
            run_id=RUN_ID,
            evidence_type=EvidenceType.DBT_NODE_ERROR,
            source=EvidenceSource.DBT_RUN_RESULTS,
            subject=test_id,
            observed_at=datetime(2026, 8, 30, tzinfo=UTC),
            content=DbtNodeErrorFact(
                kind="DBT_NODE_ERROR",
                run_id=RUN_ID,
                node_id=test_id,
                resource_type="test",
                status="fail",
                message="required field is null",
            ),
        ),
        EvidenceRecord.create(
            run_id=RUN_ID,
            evidence_type=EvidenceType.DBT_LINEAGE,
            source=EvidenceSource.DBT_MANIFEST,
            subject=test_id,
            observed_at=datetime(2026, 8, 30, tzinfo=UTC),
            content=DbtLineageFact(
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
        ),
        EvidenceRecord.create(
            run_id=RUN_ID,
            evidence_type=EvidenceType.RELATION_SCHEMA,
            source=EvidenceSource.POSTGRES_CATALOG,
            subject="raw_orders",
            observed_at=datetime(2026, 8, 30, tzinfo=UTC),
            content=RelationSchemaFact(
                kind="RELATION_SCHEMA",
                run_id=RUN_ID,
                schema_name="staging",
                relation_name="raw_orders",
                columns=(
                    RelationSchemaColumn(
                        name="user_id",
                        data_type="integer",
                        nullable=True,
                        ordinal_position=2,
                    ),
                ),
            ),
        ),
        EvidenceRecord.create(
            run_id=RUN_ID,
            evidence_type=EvidenceType.RELATION_DATA_PROFILE,
            source=EvidenceSource.POSTGRES_PROFILE_SNAPSHOT,
            subject=profile_relation,
            observed_at=datetime(2026, 8, 30, tzinfo=UTC),
            content=RelationDataProfileFact(
                kind="RELATION_DATA_PROFILE",
                run_id=RUN_ID,
                relation_name=profile_relation,
                profile_spec_version=profile_spec.schema_version,
                profile_spec_sha256=profile_spec.digest(),
                snapshot=(
                    RelationProfileSnapshot(
                        relation_name="raw_orders",
                        row_count=99,
                        columns=(
                            ColumnProfileFact(
                                column_name="user_id",
                                null_count=1,
                                distinct_count=98,
                            ),
                        ),
                    )
                    if profile_relation == "raw_orders"
                    else RelationProfileSnapshot(
                        relation_name="raw_customers",
                        row_count=100,
                        columns=(
                            ColumnProfileFact(
                                column_name="last_name",
                                null_count=1,
                                distinct_count=99,
                            ),
                        ),
                    )
                ),
            ),
        ),
    )


def _m8_tool_trace(records: tuple[EvidenceRecord, ...]) -> tuple[ToolTraceEvent, ...]:
    test_id = "test.jaffle_shop.not_null_orders_customer_id.c5f02694af"
    relation = records[-1].content.relation_name
    specs = (
        ("get_dbt_run_results", {"run_id": RUN_ID}),
        ("get_dbt_node_error", {"run_id": RUN_ID, "node_id": test_id}),
        (
            "get_dbt_lineage",
            {"node_id": test_id, "direction": "upstream"},
        ),
        ("get_relation_schema", {"relation_name": "raw_orders"}),
        ("get_relation_data_profile", {"relation_name": relation}),
    )
    return tuple(
        ToolTraceEvent(
            event_type="TOOL_CALL",
            tool_name=tool_name,
            arguments=arguments,
            fingerprint=f"{index + 1:064x}",
            evidence_ids=(record.evidence_id,),
            elapsed_ms=1,
        )
        for index, ((tool_name, arguments), record) in enumerate(zip(specs, records, strict=True))
    )


def _m8_confirmed_run(profile_relation: str) -> DiagnosisRunResult:
    scenario = load_scenario_spec("required_null_order_customer_a")
    assert scenario.direct_failure is not None
    records = _m8_records(profile_relation)
    evidence_ids = tuple(record.evidence_id for record in records)
    lineage_id = records[2].evidence_id
    diagnosis = Diagnosis(
        status=DiagnosisStatus.CONFIRMED,
        run_id=RUN_ID,
        root_cause_code="SOURCE_REQUIRED_FIELD_NULL",
        summary="The required source field is null.",
        affected_assets=("model.jaffle_shop.orders",),
        evidence_ids=evidence_ids,
        claims=(
            RootCauseClaim(
                kind="ROOT_CAUSE",
                root_cause_code="SOURCE_REQUIRED_FIELD_NULL",
                evidence_ids=evidence_ids,
            ),
            AffectedAssetClaim(
                kind="AFFECTED_ASSET",
                asset="model.jaffle_shop.orders",
                evidence_ids=(lineage_id,),
            ),
        ),
        confidence=0.9,
    )
    terminal = DiagnosisTerminalTraceEvent(
        event_type="DIAGNOSIS_TERMINAL",
        strategy=DiagnosticStrategy.STATIC_SKILL,
        status=DiagnosisStatus.CONFIRMED,
        evidence_inventory=evidence_ids,
    )
    return _model_error().model_copy(
        update={
            "diagnosis": diagnosis,
            "evidence_records": records,
            "trace": (*_m8_tool_trace(records), terminal),
        }
    )


def test_evaluator_accepts_the_m8_source_null_and_test_model_claim() -> None:
    case_id = "required_null_order_customer_a"
    run = _m8_confirmed_run("raw_orders")
    result = DeterministicEvaluator.evaluate(
        load_scenario_spec(case_id),
        _verification(case_id),
        run,
        recovery_succeeded=True,
    )

    assert result.status is EvaluationStatus.PASSED
    assert EvaluationCheckCode.CLAIM_EVIDENCE_COMPATIBLE not in result.failed_check_codes


def test_evaluator_rejects_m8_distractor_profile_as_source_null_evidence() -> None:
    case_id = "required_null_order_customer_a"
    result = DeterministicEvaluator.evaluate(
        load_scenario_spec(case_id),
        _verification(case_id),
        _m8_confirmed_run("raw_customers"),
        recovery_succeeded=True,
    )

    assert result.status is EvaluationStatus.FAILED
    assert EvaluationCheckCode.CLAIM_EVIDENCE_COMPATIBLE in result.failed_check_codes


def test_evaluator_rejects_failed_test_id_as_an_affected_asset() -> None:
    case_id = "required_null_order_customer_a"
    scenario = load_scenario_spec(case_id)
    run = _m8_confirmed_run("raw_orders")
    original = run.diagnosis
    root_claim = next(claim for claim in original.claims if claim.kind == "ROOT_CAUSE")
    node_error_id = run.evidence_records[1].evidence_id
    invalid = Diagnosis(
        status=DiagnosisStatus.CONFIRMED,
        run_id=RUN_ID,
        root_cause_code=original.root_cause_code,
        summary=original.summary,
        affected_assets=(scenario.direct_failure,),
        evidence_ids=original.evidence_ids,
        claims=(
            root_claim,
            AffectedAssetClaim(
                kind="AFFECTED_ASSET",
                asset=scenario.direct_failure,
                evidence_ids=(node_error_id,),
            ),
        ),
        confidence=original.confidence,
    )
    result = DeterministicEvaluator.evaluate(
        scenario,
        _verification(case_id),
        run.model_copy(update={"diagnosis": invalid}),
        recovery_succeeded=True,
    )

    assert result.status is EvaluationStatus.FAILED
    assert EvaluationCheckCode.AFFECTED_ASSETS_EXACT in result.failed_check_codes


@pytest.mark.parametrize(
    ("tool_name", "error_code", "expected"),
    (
        ("get_relation_data_profile", "RELATION_NOT_ALLOWED", True),
        ("get_relation_schema", "RELATION_NOT_ALLOWED", False),
        ("get_relation_data_profile", None, False),
        ("get_relation_data_profile", "RELATION_NOT_FOUND", False),
    ),
)
def test_evaluator_requires_the_exact_failed_profile_gap_attempt(
    tool_name: str,
    error_code: str | None,
    expected: bool,
) -> None:
    scenario = load_scenario_spec("required_null_order_customer_b")
    diagnosis = Diagnosis(
        status=DiagnosisStatus.INSUFFICIENT_EVIDENCE,
        run_id=RUN_ID,
        summary="The source profile and transformation are unavailable.",
        unresolved_evidence=(
            {
                "evidence_kind": "RELATION_DATA_PROFILE",
                "subject": "raw_orders",
                "reason_code": "RELATION_NOT_ALLOWED",
            },
            {
                "evidence_kind": "TRANSFORMATION_DEFINITION",
                "subject": "model.jaffle_shop.stg_orders",
                "reason_code": "NOT_OBSERVABLE",
            },
        ),
        confidence=0.2,
    )
    event = ToolTraceEvent(
        event_type="TOOL_CALL",
        tool_name=tool_name,
        arguments={"relation_name": "raw_orders"},
        fingerprint="f" * 64,
        evidence_ids=(),
        error_code=error_code,
        elapsed_ms=1,
    )
    terminal = DiagnosisTerminalTraceEvent(
        event_type="DIAGNOSIS_TERMINAL",
        strategy=DiagnosticStrategy.STATIC_SKILL,
        status=DiagnosisStatus.INSUFFICIENT_EVIDENCE,
        evidence_inventory=(),
    )
    result = DeterministicEvaluator.evaluate(
        scenario,
        _verification(scenario.incident_case_id),
        _model_error().model_copy(update={"diagnosis": diagnosis, "trace": (event, terminal)}),
        recovery_succeeded=True,
    )

    check = next(
        check
        for check in result.checks
        if check.code is EvaluationCheckCode.INSUFFICIENCY_GAP_DECLARED
    )
    assert check.passed is expected
