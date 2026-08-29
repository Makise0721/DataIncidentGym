from __future__ import annotations

import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta, timezone
from importlib import resources
from pathlib import Path

import pytest
from jinja2 import Environment, StrictUndefined, UndefinedError
from pydantic import ValidationError

from data_incident_gym.artifacts import (
    ARTIFACT_FILENAMES,
    ArtifactRun,
    ArtifactWriteError,
    ArtifactWriter,
    Diagnosis,
    EvidenceArtifact,
    RecoveryStatus,
    RunMetadata,
    TraceEnvelope,
)
from data_incident_gym.diagnosis import (
    DiagnosisMetrics,
    DiagnosisRunResult,
    DiagnosisStatus,
    EvidenceGateTraceEvent,
    ToolTraceEvent,
)
from data_incident_gym.diagnostic_kernel import (
    ClaimEvidence,
    ClaimKind,
    EvidenceGap,
    EvidenceGapKind,
    EvidenceGapStatus,
    Hypothesis,
    HypothesisAssessment,
    HypothesisVerdict,
    InvestigationState,
    KernelFinalStatus,
    KernelStateTraceEvent,
)
from data_incident_gym.evaluation import (
    DeterministicEvaluator,
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
from data_incident_gym.incidents import CASE_ID, load_ground_truth
from data_incident_gym.lab_verifier import LabVerification

EXPECTED_FILES = set(ARTIFACT_FILENAMES)
RUN_ID = "a" * 32
OBSERVED_AT = datetime(2026, 8, 28, 0, 0, tzinfo=UTC)
STARTED_AT = datetime(2026, 8, 28, 1, 0, tzinfo=UTC)
FINISHED_AT = STARTED_AT + timedelta(milliseconds=125)
MODEL_BASE_URL = "http://127.0.0.1:11434/v1"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def lines(output: Path) -> list[str]:
    result = (output / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    assert result
    return result


def fake_clean_git(revision: str):
    def run(
        argv: list[str],
        *,
        capture_output: bool,
        text: bool,
        check: bool,
        timeout: int,
    ) -> subprocess.CompletedProcess[str]:
        assert capture_output is True
        assert text is True
        assert check is True
        assert timeout == 5
        if argv[-2:] == ["rev-parse", "HEAD"]:
            stdout = revision + "\n"
        elif argv[-1:] == ["--porcelain"]:
            stdout = ""
        else:
            raise AssertionError(f"unexpected git argv: {argv}")
        return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr="")

    return run


def _recorded_event(
    tool_name: str,
    arguments: dict[str, str],
    evidence_ids: tuple[str, ...],
    fingerprint: str,
    elapsed_ms: int,
    error_code: str | None = None,
) -> ToolTraceEvent:
    return ToolTraceEvent(
        event_type="TOOL_CALL",
        tool_name=tool_name,
        arguments=arguments,
        fingerprint=fingerprint,
        evidence_ids=evidence_ids,
        error_code=error_code,
        elapsed_ms=elapsed_ms,
    )


def _kernel_state(
    run_id: str,
    truth,
    diagnosis: Diagnosis,
    records: tuple[EvidenceRecord, ...],
) -> InvestigationState:
    run_results, node_error, schema, lineage = records
    selected_id = "h_selected"
    alternative_id = "h_alternative"
    alternative_code = next(
        code
        for code in (
            "SOURCE_SCHEMA_COLUMN_RENAMED",
            "SOURCE_SCHEMA_COLUMN_TYPE_CHANGED",
        )
        if code != truth.root_cause_code
    )
    return InvestigationState(
        schema_version="m6.investigation.v1",
        incident_case_id=CASE_ID,
        run_id=run_id,
        revision=8,
        allowed_root_cause_codes=(
            "SOURCE_SCHEMA_COLUMN_RENAMED",
            "SOURCE_SCHEMA_COLUMN_TYPE_CHANGED",
        ),
        hypotheses=(
            Hypothesis(hypothesis_id=selected_id, root_cause_code=truth.root_cause_code),
            Hypothesis(hypothesis_id=alternative_id, root_cause_code=alternative_code),
        ),
        gaps=(
            EvidenceGap(
                gap_id="g_locate",
                gap_kind=EvidenceGapKind.LOCATE_FAILURE,
                hypothesis_ids=(selected_id, alternative_id),
                tool_name="get_dbt_run_results",
                status=EvidenceGapStatus.CLOSED,
                evidence_ids=(run_results.evidence_id,),
            ),
            EvidenceGap(
                gap_id="g_explain",
                gap_kind=EvidenceGapKind.EXPLAIN_FAILURE,
                hypothesis_ids=(selected_id, alternative_id),
                tool_name="get_dbt_node_error",
                status=EvidenceGapStatus.CLOSED,
                evidence_ids=(node_error.evidence_id,),
            ),
            EvidenceGap(
                gap_id="g_schema",
                gap_kind=EvidenceGapKind.DISCRIMINATE_SCHEMA,
                hypothesis_ids=(selected_id, alternative_id),
                tool_name="get_relation_schema",
                status=EvidenceGapStatus.CLOSED,
                evidence_ids=(schema.evidence_id,),
            ),
            EvidenceGap(
                gap_id="g_impact",
                gap_kind=EvidenceGapKind.MAP_IMPACT,
                hypothesis_ids=(selected_id, alternative_id),
                tool_name="get_dbt_lineage",
                status=EvidenceGapStatus.CLOSED,
                evidence_ids=(lineage.evidence_id,),
            ),
        ),
        assessments=(
            HypothesisAssessment(
                hypothesis_id=selected_id,
                verdict=HypothesisVerdict.SUPPORTED,
                evidence_ids=(node_error.evidence_id, schema.evidence_id),
            ),
            HypothesisAssessment(
                hypothesis_id=alternative_id,
                verdict=HypothesisVerdict.REFUTED,
                evidence_ids=(schema.evidence_id,),
            ),
        ),
        claims=(
            ClaimEvidence(
                kind=ClaimKind.ROOT_CAUSE,
                value=truth.root_cause_code,
                evidence_ids=(node_error.evidence_id, schema.evidence_id),
            ),
            *(
                ClaimEvidence(
                    kind=ClaimKind.AFFECTED_ASSET,
                    value=asset,
                    evidence_ids=(
                        node_error.evidence_id
                        if asset == truth.direct_failure
                        else lineage.evidence_id,
                    ),
                )
                for asset in diagnosis.affected_assets
            ),
        ),
        evidence_inventory=tuple(record.evidence_id for record in records),
        tool_fingerprints=("1" * 64, "2" * 64, "3" * 64, "4" * 64),
        model_request_limit=8,
        model_requests_used=4,
        model_requests_remaining=4,
        tool_call_limit=8,
        tool_calls_used=4,
        tool_calls_remaining=4,
        final_status=KernelFinalStatus.CONFIRMED,
        gate_reason="CONFIRMED",
        selected_hypothesis_id=selected_id,
    )


def _artifact_run(
    run_id: str = RUN_ID,
    *,
    recovery_status: RecoveryStatus = RecoveryStatus.HEALTHY,
    summary: str = "The synthetic source contract changed.",
    actions: tuple[str, ...] = ("Restore the source contract.",),
) -> ArtifactRun:
    truth = load_ground_truth(CASE_ID)
    columns = tuple(
        RelationSchemaColumn(
            name=column.name,
            data_type=column.data_type,
            nullable=column.nullable,
            ordinal_position=column.ordinal_position,
        )
        for column in truth.expected_schema.fault_column_metadata
    )
    run_results = EvidenceRecord.create(
        run_id=run_id,
        evidence_type=EvidenceType.DBT_RUN_RESULTS,
        source=EvidenceSource.DBT_RUN_RESULTS,
        subject=run_id,
        observed_at=OBSERVED_AT,
        content=DbtRunResultsFact(
            kind="DBT_RUN_RESULTS",
            run_id=run_id,
            run_status="FAILED",
            dbt_exit_code=1,
            failed_nodes=(truth.direct_failure,),
            skipped_nodes=(),
        ),
    )
    node_error = EvidenceRecord.create(
        run_id=run_id,
        evidence_type=EvidenceType.DBT_NODE_ERROR,
        source=EvidenceSource.DBT_RUN_RESULTS,
        subject=truth.direct_failure,
        observed_at=OBSERVED_AT,
        content=DbtNodeErrorFact(
            kind="DBT_NODE_ERROR",
            run_id=run_id,
            node_id=truth.direct_failure,
            resource_type="model",
            status="error",
            message="column amount does not exist",
        ),
    )
    schema = EvidenceRecord.create(
        run_id=run_id,
        evidence_type=EvidenceType.RELATION_SCHEMA,
        source=EvidenceSource.POSTGRES_CATALOG,
        subject=truth.injection.relation,
        observed_at=OBSERVED_AT,
        content=RelationSchemaFact(
            kind="RELATION_SCHEMA",
            run_id=run_id,
            schema_name="public",
            relation_name=truth.injection.relation,
            columns=columns,
        ),
    )
    lineage = EvidenceRecord.create(
        run_id=run_id,
        evidence_type=EvidenceType.DBT_LINEAGE,
        source=EvidenceSource.DBT_MANIFEST,
        subject=truth.direct_failure,
        observed_at=OBSERVED_AT,
        content=DbtLineageFact(
            kind="DBT_LINEAGE",
            run_id=run_id,
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
        run_id=run_id,
        root_cause_code=truth.root_cause_code,
        summary=summary,
        affected_assets=truth.affected_assets,
        evidence_ids=(node_error.evidence_id, schema.evidence_id, lineage.evidence_id),
        recommended_actions=actions,
        confidence=0.8,
    )
    trace = (
        _recorded_event(
            "get_dbt_run_results",
            {"run_id": run_id},
            (run_results.evidence_id,),
            "1" * 64,
            4,
        ),
        _recorded_event(
            "get_dbt_node_error",
            {"run_id": run_id, "node_id": truth.direct_failure},
            (node_error.evidence_id,),
            "2" * 64,
            12,
            "EVIDENCE_TOOL_ERROR",
        ),
        _recorded_event(
            "get_relation_schema",
            {"relation_name": truth.injection.relation},
            (schema.evidence_id,),
            "3" * 64,
            7,
        ),
        _recorded_event(
            "get_dbt_lineage",
            {"node_id": truth.direct_failure, "direction": "downstream"},
            (lineage.evidence_id,),
            "4" * 64,
            9,
        ),
        EvidenceGateTraceEvent(
            event_type="EVIDENCE_GATE",
            reason_code="CONFIRMED",
            accepted=True,
        ),
    )
    records = (run_results, node_error, schema, lineage)
    state = _kernel_state(run_id, truth, diagnosis, records)
    diagnosis_run = DiagnosisRunResult(
        diagnosis=diagnosis,
        evidence_records=records,
        trace=(*trace, KernelStateTraceEvent(event_type="KERNEL_STATE", state=state)),
        investigation_state=state,
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
        run_id=run_id,
        failed_nodes=(truth.direct_failure,),
        affected_assets=truth.affected_assets,
        error_category=truth.expected_failure_category,
        schema_fingerprint="d" * 64,
        ground_truth_digest=truth.digest(),
    )
    evaluation = DeterministicEvaluator.evaluate(
        truth,
        verification,
        diagnosis_run,
        recovery_succeeded=recovery_status == RecoveryStatus.HEALTHY,
    )
    return ArtifactRun(
        incident_case_id=CASE_ID,
        run_id=run_id,
        started_at=STARTED_AT,
        finished_at=FINISHED_AT,
        recovery_status=recovery_status,
        model_base_url=MODEL_BASE_URL,
        diagnosis_run=diagnosis_run,
        evaluation=evaluation,
    )


@pytest.fixture
def artifact_run() -> ArtifactRun:
    return _artifact_run()


@pytest.fixture
def failed_artifact_run() -> ArtifactRun:
    return _artifact_run(recovery_status=RecoveryStatus.FAILED)


def test_writer_publishes_exactly_six_round_trippable_files(
    tmp_path: Path, artifact_run: ArtifactRun
) -> None:
    writer = ArtifactWriter(tmp_path, run_command=fake_clean_git("1" * 40))

    output = writer.write(artifact_run)

    assert output == tmp_path / "artifacts" / artifact_run.run_id
    assert {path.name for path in output.iterdir()} == EXPECTED_FILES
    assert len(tuple(output.iterdir())) == len(ARTIFACT_FILENAMES)
    assert RunMetadata.model_validate_json(read(output / "metadata.json")).run_id == (
        artifact_run.run_id
    )
    assert EvidenceArtifact.model_validate_json(read(output / "evidence.json")).records == (
        artifact_run.diagnosis_run.evidence_records
    )
    assert Diagnosis.model_validate_json(read(output / "diagnosis.json")) == (
        artifact_run.diagnosis_run.diagnosis
    )
    assert EvaluationResult.model_validate_json(read(output / "evaluation.json")) == (
        artifact_run.evaluation
    )
    assert [TraceEnvelope.model_validate_json(line).sequence for line in lines(output)] == list(
        range(1, len(artifact_run.diagnosis_run.trace) + 1)
    )
    assert TraceEnvelope.model_validate_json(lines(output)[-1]).event.state == (
        artifact_run.diagnosis_run.investigation_state
    )


def test_failed_evaluation_is_persisted_without_being_filtered(
    tmp_path: Path, failed_artifact_run: ArtifactRun
) -> None:
    output = ArtifactWriter(
        tmp_path, run_command=fake_clean_git("2" * 40)
    ).write(failed_artifact_run)

    stored = EvaluationResult.model_validate_json(read(output / "evaluation.json"))
    assert stored.status == EvaluationStatus.FAILED
    assert {path.name for path in output.iterdir()} == EXPECTED_FILES


def test_trace_jsonl_preserves_order_duration_errors_and_evidence_references(
    tmp_path: Path, artifact_run: ArtifactRun
) -> None:
    output = ArtifactWriter(tmp_path, run_command=fake_clean_git("1" * 40)).write(
        artifact_run
    )
    stored = [TraceEnvelope.model_validate_json(line) for line in lines(output)]

    assert [item.sequence for item in stored] == [1, 2, 3, 4, 5, 6]
    tool_events = [item.event for item in stored if item.event.event_type == "TOOL_CALL"]
    assert [event.elapsed_ms for event in tool_events] == [4, 12, 7, 9]
    assert tool_events[1].error_code == "EVIDENCE_TOOL_ERROR"
    assert [event.evidence_ids for event in tool_events] == [
        (artifact_run.diagnosis_run.evidence_records[0].evidence_id,),
        (artifact_run.diagnosis_run.evidence_records[1].evidence_id,),
        (artifact_run.diagnosis_run.evidence_records[2].evidence_id,),
        (artifact_run.diagnosis_run.evidence_records[3].evidence_id,),
    ]


def test_metadata_contains_revision_dirty_flag_safe_config_prompt_hash_and_metrics(
    tmp_path: Path, artifact_run: ArtifactRun
) -> None:
    output = ArtifactWriter(tmp_path, run_command=fake_clean_git("1" * 40)).write(
        artifact_run
    )
    metadata = RunMetadata.model_validate_json(read(output / "metadata.json"))

    assert metadata.code_revision == "1" * 40
    assert metadata.workspace_dirty is False
    assert metadata.provider == "openai-compatible"
    assert metadata.model == "mimo-v2.5"
    assert metadata.model_base_url == MODEL_BASE_URL
    assert metadata.prompt_version == "m6.diagnosis.v1"
    assert len(metadata.prompt_sha256) == 64
    assert metadata.diagnosis_metrics == artifact_run.diagnosis_run.metrics
    assert metadata.budget.model_request_limit == 8
    assert metadata.budget.tool_call_limit == 8
    assert metadata.budget.output_retry_limit == 2
    assert metadata.budget.timeout_seconds == 300


def test_report_is_deterministic_chinese_and_contains_every_failed_check(
    tmp_path: Path, failed_artifact_run: ArtifactRun
) -> None:
    output = ArtifactWriter(tmp_path, run_command=fake_clean_git("2" * 40)).write(
        failed_artifact_run
    )
    report = read(output / "report.md")

    assert "DataIncidentGym" in report
    assert "评测" in report
    assert "RECOVERY_HEALTHY_FAILED" in report
    for hypothesis in failed_artifact_run.diagnosis_run.investigation_state.hypotheses:
        assert hypothesis.root_cause_code in report
    for gap in failed_artifact_run.diagnosis_run.investigation_state.gaps:
        assert gap.gap_id in report
    for claim in failed_artifact_run.diagnosis_run.investigation_state.claims:
        assert claim.value in report
    assert "模型请求：4 / 8，剩余 4" in report
    assert "工具调用：4 / 8，剩余 4" in report
    assert "最终状态：CONFIRMED" in report
    assert "最终门禁原因：CONFIRMED" in report
    assert report.endswith("\n")
    assert not report.endswith("\n\n")
    assert all(check.reason_code in report for check in failed_artifact_run.evaluation.checks)


def test_writer_refuses_existing_run_directory_instead_of_overwriting(
    tmp_path: Path, artifact_run: ArtifactRun
) -> None:
    writer = ArtifactWriter(tmp_path, run_command=fake_clean_git("1" * 40))
    output = writer.write(artifact_run)
    before = {path.name: path.read_bytes() for path in output.iterdir()}

    with pytest.raises(ArtifactWriteError):
        writer.write(artifact_run)

    assert {path.name: path.read_bytes() for path in output.iterdir()} == before


def test_writer_rejects_artifact_root_or_target_symlink(
    tmp_path: Path, artifact_run: ArtifactRun
) -> None:
    artifact_root = tmp_path / "artifacts"
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    try:
        artifact_root.symlink_to(elsewhere, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"symlink unavailable: {error}")

    with pytest.raises(ArtifactWriteError):
        ArtifactWriter(tmp_path, run_command=fake_clean_git("1" * 40)).write(artifact_run)

    artifact_root.unlink()
    artifact_root.mkdir()
    target = artifact_root / artifact_run.run_id
    target.symlink_to(elsewhere, target_is_directory=True)
    with pytest.raises(ArtifactWriteError):
        ArtifactWriter(tmp_path, run_command=fake_clean_git("1" * 40)).write(artifact_run)


def test_validation_failure_never_publishes_partial_final_directory(
    tmp_path: Path, artifact_run: ArtifactRun
) -> None:
    class InvalidWriter(ArtifactWriter):
        def _build_payloads(self, run: ArtifactRun) -> dict[str, str]:
            payloads = super()._build_payloads(run)
            payloads["diagnosis.json"] = "{}"
            return payloads

    with pytest.raises(ArtifactWriteError):
        InvalidWriter(tmp_path, run_command=fake_clean_git("1" * 40)).write(artifact_run)

    assert not (tmp_path / "artifacts" / artifact_run.run_id).exists()
    assert not list((tmp_path / "artifacts").glob(f".{artifact_run.run_id}.*.tmp"))


def test_concurrent_different_run_ids_publish_independently(tmp_path: Path) -> None:
    runs = (_artifact_run("a" * 32), _artifact_run("b" * 32))

    def publish(run: ArtifactRun) -> Path:
        return ArtifactWriter(tmp_path, run_command=fake_clean_git("1" * 40)).write(run)

    with ThreadPoolExecutor(max_workers=2) as executor:
        outputs = list(executor.map(publish, runs))

    assert {output.name for output in outputs} == {"a" * 32, "b" * 32}
    assert all({path.name for path in output.iterdir()} == EXPECTED_FILES for output in outputs)


def test_competing_same_run_id_has_at_most_one_success_and_keeps_one_valid_bundle(
    tmp_path: Path, artifact_run: ArtifactRun
) -> None:
    def publish() -> tuple[str, Path | None]:
        try:
            return "success", ArtifactWriter(
                tmp_path, run_command=fake_clean_git("1" * 40)
            ).write(artifact_run)
        except ArtifactWriteError:
            return "failed", None

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: publish(), range(2)))

    assert [status for status, _ in results].count("success") == 1
    output = tmp_path / "artifacts" / artifact_run.run_id
    assert {path.name for path in output.iterdir()} == EXPECTED_FILES
    assert EvaluationResult.model_validate_json(read(output / "evaluation.json")).status == (
        EvaluationStatus.PASSED
    )


