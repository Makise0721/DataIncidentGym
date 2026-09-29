import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from data_incident_gym.artifacts import (
    ArtifactRun,
    ArtifactWriter,
    BudgetSummary,
    RecoveryStatus,
)
from data_incident_gym.benchmark_report import BenchmarkReporter
from data_incident_gym.benchmark_runner import BenchmarkDoctorReceipt, BenchmarkLedgerEntry
from data_incident_gym.diagnosis import (
    Diagnosis,
    DiagnosisMetrics,
    DiagnosisRunResult,
    DiagnosisRunResultV2,
    DiagnosisRunResultV3,
    DiagnosisStatus,
    DiagnosisTerminalTraceEvent,
    DiagnosticStrategy,
    EvidenceGateTraceEvent,
    KernelStateTraceEvent,
    ModelProtocolTraceEvent,
    PlanTraceEvent,
    ToolTraceEvent,
)
from data_incident_gym.diagnostic_agent import P1_ROOT_CAUSE_CODES, policy_identity_for_strategy
from data_incident_gym.diagnostic_kernel import DiagnosticKernel
from data_incident_gym.doctor import DoctorCheckCode, DoctorResult, DoctorRunner, DoctorStatus
from data_incident_gym.evaluation import (
    DeterministicEvaluator,
    EvaluationApplicability,
    EvaluationCheck,
    EvaluationCheckCode,
    EvaluationResult,
    EvaluationStatus,
)
from data_incident_gym.evaluation_inputs import (
    RecoveryProof,
    build_evaluation_input_bundle,
    default_evaluator_identity,
    write_evaluation_input_bundle,
)
from data_incident_gym.lab_verifier import ScenarioVerification, ScenarioVerificationStatus
from data_incident_gym.planner_comparison_manifest import build_experiment_manifest
from data_incident_gym.planner_comparison_report import (
    PlannerComparisonReporter,
    PlannerComparisonReportError,
    _retry_observations,
)
from data_incident_gym.planner_probe_receipt import (
    create_planner_probe_receipt,
    write_planner_probe_receipt,
)
from data_incident_gym.scenarios import load_scenario_spec

PROJECT_ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 9, 29, tzinfo=UTC)


def _passing_not_applicable_evaluation(case_id: str, run_id: str) -> EvaluationResult:
    checks = tuple(
        EvaluationCheck(
            code=code,
            applicability=EvaluationApplicability.NOT_APPLICABLE,
            passed=True,
            expected=("NOT_APPLICABLE",),
            actual=("NOT_APPLICABLE",),
            reason_code="NOT_APPLICABLE",
        )
        for code in EvaluationCheckCode
    )
    return EvaluationResult(
        incident_case_id=case_id,
        run_id=run_id,
        status=EvaluationStatus.PASSED,
        checks=checks,
        failed_check_codes=(),
        answerability="UNAVAILABLE",
        expected_status="UNAVAILABLE",
    )


