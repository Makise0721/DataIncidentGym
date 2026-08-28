from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from data_incident_gym.config import PROJECT_ROOT
from data_incident_gym.diagnosis import (
    Diagnosis,
    DiagnosisMetrics,
    DiagnosisRunResult,
    DiagnosisStatus,
    EvidenceGateTraceEvent,
    ToolTraceEvent,
)
from data_incident_gym.evaluation import (
    ALLOWED_DIAGNOSTIC_TOOLS,
    DeterministicEvaluator,
    EvaluationCheck,
    EvaluationCheckCode,
    EvaluationResult,
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
    RelationSchemaColumn,
    RelationSchemaFact,
)
from data_incident_gym.incidents import CASE_ID, GroundTruth, load_ground_truth
from data_incident_gym.lab_verifier import LabVerification

RUN_ID = "a" * 32
OTHER_RUN_ID = "c" * 32
OBSERVED_AT = datetime(2026, 8, 28, 0, 0, tzinfo=UTC)
CHECK_ORDER = (
    "ENVIRONMENT_VERIFIED",
    "DIAGNOSIS_CONFIRMED",
    "ROOT_CAUSE_EXACT",
    "AFFECTED_ASSETS_EXACT",
    "EVIDENCE_IDS_EXIST",
    "EVIDENCE_RUN_SCOPE",
    "REQUIRED_EVIDENCE_TYPES_PRESENT",
    "EVIDENCE_CONTENT_COMPATIBLE",
    "TRACE_READ_ONLY_SAFE",
    "RECOVERY_HEALTHY",
)


def _tool_event(
    tool_name: str,
    arguments: dict[str, str],
    evidence_ids: tuple[str, ...],
    fingerprint: str,
) -> ToolTraceEvent:
    return ToolTraceEvent(
        event_type="TOOL_CALL",
        tool_name=tool_name,
        arguments=arguments,
        fingerprint=fingerprint,
        evidence_ids=evidence_ids,
        elapsed_ms=0,
    )


def _valid_inputs() -> tuple[GroundTruth, LabVerification, DiagnosisRunResult]:
    truth = load_ground_truth(CASE_ID, PROJECT_ROOT)
    fault_columns = tuple(
        RelationSchemaColumn(
            name=column.name,
            data_type=column.data_type,
            nullable=column.nullable,
            ordinal_position=column.ordinal_position,
        )
        for column in truth.expected_schema.fault_column_metadata
    )
    run_results = EvidenceRecord.create(
        run_id=RUN_ID,
        evidence_type=EvidenceType.DBT_RUN_RESULTS,
        source=EvidenceSource.DBT_RUN_RESULTS,
        subject=RUN_ID,
        observed_at=OBSERVED_AT,
        content=DbtRunResultsFact(
            kind="DBT_RUN_RESULTS",
            run_id=RUN_ID,
            run_status="FAILED",
            dbt_exit_code=1,
            failed_nodes=(truth.direct_failure,),
            skipped_nodes=(),
        ),
    )
    node_error = EvidenceRecord.create(
        run_id=RUN_ID,
        evidence_type=EvidenceType.DBT_NODE_ERROR,
        source=EvidenceSource.DBT_RUN_RESULTS,
        subject=truth.direct_failure,
        observed_at=OBSERVED_AT,
        content=DbtNodeErrorFact(
            kind="DBT_NODE_ERROR",
            run_id=RUN_ID,
            node_id=truth.direct_failure,
            resource_type="model",
            status="error",
            message="column amount does not exist",
        ),
    )
    schema = EvidenceRecord.create(
        run_id=RUN_ID,
        evidence_type=EvidenceType.RELATION_SCHEMA,
        source=EvidenceSource.POSTGRES_CATALOG,
        subject=truth.injection.relation,
        observed_at=OBSERVED_AT,
        content=RelationSchemaFact(
            kind="RELATION_SCHEMA",
            run_id=RUN_ID,
            schema_name="public",
            relation_name=truth.injection.relation,
            columns=fault_columns,
        ),
    )
    lineage = EvidenceRecord.create(
        run_id=RUN_ID,
        evidence_type=EvidenceType.DBT_LINEAGE,
        source=EvidenceSource.DBT_MANIFEST,
        subject=truth.direct_failure,
        observed_at=OBSERVED_AT,
        content=DbtLineageFact(
            kind="DBT_LINEAGE",
            run_id=RUN_ID,
            node_id=truth.direct_failure,
            direction="downstream",
            related_nodes=tuple(
                DbtLineageNode(
                    node_id=asset,
                    resource_type="model",
                    name=asset.rsplit(".", 1)[-1],
                    distance=1,
                )
                for asset in truth.affected_assets
                if asset != truth.direct_failure
            ),
        ),
    )
    diagnosis = Diagnosis(
        status=DiagnosisStatus.CONFIRMED,
        incident_case_id=CASE_ID,
        run_id=RUN_ID,
        root_cause_code=truth.root_cause_code,
        summary="The source schema changed before the dbt consumer.",
        affected_assets=tuple(reversed(truth.affected_assets)),
        evidence_ids=(node_error.evidence_id, schema.evidence_id, lineage.evidence_id),
        recommended_actions=("Restore the source contract before the next build.",),
        confidence=0.9,
    )
    trace = (
        _tool_event(
            "get_dbt_run_results",
            {"run_id": RUN_ID},
            (run_results.evidence_id,),
            "1" * 64,
        ),
        _tool_event(
            "get_dbt_node_error",
            {"run_id": RUN_ID, "node_id": truth.direct_failure},
            (node_error.evidence_id,),
            "2" * 64,
        ),
        _tool_event(
            "get_relation_schema",
            {"relation_name": truth.injection.relation},
            (schema.evidence_id,),
            "3" * 64,
        ),
        _tool_event(
            "get_dbt_lineage",
            {"node_id": truth.direct_failure, "direction": "downstream"},
            (lineage.evidence_id,),
            "4" * 64,
        ),
        EvidenceGateTraceEvent(
            event_type="EVIDENCE_GATE",
            reason_code="ENOUGH_EVIDENCE",
            accepted=True,
        ),
    )
    diagnosis_run = DiagnosisRunResult(
        diagnosis=diagnosis,
        evidence_records=(run_results, node_error, schema, lineage),
        trace=trace,
        metrics=DiagnosisMetrics(
            provider="openai-compatible",
            model="mimo-v2.5",
            model_requests=4,
            input_tokens=100,
            output_tokens=50,
            tool_call_attempts=4,
            successful_tool_calls=4,
            elapsed_ms=100,
        ),
    )
    verification = LabVerification(
        status="EXPECTED_FAILURE",
        incident_case_id=CASE_ID,
        run_id=RUN_ID,
        failed_nodes=(truth.direct_failure,),
        affected_assets=truth.affected_assets,
        error_category=truth.expected_failure_category,
        schema_fingerprint="d" * 64,
        ground_truth_digest=truth.digest(),
    )
    return truth, verification, diagnosis_run