def test_pre_redacted_trace_and_error_sentinels_never_reappear_in_non_diagnosis_surfaces(
    tmp_path: Path, artifact_run: ArtifactRun
) -> None:
    def sensitive_git(
        argv: list[str],
        *,
        capture_output: bool,
        text: bool,
        check: bool,
        timeout: int,
    ) -> subprocess.CompletedProcess[str]:
        assert argv[-2:] == ["rev-parse", "HEAD"] or argv[-1:] == ["--porcelain"]
        stdout = (
            "1" * 40
            if argv[-2:] == ["rev-parse", "HEAD"]
            else "password=TEST_REDACTED_VALUE C:\\secret\\probe.txt"
        )
        return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr="")

    unsafe_metrics = artifact_run.diagnosis_run.metrics.model_copy(
        update={
            "provider": "TEST_REDACTED_VALUE",
            "model": "TEST_REDACTED_VALUE",
        }
    )
    unsafe_run = artifact_run.model_copy(
        update={
            "diagnosis_run": artifact_run.diagnosis_run.model_copy(
                update={"metrics": unsafe_metrics}
            )
        }
    )
    output = ArtifactWriter(tmp_path, run_command=sensitive_git).write(unsafe_run)

    for filename in ("metadata.json", "trace.jsonl", "evaluation.json", "report.md"):
        assert "TEST_REDACTED_VALUE" not in read(output / filename)
        assert "C:\\secret\\probe.txt" not in read(output / filename)
    metadata = RunMetadata.model_validate_json(read(output / "metadata.json"))
    assert metadata.provider == "[redacted]"
    assert metadata.model == "[redacted]"
    assert metadata.diagnosis_metrics.provider == "[redacted]"
    assert metadata.diagnosis_metrics.model == "[redacted]"