def _write_synthetic_gate_error_artifact(tmp_path: Path, manifest, cell):
    revision = "b" * 40

    def run_command(command, **_kwargs):
        stdout = revision if command[-2:] == ["rev-parse", "HEAD"] else ""
        return SimpleNamespace(stdout=stdout)

    diagnosis = Diagnosis(
        status=DiagnosisStatus.MODEL_ERROR,
        run_id=cell.run_id,
        summary="MODEL_RUNTIME_ERROR",
        confidence=0.0,
    )
    trace = (
        EvidenceGateTraceEvent(
            event_type="EVIDENCE_GATE",
            reason_code="GATE_INTERNAL_ERROR",
            accepted=False,
        ),
        DiagnosisTerminalTraceEvent(
            event_type="DIAGNOSIS_TERMINAL",
            strategy=cell.strategy,
            status=diagnosis.status,
            evidence_inventory=(),
        ),
    )
    diagnosis_run = DiagnosisRunResult(
        strategy=cell.strategy,
        policy_identity=policy_identity_for_strategy(cell.strategy),
        diagnosis=diagnosis,
        evidence_records=(),
        trace=trace,
        metrics=DiagnosisMetrics(
            provider="synthetic",
            model="synthetic",
            model_requests=0,
            input_tokens=0,
            output_tokens=0,
            tool_call_attempts=0,
            successful_tool_calls=0,
            elapsed_ms=0,
        ),
    )
    run = ArtifactRun(
        incident_case_id=cell.incident_case_id,
        run_id=cell.run_id,
        started_at=NOW,
        finished_at=NOW,
        recovery_status=RecoveryStatus.HEALTHY,
        model_base_url=manifest.model_configuration.base_url,
        benchmark_manifest_sha256=manifest.digest(),
        diagnosis_run=diagnosis_run,
        evaluation=_passing_not_applicable_evaluation(cell.incident_case_id, cell.run_id),
    )
    artifact_dir = ArtifactWriter(tmp_path, run_command=run_command).write(run)
    scenario = load_scenario_spec(cell.incident_case_id, PROJECT_ROOT)
    verification = ScenarioVerification(
        status=ScenarioVerificationStatus.EXPECTED_FAILURE,
        incident_case_id=cell.incident_case_id,
        run_id=cell.run_id,
        dbt_exit_code=1,
        failed_nodes=(),
        skipped_nodes=(),
        affected_assets=tuple(sorted(scenario.affected_assets)),
        schema_fingerprint="c" * 64,
        profile_spec_sha256="d" * 64,
    )
    scoring_inputs = build_evaluation_input_bundle(
        scenario=scenario,
        verification=verification,
        diagnosis_run=diagnosis_run,
        recovery=RecoveryProof(
            source="LAB_RESTORE",
            incident_case_id=cell.incident_case_id,
            state="HEALTHY",
        ),
        artifact_dir=artifact_dir,
        budget=BudgetSummary(
            model_request_limit=8,
            tool_call_limit=8,
            output_retry_limit=2,
            timeout_seconds=300,
        ),
        evaluator=default_evaluator_identity(),
    )
    write_evaluation_input_bundle(tmp_path, scoring_inputs, created_at=NOW)