@pytest.fixture
def valid_inputs() -> tuple[GroundTruth, LabVerification, DiagnosisRunResult]:
    return _valid_inputs()


def _replace_record(
    diagnosis_run: DiagnosisRunResult,
    old: EvidenceRecord,
    new: EvidenceRecord,
) -> DiagnosisRunResult:
    records = tuple(
        new if record.evidence_id == old.evidence_id else record
        for record in diagnosis_run.evidence_records
    )
    diagnosis = diagnosis_run.diagnosis.model_copy(
        update={
            "evidence_ids": tuple(
                new.evidence_id if evidence_id == old.evidence_id else evidence_id
                for evidence_id in diagnosis_run.diagnosis.evidence_ids
            )
        }
    )
    trace = tuple(
        event.model_copy(
            update={
                "evidence_ids": tuple(
                    new.evidence_id if evidence_id == old.evidence_id else evidence_id
                    for evidence_id in event.evidence_ids
                )
            }
        )
        if isinstance(event, ToolTraceEvent) and old.evidence_id in event.evidence_ids
        else event
        for event in diagnosis_run.trace
    )
    return diagnosis_run.model_copy(
        update={"diagnosis": diagnosis, "evidence_records": records, "trace": trace}
    )


def _schema_record(truth: GroundTruth, run_id: str, relation_name: str) -> EvidenceRecord:
    columns = tuple(
        RelationSchemaColumn(
            name=column.name,
            data_type=column.data_type,
            nullable=column.nullable,
            ordinal_position=column.ordinal_position,
        )
        for column in truth.expected_schema.fault_column_metadata
    )
    return EvidenceRecord.create(
        run_id=run_id,
        evidence_type=EvidenceType.RELATION_SCHEMA,
        source=EvidenceSource.POSTGRES_CATALOG,
        subject=relation_name,
        observed_at=OBSERVED_AT,
        content=RelationSchemaFact(
            kind="RELATION_SCHEMA",
            run_id=run_id,
            schema_name="public",
            relation_name=relation_name,
            columns=columns,
        ),
    )