def test_artifact_timestamps_require_utc(tmp_path: Path, artifact_run: ArtifactRun) -> None:
    payload = artifact_run.model_dump(mode="python")
    payload["started_at"] = datetime(2026, 8, 28, 9, 0, tzinfo=timezone(timedelta(hours=8)))

    with pytest.raises(ValidationError):
        ArtifactRun.model_validate(payload)


def test_report_escapes_diagnosis_text_as_inert_preformatted_content(tmp_path: Path) -> None:
    text = '<script>alert("x")</script> [link](https://example.test) a|b'
    run = _artifact_run(summary=text, actions=(text,))
    output = ArtifactWriter(tmp_path, run_command=fake_clean_git("1" * 40)).write(run)
    report = read(output / "report.md")

    assert "<script>" not in report
    assert "&lt;script&gt;" in report
    assert '<pre class="diagnosis-summary">' in report
    assert '<pre class="recommended-actions">' in report
    assert "https://example.test" in report
    summary = report.split('<pre class="diagnosis-summary">', 1)[1].split(
        "</pre>", 1
    )[0]
    assert "a|b" in summary
    assert "<a " not in summary
    assert json.loads(read(output / "diagnosis.json"))["summary"] == text


def test_template_uses_strict_undefined_and_is_available_through_importlib_resources() -> None:
    resource = resources.files("data_incident_gym").joinpath("templates", "report.md.j2")
    source = resource.read_text(encoding="utf-8")
    template = Environment(undefined=StrictUndefined, autoescape=True).from_string(source)

    assert resource.is_file()
    assert "<pre" in source
    with pytest.raises(UndefinedError):
        template.render()