def _write_complete_synthetic_suite(tmp_path: Path, manifest) -> Path:
    checkout_revision = "b" * 40
    scenario_dir = tmp_path / "config" / "scenarios"
    scenario_dir.mkdir(parents=True)
    scenarios = {}
    for case_id in manifest.formal_scenario_ids:
        source = PROJECT_ROOT / "config" / "scenarios" / f"{case_id}.json"
        shutil.copyfile(source, scenario_dir / source.name)
        scenarios[case_id] = load_scenario_spec(case_id, tmp_path)

    def run_command(command, **_kwargs):
        stdout = checkout_revision if command[-2:] == ["rev-parse", "HEAD"] else ""
        return SimpleNamespace(stdout=stdout)

    artifact_writer = ArtifactWriter(tmp_path, run_command=run_command)
    budget = BudgetSummary(
        model_request_limit=8,
        tool_call_limit=8,
        output_retry_limit=2,
        timeout_seconds=300,
    )
    evaluator_identity = default_evaluator_identity()
    recovery_proofs = {
        case_id: RecoveryProof(
            source="LAB_RESTORE",
            incident_case_id=case_id,
            state="HEALTHY",
        )
        for case_id in manifest.formal_scenario_ids
    }
    ledger: list[str] = []
    for cell in manifest.cells:
        scenario = scenarios[cell.incident_case_id]
        if scenario.answerability.value == "NO_INCIDENT":
            verification_status = ScenarioVerificationStatus.HEALTHY_CONTROL
            exit_code = 0
            failed_nodes: tuple[str, ...] = ()
        elif scenario.direct_failure is None:
            verification_status = ScenarioVerificationStatus.EXPECTED_ANOMALY
            exit_code = 0
            failed_nodes = ()
        else:
            verification_status = ScenarioVerificationStatus.EXPECTED_FAILURE
            exit_code = 1
            failed_nodes = (scenario.direct_failure,)
        verification = ScenarioVerification(
            status=verification_status,
            incident_case_id=cell.incident_case_id,
            run_id=cell.run_id,
            dbt_exit_code=exit_code,
            failed_nodes=failed_nodes,
            skipped_nodes=(),
            affected_assets=tuple(sorted(scenario.affected_assets)),
            schema_fingerprint="c" * 64,
            profile_spec_sha256="d" * 64,
        )
        diagnosis = Diagnosis(
            status=DiagnosisStatus.MODEL_ERROR,
            run_id=cell.run_id,
            summary="MODEL_RUNTIME_ERROR",
            confidence=0.0,
        )
        policy = next(item for item in manifest.policies if item.strategy is cell.strategy)
        trace = []
        kernel_state = None
        if cell.strategy is DiagnosticStrategy.EVIDENCE_PLANNER:
            trace.append(PlanTraceEvent(kind="STATE"))
        if cell.strategy is DiagnosticStrategy.DIAGNOSTIC_KERNEL:
            kernel = DiagnosticKernel.start(
                run_id=cell.run_id,
                allowed_root_cause_codes=P1_ROOT_CAUSE_CODES,
                model_request_limit=8,
                tool_call_limit=8,
            )
            kernel.terminate_model_error("MODEL_RUNTIME_ERROR")
            kernel_state = kernel.snapshot(model_requests_used=0)
            trace.append(KernelStateTraceEvent(event_type="KERNEL_STATE", state=kernel_state))
        trace.append(
            DiagnosisTerminalTraceEvent(
                event_type="DIAGNOSIS_TERMINAL",
                strategy=cell.strategy,
                status=diagnosis.status,
                evidence_inventory=(),
            )
        )
        identity = policy.policy_identity
        metrics = DiagnosisMetrics(
            provider=manifest.model_configuration.provider,
            model=manifest.model_configuration.model,
            model_requests=0,
            input_tokens=0,
            output_tokens=0,
            tool_call_attempts=0,
            successful_tool_calls=0,
            elapsed_ms=1000,
        )
        run_model = (
            DiagnosisRunResultV3
            if identity.controller_protocol_version == "p1.controller.v22"
            else DiagnosisRunResultV2
            if identity.controller_protocol_version == "p1.controller.v21"
            else DiagnosisRunResult
        )
        diagnosis_run = run_model(
            strategy=cell.strategy,
            policy_identity=identity,
            diagnosis=diagnosis,
            evidence_records=(),
            trace=tuple(trace),
            metrics=metrics,
            kernel_state=kernel_state,
        )
        evaluation = DeterministicEvaluator.evaluate(
            scenario,
            verification,
            diagnosis_run,
            recovery_succeeded=True,
        )
        started_at = NOW + timedelta(seconds=cell.sequence)
        artifact_dir = artifact_writer.write(
            ArtifactRun(
                incident_case_id=cell.incident_case_id,
                run_id=cell.run_id,
                started_at=started_at,
                finished_at=started_at + timedelta(seconds=1),
                recovery_status=RecoveryStatus.HEALTHY,
                model_base_url=manifest.model_configuration.base_url,
                benchmark_manifest_sha256=manifest.digest(),
                diagnosis_run=diagnosis_run,
                evaluation=evaluation,
            )
        )
        scoring_inputs = build_evaluation_input_bundle(
            scenario=scenario,
            verification=verification,
            diagnosis_run=diagnosis_run,
            recovery=recovery_proofs[cell.incident_case_id],
            artifact_dir=artifact_dir,
            budget=budget,
            evaluator=evaluator_identity,
        )
        write_evaluation_input_bundle(tmp_path, scoring_inputs, created_at=started_at)

        ledger.extend(
            (
                BenchmarkLedgerEntry.create(
                    manifest_id=manifest.manifest_id,
                    sequence=cell.sequence,
                    run_id=cell.run_id,
                    incident_case_id=cell.incident_case_id,
                    strategy=cell.strategy,
                    state="STARTED",
                    now=started_at,
                    started_at=started_at,
                ).model_dump_json(),
                BenchmarkLedgerEntry.create(
                    manifest_id=manifest.manifest_id,
                    sequence=cell.sequence,
                    run_id=cell.run_id,
                    incident_case_id=cell.incident_case_id,
                    strategy=cell.strategy,
                    state=(
                        "COMPLETED" if evaluation.status is EvaluationStatus.PASSED else "FAILED"
                    ),
                    now=started_at + timedelta(seconds=1),
                    started_at=started_at,
                    reason_code=(
                        None
                        if evaluation.status is EvaluationStatus.PASSED
                        else "EVALUATION_FAILED"
                    ),
                ).model_dump_json(),
            )
        )

    suite_root = tmp_path / "artifacts" / "benchmarks" / manifest.manifest_id
    suite_root.mkdir(parents=True)
    (suite_root / "ledger.jsonl").write_text("\n".join(ledger) + "\n", encoding="utf-8")
    doctor_result = DoctorResult(
        status=DoctorStatus.PASSED,
        checks=tuple(DoctorRunner._check(code, True, "OK") for code in DoctorCheckCode),
    )
    doctor_receipt = BenchmarkDoctorReceipt(
        manifest_id=manifest.manifest_id,
        manifest_sha256=manifest.digest(),
        implementation_revision=manifest.implementation_revision,
        checkout_revision=checkout_revision,
        result_inputs_sha256=BenchmarkReporter.result_inputs_digest(manifest),
        model_probe_required=True,
        checked_at=NOW,
        result=doctor_result,
    )
    (suite_root / "doctor.json").write_text(
        doctor_receipt.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )
    probe_receipt = create_planner_probe_receipt(
        manifest,
        checkout_revision=checkout_revision,
        model_provider=manifest.model_configuration.provider,
        model_name=manifest.model_configuration.model,
        passed=True,
        observed="PLAN_LOOP_COMPLETED",
        transport=None,
        detail={"plan_step_receipts": 1},
        checked_at=NOW,
    )
    write_planner_probe_receipt(suite_root / "planner-probe.json", probe_receipt)
    return suite_root