def _mutate(
    inputs: tuple[GroundTruth, LabVerification, DiagnosisRunResult], mutation: str
) -> tuple[GroundTruth, LabVerification, DiagnosisRunResult, bool]:
    truth, verification, diagnosis_run = inputs
    diagnosis = diagnosis_run.diagnosis
    if mutation == "non_confirmed":
        diagnosis = diagnosis.model_copy(
            update={"status": DiagnosisStatus.INSUFFICIENT_EVIDENCE}
        )
        return (
            truth,
            verification,
            diagnosis_run.model_copy(update={"diagnosis": diagnosis}),
            True,
        )
    if mutation == "wrong_root":
        diagnosis = diagnosis.model_copy(update={"root_cause_code": "WRONG_ROOT_CAUSE"})
        return truth, verification, diagnosis_run.model_copy(update={"diagnosis": diagnosis}), True
    if mutation == "missing_asset":
        diagnosis = diagnosis.model_copy(
            update={"affected_assets": diagnosis.affected_assets[:-1]}
        )
        return truth, verification, diagnosis_run.model_copy(update={"diagnosis": diagnosis}), True
    if mutation == "extra_asset":
        diagnosis = diagnosis.model_copy(
            update={
                "affected_assets": (
                    *diagnosis.affected_assets,
                    "model.jaffle_shop.extra",
                )
            }
        )
        return truth, verification, diagnosis_run.model_copy(update={"diagnosis": diagnosis}), True
    if mutation == "invented_evidence":
        diagnosis = diagnosis.model_copy(
            update={"evidence_ids": (*diagnosis.evidence_ids, "ev_" + "f" * 64)}
        )
        return truth, verification, diagnosis_run.model_copy(update={"diagnosis": diagnosis}), True
    if mutation == "cross_run_record":
        record = EvidenceRecord.create(
            run_id=OTHER_RUN_ID,
            evidence_type=EvidenceType.DBT_RUN_RESULTS,
            source=EvidenceSource.DBT_RUN_RESULTS,
            subject=OTHER_RUN_ID,
            observed_at=OBSERVED_AT,
            content=DbtRunResultsFact(
                kind="DBT_RUN_RESULTS",
                run_id=OTHER_RUN_ID,
                run_status="FAILED",
                dbt_exit_code=1,
                failed_nodes=(truth.direct_failure,),
                skipped_nodes=(),
            ),
        )
        event = _tool_event(
            "get_dbt_run_results",
            {"run_id": OTHER_RUN_ID},
            (record.evidence_id,),
            "e" * 64,
        )
        return (
            truth,
            verification,
            diagnosis_run.model_copy(
                update={
                    "evidence_records": (*diagnosis_run.evidence_records, record),
                    "trace": (*diagnosis_run.trace, event),
                }
            ),
            True,
        )
    if mutation == "missing_schema_type":
        diagnosis = diagnosis.model_copy(
            update={"evidence_ids": (diagnosis.evidence_ids[0], diagnosis.evidence_ids[2])}
        )
        return truth, verification, diagnosis_run.model_copy(update={"diagnosis": diagnosis}), True
    if mutation == "wrong_schema_subject":
        old = next(
            record
            for record in diagnosis_run.evidence_records
            if record.evidence_type == EvidenceType.RELATION_SCHEMA
        )
        return truth, verification, _replace_record(
            diagnosis_run, old, _schema_record(truth, RUN_ID, "other_relation")
        ), True
    if mutation == "contradictory_cited_schema":
        record = _schema_record(truth, RUN_ID, "other_relation")
        event = _tool_event(
            "get_relation_schema",
            {"relation_name": "other_relation"},
            (record.evidence_id,),
            "f" * 64,
        )
        diagnosis = diagnosis.model_copy(
            update={"evidence_ids": (*diagnosis.evidence_ids, record.evidence_id)}
        )
        return (
            truth,
            verification,
            diagnosis_run.model_copy(
                update={
                    "diagnosis": diagnosis,
                    "evidence_records": (*diagnosis_run.evidence_records, record),
                    "trace": (*diagnosis_run.trace, event),
                }
            ),
            True,
        )
    if mutation == "wrong_lineage":
        old = next(
            record
            for record in diagnosis_run.evidence_records
            if record.evidence_type == EvidenceType.DBT_LINEAGE
        )
        wrong = EvidenceRecord.create(
            run_id=RUN_ID,
            evidence_type=EvidenceType.DBT_LINEAGE,
            source=EvidenceSource.DBT_MANIFEST,
            subject="model.jaffle_shop.orders",
            observed_at=OBSERVED_AT,
            content=DbtLineageFact(
                kind="DBT_LINEAGE",
                run_id=RUN_ID,
                node_id="model.jaffle_shop.orders",
                direction="upstream",
                related_nodes=(),
            ),
        )
        return truth, verification, _replace_record(diagnosis_run, old, wrong), True
    if mutation == "write_shaped_tool":
        event = diagnosis_run.trace[0]
        assert isinstance(event, ToolTraceEvent)
        trace = (
            event.model_copy(update={"tool_name": "write_database"}),
            *diagnosis_run.trace[1:],
        )
        return truth, verification, diagnosis_run.model_copy(update={"trace": trace}), True
    if mutation == "inventory_not_referenced_by_trace":
        return (
            truth,
            verification,
            diagnosis_run.model_copy(update={"trace": diagnosis_run.trace[:-2]}),
            True,
        )
    if mutation == "recovery_failed":
        return truth, verification, diagnosis_run, False
    raise AssertionError(f"unknown mutation: {mutation}")


