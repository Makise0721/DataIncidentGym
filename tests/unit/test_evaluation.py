from __future__ import annotations

from datetime import UTC, datetime

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
)
from data_incident_gym.evaluation import (
    DeterministicEvaluator,
    EvaluationApplicability,
    EvaluationCheck,
    EvaluationCheckCode,
    EvaluationStatus,
)
from data_incident_gym.evidence import (
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