def test_formal_report_rejects_archived_trace_gate_internal_error(tmp_path: Path) -> None:
    manifest = build_experiment_manifest("a" * 40, project_root=PROJECT_ROOT)
    cell = next(
        item for item in manifest.cells if item.strategy is DiagnosticStrategy.EVIDENCE_PLANNER
    )
    _write_synthetic_gate_error_artifact(tmp_path, manifest, cell)
    suite_root = tmp_path / "artifacts" / "benchmarks" / manifest.manifest_id
    suite_root.mkdir(parents=True)
    reporter = PlannerComparisonReporter(manifest, suite_root, project_root=tmp_path)
    terminal = BenchmarkLedgerEntry.create(
        manifest_id=manifest.manifest_id,
        sequence=cell.sequence,
        run_id=cell.run_id,
        incident_case_id=cell.incident_case_id,
        strategy=cell.strategy,
        state="COMPLETED",
        now=NOW,
        started_at=NOW,
    )

    # This persisted six-file archive has no controller_checks; the failure is
    # represented only in the trace's evidence-gate reason_code.
    with pytest.raises(PlannerComparisonReportError, match="trace records GATE_INTERNAL_ERROR"):
        reporter._load_cell(cell, terminal, "b" * 40)
    assert (tmp_path / ".dig" / "scoring-inputs" / cell.run_id).is_dir()


def test_formal_report_analyzes_complete_synthetic_archive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import data_incident_gym.planner_comparison_report as report_module

    manifest = build_experiment_manifest("a" * 40, project_root=PROJECT_ROOT)
    suite_root = _write_complete_synthetic_suite(tmp_path, manifest)
    monkeypatch.setattr(
        report_module,
        "verify_experiment_manifest",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        report_module,
        "verify_scoring_identity",
        lambda *_args, **_kwargs: None,
    )
    reporter = PlannerComparisonReporter(manifest, suite_root, project_root=tmp_path)

    summary_path, markdown_path, summary = reporter.write(tmp_path / "derived-report")

    assert summary["integrity"]["status"] == "VERIFIED_COMPLETE"
    assert summary["integrity"]["cells"] == 108
    assert summary["integrity"]["full_evaluation_recomputations"] == 108
    assert summary["primary"]["paired_planner_vs_kernel"]["pairs"] == 36
    assert summary["primary"]["screening"] == "NOT_ESTABLISHED_T06_IDENTITY_PENDING"
    planner_costs = summary["costs_and_failures"][DiagnosticStrategy.EVIDENCE_PLANNER.value]
    assert "output_retries" not in planner_costs["totals"]
    assert planner_costs["retry_observations"] == {
        "model_protocol": {
            "status": "INCOMPLETE",
            "output_retry_used_observations": None,
            "events_without_output_retry_used": 0,
        },
        "evidence_gate_refusal_events": 0,
    }
    assert summary_path.is_file()
    assert markdown_path.is_file()
    assert "NOT_ESTABLISHED_T06_IDENTITY_PENDING" in markdown_path.read_text(encoding="utf-8")


def _metric_item(
    sequence: int,
    strategy: DiagnosticStrategy,
    events: tuple[object, ...],
) -> dict[str, object]:
    return {
        "cell": SimpleNamespace(
            sequence=sequence,
            run_id=f"{sequence:032x}",
            strategy=strategy,
        ),
        "trace": tuple(SimpleNamespace(event=event) for event in events),
        "diagnosis": SimpleNamespace(evidence_ids=(), claims=()),
    }