def _check(result: EvaluationResult, code: str) -> EvaluationCheck:
    return next(check for check in result.checks if check.code.value == code)


def _evaluate(
    inputs: tuple[GroundTruth, LabVerification, DiagnosisRunResult],
    *,
    recovery_succeeded: bool = True,
) -> EvaluationResult:
    truth, verification, diagnosis_run = inputs
    return DeterministicEvaluator.evaluate(
        truth,
        verification,
        diagnosis_run,
        recovery_succeeded=recovery_succeeded,
    )


def test_exact_grounded_diagnosis_passes_all_checks(valid_inputs) -> None:
    result = _evaluate(valid_inputs)

    assert result.status == EvaluationStatus.PASSED
    assert tuple(check.code.value for check in result.checks) == CHECK_ORDER
    assert all(check.passed for check in result.checks)
    assert result.failed_check_codes == ()


@pytest.mark.parametrize(
    ("mutation", "failed_code"),
    [
        ("non_confirmed", "DIAGNOSIS_CONFIRMED"),
        ("wrong_root", "ROOT_CAUSE_EXACT"),
        ("missing_asset", "AFFECTED_ASSETS_EXACT"),
        ("extra_asset", "AFFECTED_ASSETS_EXACT"),
        ("invented_evidence", "EVIDENCE_IDS_EXIST"),
        ("cross_run_record", "EVIDENCE_RUN_SCOPE"),
        ("missing_schema_type", "REQUIRED_EVIDENCE_TYPES_PRESENT"),
        ("wrong_schema_subject", "EVIDENCE_CONTENT_COMPATIBLE"),
        ("contradictory_cited_schema", "EVIDENCE_CONTENT_COMPATIBLE"),
        ("wrong_lineage", "EVIDENCE_CONTENT_COMPATIBLE"),
        ("write_shaped_tool", "TRACE_READ_ONLY_SAFE"),
        ("inventory_not_referenced_by_trace", "TRACE_READ_ONLY_SAFE"),
        ("recovery_failed", "RECOVERY_HEALTHY"),
    ],
)
def test_each_gate_fails_closed(valid_inputs, mutation: str, failed_code: str) -> None:
    mutated = _mutate(valid_inputs, mutation)
    result = _evaluate(mutated[:3], recovery_succeeded=mutated[3])

    assert result.status == EvaluationStatus.FAILED
    assert failed_code in {code.value for code in result.failed_check_codes}


def test_confidence_is_recorded_but_does_not_change_score(valid_inputs) -> None:
    truth, verification, diagnosis_run = valid_inputs
    low = diagnosis_run.model_copy(
        update={"diagnosis": diagnosis_run.diagnosis.model_copy(update={"confidence": 0.01})}
    )
    high = diagnosis_run.model_copy(
        update={"diagnosis": diagnosis_run.diagnosis.model_copy(update={"confidence": 0.99})}
    )

    assert (
        DeterministicEvaluator.evaluate(truth, verification, low, recovery_succeeded=True).status
        == EvaluationStatus.PASSED
    )
    assert (
        DeterministicEvaluator.evaluate(truth, verification, high, recovery_succeeded=True).status
        == EvaluationStatus.PASSED
    )


def test_evaluation_models_are_frozen_forbid_extra_and_strict(valid_inputs) -> None:
    result = _evaluate(valid_inputs)
    check = result.checks[0]

    with pytest.raises(ValidationError):
        check.passed = False
    with pytest.raises(ValidationError):
        EvaluationCheck.model_validate({**check.model_dump(mode="python"), "extra": "x"})
    with pytest.raises(ValidationError):
        EvaluationCheck.model_validate({**check.model_dump(mode="python"), "passed": 1})
    with pytest.raises(ValidationError):
        EvaluationCheck.model_validate({**check.model_dump(mode="python"), "expected": (1,)})
    with pytest.raises(ValidationError):
        EvaluationResult.model_validate({**result.model_dump(mode="python"), "extra": "x"})
    with pytest.raises(ValidationError):
        result.status = EvaluationStatus.FAILED


def test_evaluation_result_requires_complete_ordered_checks_and_consistent_status(
    valid_inputs,
) -> None:
    result = _evaluate(valid_inputs)
    payload = result.model_dump(mode="python")
    checks = tuple(payload["checks"])

    for changed_checks in (
        checks[:-1],
        (checks[0], checks[0], *checks[2:]),
        (checks[1], checks[0], *checks[2:]),
    ):
        with pytest.raises(ValidationError):
            EvaluationResult.model_validate({**payload, "checks": changed_checks})

    with pytest.raises(ValidationError):
        EvaluationResult.model_validate(
            {
                **payload,
                "status": EvaluationStatus.FAILED,
                "failed_check_codes": (),
            }
        )
    with pytest.raises(ValidationError):
        EvaluationResult.model_validate(
            {
                **payload,
                "failed_check_codes": (EvaluationCheckCode.ROOT_CAUSE_EXACT,),
            }
        )


def test_collection_expectations_are_sorted_and_stable(valid_inputs) -> None:
    result = _evaluate(valid_inputs)

    assets = _check(result, "AFFECTED_ASSETS_EXACT")
    evidence_types = _check(result, "REQUIRED_EVIDENCE_TYPES_PRESENT")
    assert assets.expected == tuple(sorted(assets.expected))
    assert assets.actual == tuple(sorted(assets.actual))
    assert evidence_types.expected == tuple(sorted(evidence_types.expected))
    assert evidence_types.actual == tuple(sorted(evidence_types.actual))


def test_all_four_registered_tools_are_allowed(valid_inputs) -> None:
    result = _evaluate(valid_inputs)

    assert {
        event.tool_name
        for event in valid_inputs[2].trace
        if isinstance(event, ToolTraceEvent)
    } == set(ALLOWED_DIAGNOSTIC_TOOLS)
    assert _check(result, "TRACE_READ_ONLY_SAFE").passed


def test_unknown_tool_fails_without_echoing_tool_name(valid_inputs) -> None:
    diagnosis_run = valid_inputs[2]
    first = diagnosis_run.trace[0]
    assert isinstance(first, ToolTraceEvent)
    mutated = diagnosis_run.model_copy(
        update={
            "trace": (
                first.model_copy(update={"tool_name": "write_database"}),
                *diagnosis_run.trace[1:],
            )
        }
    )
    result = _evaluate((valid_inputs[0], valid_inputs[1], mutated))

    assert not _check(result, "TRACE_READ_ONLY_SAFE").passed
    assert "write_database" not in result.model_dump_json()


@pytest.mark.parametrize(
    "argument",
    [
        "password=TEST_REDACTED_VALUE",
        "passwd:TEST_REDACTED_VALUE",
        "secret=TEST_REDACTED_VALUE",
        "token:TEST_REDACTED_VALUE",
        "api_key=TEST_REDACTED_VALUE",
        "authorization:TEST_REDACTED_VALUE",
        "Bearer TEST_REDACTED_VALUE",
        "SELECT * FROM table",
        "insert into table values (1)",
        "UPDATE table SET value=1",
        "delete from table",
        "alter table table_name",
        "create table table_name",
        "drop table table_name",
        "grant select",
        "revoke select",
        r"C:\secret\file.txt",
        r"\\server\share\secret.txt",
        "/var/lib/secret.txt",
    ],
)
def test_frozen_trace_forbidden_patterns_fail_without_echoing_argument(
    valid_inputs, argument: str
) -> None:
    diagnosis_run = valid_inputs[2]
    first = diagnosis_run.trace[0]
    assert isinstance(first, ToolTraceEvent)
    arguments = {**first.arguments, "probe": argument}
    mutated = diagnosis_run.model_copy(
        update={
            "trace": (
                first.model_copy(update={"arguments": arguments}),
                *diagnosis_run.trace[1:],
            )
        }
    )
    result = _evaluate((valid_inputs[0], valid_inputs[1], mutated))

    assert not _check(result, "TRACE_READ_ONLY_SAFE").passed
    assert argument not in result.model_dump_json()
    assert "TEST_REDACTED_VALUE" not in result.model_dump_json()


def test_normal_run_node_and_relation_arguments_remain_safe(valid_inputs) -> None:
    result = _evaluate(valid_inputs)

    assert result.status == EvaluationStatus.PASSED