def test_retry_observations_keep_protocol_values_separate_from_gate_refusals() -> None:
    protocol_event = ModelProtocolTraceEvent(
        event_type="MODEL_PROTOCOL",
        stage="OUTPUT_VALIDATION",
        tool_name="submit_diagnosis",
        category="DECISION_CONTRACT_REJECTED",
        output_retry_used=1,
    )
    gate_refusal = EvidenceGateTraceEvent(
        event_type="EVIDENCE_GATE",
        reason_code="SUBMISSION_REJECTED",
        accepted=False,
    )
    item = _metric_item(
        1,
        DiagnosticStrategy.EVIDENCE_PLANNER,
        (protocol_event, protocol_event, gate_refusal),
    )

    assert _retry_observations([item]) == {
        "model_protocol": {
            "status": "INCOMPLETE",
            "output_retry_used_observations": [1, 1],
            "events_without_output_retry_used": 0,
        },
        "evidence_gate_refusal_events": 1,
    }

    unobserved_event = ModelProtocolTraceEvent(
        event_type="MODEL_PROTOCOL",
        stage="PROVIDER_RESPONSE",
        tool_name=None,
        category="PROVIDER_PROTOCOL_FAILURE",
        output_retry_used=None,
    )
    unobserved = _retry_observations(
        [_metric_item(2, DiagnosticStrategy.EVIDENCE_PLANNER, (unobserved_event,))]
    )
    assert unobserved["model_protocol"]["status"] == "INCOMPLETE"
    assert unobserved["model_protocol"]["output_retry_used_observations"] is None
    assert unobserved["model_protocol"]["events_without_output_retry_used"] == 1


def _metric_items(*, include_tool_error: bool, omit_first_planner_state: bool = False):
    items: list[dict[str, object]] = []
    sequence = 0
    for strategy in (
        DiagnosticStrategy.EVIDENCE_PLANNER,
        DiagnosticStrategy.DIAGNOSTIC_KERNEL,
        DiagnosticStrategy.STATIC_SKILL,
    ):
        for repeat in range(36):
            sequence += 1
            events: tuple[object, ...] = ()
            if strategy is DiagnosticStrategy.EVIDENCE_PLANNER:
                if include_tool_error and repeat == 0:
                    events = (
                        PlanTraceEvent(
                            kind="STEP",
                            obligation_id="obligation-1",
                            tool_name="get_relation_schema",
                        ),
                        PlanTraceEvent(
                            kind="STEP",
                            obligation_id="obligation-2",
                            tool_name="get_relation_schema",
                        ),
                        ToolTraceEvent(
                            event_type="TOOL_CALL",
                            tool_name="get_relation_schema",
                            arguments={"relation_name": "orders"},
                            fingerprint="a" * 64,
                            evidence_ids=(),
                            error_code="BACKEND_ERROR",
                            elapsed_ms=1,
                        ),
                        ToolTraceEvent(
                            event_type="TOOL_CALL",
                            tool_name="get_relation_schema",
                            arguments={"relation_name": "orders"},
                            fingerprint="b" * 64,
                            evidence_ids=(),
                            error_code=None,
                            elapsed_ms=1,
                        ),
                        PlanTraceEvent(kind="STATE"),
                    )
                elif omit_first_planner_state and repeat == 0:
                    events = ()
                else:
                    events = (PlanTraceEvent(kind="STATE"),)
            items.append(_metric_item(sequence, strategy, events))
    return items


def test_plan_metrics_attribute_tool_error_using_event_order() -> None:
    manifest = build_experiment_manifest("a" * 40, project_root=PROJECT_ROOT)
    reporter = PlannerComparisonReporter(manifest, Path("."), project_root=PROJECT_ROOT)

    metrics = reporter._planner_metrics(_metric_items(include_tool_error=True))

    assert metrics["planner_tool_receipts"]["errors_without_archived_backend_origin_by_code"] == {
        "BACKEND_ERROR": 1
    }
    assert (
        metrics["planner_tool_receipts"]["tool_error_followed_by_replan_origin_unclassified"] == 1
    )
    assert (
        metrics["planner_tool_receipts"][
            "tool_error_followed_by_changed_obligation_origin_unclassified"
        ]
        == 1
    )


def test_plan_metrics_turns_missing_plan_trace_into_report_error() -> None:
    manifest = build_experiment_manifest("a" * 40, project_root=PROJECT_ROOT)
    reporter = PlannerComparisonReporter(manifest, Path("."), project_root=PROJECT_ROOT)

    with pytest.raises(PlannerComparisonReportError, match="planner trace must end"):
        reporter._planner_metrics(
            _metric_items(include_tool_error=False, omit_first_planner_state=True)
        )
