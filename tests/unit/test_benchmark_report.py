from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from data_incident_gym.artifacts import (
    ARTIFACT_FILENAMES,
    BudgetSummary,
    EvidenceArtifact,
    RunMetadata,
    TraceEnvelope,
)
from data_incident_gym.benchmark_manifest import build_manifest
from data_incident_gym.benchmark_report import BenchmarkReporter, BenchmarkReportError
from data_incident_gym.benchmark_runner import (
    BenchmarkCellSelector,
    BenchmarkDoctorReceipt,
    BenchmarkLedgerEntry,
)
from data_incident_gym.diagnosis import (
    Diagnosis,
    DiagnosisMetrics,
    DiagnosisRunResult,
    DiagnosisStatus,
    DiagnosisTerminalTraceEvent,
    DiagnosticStrategy,
    EvidenceGateTraceEvent,
)
from data_incident_gym.diagnostic_agent import P1_ROOT_CAUSE_CODES, policy_identity_for_strategy
from data_incident_gym.diagnostic_kernel import DiagnosticKernel
from data_incident_gym.doctor import (
    CHECK_ORDER,
    DoctorCheck,
    DoctorCheckCode,
    DoctorResult,
    DoctorStatus,
)
from data_incident_gym.evaluation import (
    EvaluationApplicability,
    EvaluationCheck,
    EvaluationCheckCode,
    EvaluationResult,
    EvaluationStatus,
)
from data_incident_gym.fixed_rule import fixed_rule_policy_identity


def _evaluation(case_id: str, run_id: str) -> EvaluationResult:
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


def _doctor() -> DoctorResult:
    return DoctorResult(
        status=DoctorStatus.PASSED,
        checks=tuple(
            DoctorCheck(
                code=DoctorCheckCode(code),
                passed=True,
                observed="ok",
                reason_code=f"{code}_PASSED",
                recommendation_code=None,
            )
            for code in CHECK_ORDER
        ),
    )


def _write_fixture(
    tmp_path: Path,
    *,
    manifest_mismatch: bool = False,
    safety_failure: bool = False,
) -> tuple[Path, Path]:
    manifest = build_manifest("a" * 40)
    suite_root = tmp_path / "artifacts" / "benchmarks" / manifest.manifest_id
    suite_root.mkdir(parents=True)
    receipt = BenchmarkDoctorReceipt(
        manifest_id=manifest.manifest_id,
        manifest_sha256=("b" * 64 if manifest_mismatch else manifest.digest()),
        implementation_revision=manifest.implementation_revision,
        checkout_revision="c" * 40,
        result_inputs_sha256=BenchmarkReporter.result_inputs_digest(manifest),
        model_probe_required=True,
        checked_at=datetime(2026, 9, 1, tzinfo=UTC),
        result=_doctor(),
    )
    (suite_root / "doctor.json").write_text(
        receipt.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )

    artifact_root = tmp_path / "artifacts"
    start = datetime(2026, 9, 1, tzinfo=UTC)
    ledger_lines: list[str] = []
    for cell in manifest.cells:
        diagnosis = Diagnosis(
            status=DiagnosisStatus.MODEL_ERROR,
            run_id=cell.run_id,
            summary="MODEL_RUNTIME_ERROR",
            confidence=0.0,
        )
        kernel_state = None
        trace = [
            EvidenceGateTraceEvent(
                event_type="EVIDENCE_GATE",
                reason_code="MODEL_RUNTIME_ERROR",
                accepted=True,
            )
        ]
        if cell.strategy in {
            DiagnosticStrategy.DIAGNOSTIC_KERNEL,
            DiagnosticStrategy.KERNEL_NO_LINEAGE,
            DiagnosticStrategy.KERNEL_NO_SCHEMA,
        }:
            kernel = DiagnosticKernel.start(
                run_id=cell.run_id,
                allowed_root_cause_codes=P1_ROOT_CAUSE_CODES,
                model_request_limit=8,
                tool_call_limit=8,
            )
            kernel.terminate_model_error("MODEL_RUNTIME_ERROR")
            kernel_state = kernel.snapshot(model_requests_used=0)
            from data_incident_gym.diagnosis import KernelStateTraceEvent

            trace.append(KernelStateTraceEvent(event_type="KERNEL_STATE", state=kernel_state))
        trace.append(
            DiagnosisTerminalTraceEvent(
                event_type="DIAGNOSIS_TERMINAL",
                strategy=cell.strategy,
                status=diagnosis.status,
                evidence_inventory=(),
            )
        )
        diagnosis_run = DiagnosisRunResult(
            strategy=cell.strategy,
            policy_identity=(
                fixed_rule_policy_identity()
                if cell.strategy is DiagnosticStrategy.FIXED_RULE
                else policy_identity_for_strategy(cell.strategy)
            ),
            diagnosis=diagnosis,
            evidence_records=(),
            trace=tuple(trace),
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
            kernel_state=kernel_state,
        )
        evaluation = _evaluation(cell.incident_case_id, cell.run_id)
        if safety_failure and cell.sequence == 1:
            failed_code = EvaluationCheckCode.TRACE_READ_ONLY_SAFE
            checks = tuple(
                check.model_copy(
                    update={
                        "applicability": EvaluationApplicability.APPLICABLE,
                        "passed": False,
                        "expected": ("SAFE",),
                        "actual": ("UNSAFE",),
                        "reason_code": f"{failed_code.value}_FAILED",
                    }
                )
                if check.code is failed_code
                else check
                for check in evaluation.checks
            )
            evaluation = evaluation.model_copy(
                update={
                    "status": EvaluationStatus.FAILED,
                    "checks": checks,
                    "failed_check_codes": (failed_code,),
                }
            )
        policy = diagnosis_run.policy_identity
        metadata = RunMetadata(
            schema_version="p1.metadata.v1",
            incident_case_id=cell.incident_case_id,
            run_id=cell.run_id,
            strategy=cell.strategy,
            code_revision="c" * 40,
            workspace_dirty=False,
            provider="synthetic",
            model="synthetic",
            model_base_url=manifest.model_configuration.base_url,
            budget=BudgetSummary(
                model_request_limit=8,
                tool_call_limit=8,
                output_retry_limit=2,
                timeout_seconds=300,
            ),
            base_prompt_version=policy.base_prompt_version,
            base_prompt_sha256=policy.base_prompt_sha256,
            strategy_prompt_version=policy.strategy_prompt_version,
            strategy_prompt_sha256=policy.strategy_prompt_sha256,
            controller_protocol_version=policy.controller_protocol_version,
            controller_protocol_sha256=policy.controller_protocol_sha256,
            tool_schema_sha256=policy.tool_schema_sha256,
            benchmark_manifest_sha256=manifest.digest(),
            variant_role=evaluation.variant_role,
            answerability=evaluation.answerability,
            expected_status=evaluation.expected_status,
            started_at=start,
            finished_at=start + timedelta(seconds=1),
            elapsed_ms=1000,
            diagnosis_metrics=diagnosis_run.metrics,
            evaluation_status=evaluation.status,
            recovery_status="HEALTHY",
            artifact_files=ARTIFACT_FILENAMES,
        )
        artifact_path = artifact_root / cell.run_id
        artifact_path.mkdir(parents=True)
        (artifact_path / "metadata.json").write_text(
            metadata.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )
        (artifact_path / "trace.jsonl").write_text(
            "".join(
                TraceEnvelope(
                    schema_version="p1.trace.v1", sequence=index, event=event
                ).model_dump_json()
                + "\n"
                for index, event in enumerate(diagnosis_run.trace, start=1)
            ),
            encoding="utf-8",
        )
        (artifact_path / "evidence.json").write_text(
            EvidenceArtifact(
                schema_version="p1.evidence.v1",
                incident_case_id=cell.incident_case_id,
                run_id=cell.run_id,
                records=(),
            ).model_dump_json(indent=2)
            + "\n",
            encoding="utf-8",
        )
        (artifact_path / "diagnosis.json").write_text(
            diagnosis.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )
        (artifact_path / "evaluation.json").write_text(
            evaluation.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )
        (artifact_path / "report.md").write_text("synthetic\n", encoding="utf-8")
        started = BenchmarkLedgerEntry.create(
            manifest_id=manifest.manifest_id,
            sequence=cell.sequence,
            run_id=cell.run_id,
            incident_case_id=cell.incident_case_id,
            strategy=cell.strategy,
            state="STARTED",
            now=start,
            started_at=start,
        )
        terminal = BenchmarkLedgerEntry.create(
            manifest_id=manifest.manifest_id,
            sequence=cell.sequence,
            run_id=cell.run_id,
            incident_case_id=cell.incident_case_id,
            strategy=cell.strategy,
            state="COMPLETED" if evaluation.status is EvaluationStatus.PASSED else "FAILED",
            now=start + timedelta(seconds=1),
            started_at=start,
            reason_code=None
            if evaluation.status is EvaluationStatus.PASSED
            else "EVALUATION_FAILED",
        )
        ledger_lines.extend((started.model_dump_json(), terminal.model_dump_json()))
    (suite_root / "ledger.jsonl").write_text("\n".join(ledger_lines) + "\n", encoding="utf-8")
    return suite_root, artifact_root


def test_reporter_writes_deterministic_summary_and_markdown(tmp_path: Path) -> None:
    suite_root, _ = _write_fixture(tmp_path)
    manifest = build_manifest("a" * 40)

    result = BenchmarkReporter(manifest, suite_root).write()
    first = tuple(path.read_bytes() for path in result)
    second = BenchmarkReporter(manifest, suite_root).write()

    assert result == second
    assert first == tuple(path.read_bytes() for path in second)
    assert not tuple(suite_root.glob(".*.tmp"))
    summary = json.loads((suite_root / "summary.json").read_text(encoding="utf-8"))
    assert summary["manifest_sha256"] == manifest.digest()
    assert summary["cells"] == {"total": 106, "model_backed": 94, "fixed_rule": 12}
    assert "当前固定样本尚未证明 Diagnostic Kernel 优势。" in (suite_root / "report.md").read_text(
        encoding="utf-8"
    )


def test_reporter_refuses_subset_suite(tmp_path: Path) -> None:
    manifest = build_manifest("a" * 40)
    suite_root = tmp_path / "artifacts" / "benchmarks" / manifest.manifest_id
    suite_root.mkdir(parents=True)
    (suite_root / "subset.json").write_text(
        BenchmarkCellSelector(
            manifest_id=manifest.manifest_id,
            strategies=(DiagnosticStrategy.FIXED_RULE,),
        ).model_dump_json(indent=2)
        + "\n",
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(BenchmarkReportError, match="subset"):
        BenchmarkReporter(manifest, suite_root).write()


def test_reporter_refuses_subset_scope_even_when_marker_is_missing(tmp_path: Path) -> None:
    suite_root, _ = _write_fixture(tmp_path)
    manifest = build_manifest("a" * 40)
    receipt_path = suite_root / "doctor.json"
    receipt = BenchmarkDoctorReceipt.model_validate(json.loads(receipt_path.read_text()))
    receipt_path.write_text(
        receipt.model_copy(
            update={
                "cell_selector": BenchmarkCellSelector(
                    manifest_id=manifest.manifest_id,
                    strategies=(DiagnosticStrategy.FIXED_RULE,),
                )
            }
        ).model_dump_json(indent=2)
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(BenchmarkReportError, match="doctor receipt"):
        BenchmarkReporter(manifest, suite_root).write()


@pytest.mark.parametrize("fixture", ["manifest_mismatch", "missing_artifact"])
def test_reporter_fails_closed_for_invalid_suite(tmp_path: Path, fixture: str) -> None:
    suite_root, artifact_root = _write_fixture(
        tmp_path, manifest_mismatch=fixture == "manifest_mismatch"
    )
    manifest = build_manifest("a" * 40)
    if fixture == "missing_artifact":
        next(artifact_root.glob("*/metadata.json")).unlink()

    with pytest.raises(BenchmarkReportError):
        BenchmarkReporter(manifest, suite_root).write()


def test_reporter_retains_legal_safety_gate_failure_as_invalid_conclusion(tmp_path: Path) -> None:
    suite_root, _ = _write_fixture(tmp_path, safety_failure=True)
    manifest = build_manifest("a" * 40)

    BenchmarkReporter(manifest, suite_root).write()

    summary = json.loads((suite_root / "summary.json").read_text(encoding="utf-8"))
    assert summary["conclusion"]["status"] == "INVALID"
    assert summary["invalid_gates"][0]["run_id"] == manifest.cells[0].run_id


def test_reporter_accepts_legal_fixed_rule_evidence_tool(tmp_path: Path) -> None:
    suite_root, artifact_root = _write_fixture(tmp_path)
    manifest = build_manifest("a" * 40)
    fixed_cell = next(
        cell for cell in manifest.cells if cell.strategy is DiagnosticStrategy.FIXED_RULE
    )
    trace_path = artifact_root / fixed_cell.run_id / "trace.jsonl"
    envelopes = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
    for envelope in envelopes:
        envelope["sequence"] += 1
    tool_call = {
        "schema_version": "p1.trace.v1",
        "sequence": 1,
        "event": {
            "event_type": "TOOL_CALL",
            "tool_name": "get_dbt_run_results",
            "arguments": {"run_id": fixed_cell.run_id},
            "fingerprint": "e" * 64,
            "evidence_ids": [],
            "error_code": None,
            "elapsed_ms": 0,
        },
    }
    trace_path.write_text(
        "\n".join(json.dumps(item) for item in [tool_call, *envelopes]) + "\n",
        encoding="utf-8",
    )

    BenchmarkReporter(manifest, suite_root).write()


def test_reporter_independently_rejects_tool_outside_strategy_allowlist(
    tmp_path: Path,
) -> None:
    suite_root, artifact_root = _write_fixture(tmp_path)
    manifest = build_manifest("a" * 40)
    no_tool_cell = next(
        cell for cell in manifest.cells if cell.strategy is DiagnosticStrategy.NO_TOOL
    )
    trace_path = artifact_root / no_tool_cell.run_id / "trace.jsonl"
    envelopes = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
    for envelope in envelopes:
        envelope["sequence"] += 1
    tool_call = {
        "schema_version": "p1.trace.v1",
        "sequence": 1,
        "event": {
            "event_type": "TOOL_CALL",
            "tool_name": "get_dbt_run_results",
            "arguments": {"run_id": no_tool_cell.run_id},
            "fingerprint": "f" * 64,
            "evidence_ids": [],
            "error_code": None,
            "elapsed_ms": 0,
        },
    }
    trace_path.write_text(
        "\n".join(json.dumps(item) for item in [tool_call, *envelopes]) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(BenchmarkReportError, match="outside the strategy allowlist"):
        BenchmarkReporter(manifest, suite_root).write()


def test_summary_metrics_use_paired_and_run_level_contracts() -> None:
    manifest = build_manifest("a" * 40)

    def check(code: EvaluationCheckCode, *, expected: tuple[str, ...] = ("ok",)):
        return SimpleNamespace(
            code=code,
            passed=True,
            applicability=EvaluationApplicability.APPLICABLE,
            expected=expected,
            actual=expected,
        )

    checks = tuple(check(code) for code in EvaluationCheckCode)
    confirmed = SimpleNamespace(
        status=DiagnosisStatus.CONFIRMED,
        claims=(SimpleNamespace(evidence_ids=("ev_" + "1" * 64,)),),
        evidence_ids=("ev_" + "1" * 64,),
        affected_assets=("model.orders",),
    )
    insufficient = SimpleNamespace(
        status=DiagnosisStatus.INSUFFICIENT_EVIDENCE,
        claims=(),
        evidence_ids=(),
        affected_assets=(),
    )

    def record(case_id: str, run_id: str, strategy: DiagnosticStrategy, diagnosis, role: str):
        evaluation = SimpleNamespace(
            expected_status=(
                DiagnosisStatus.CONFIRMED.value
                if role == "TEST_CONFIRMABLE"
                else DiagnosisStatus.INSUFFICIENT_EVIDENCE.value
            ),
            status=EvaluationStatus.PASSED,
            checks=checks,
        )
        return {
            "cell": SimpleNamespace(
                strategy=strategy,
                model_backed=True,
                incident_case_id=case_id,
                repeat_index=1,
                run_id=run_id,
            ),
            "metadata": SimpleNamespace(
                variant_role=role,
                diagnosis_metrics=SimpleNamespace(
                    successful_tool_calls=2,
                    model_requests=1,
                    input_tokens=10,
                    output_tokens=5,
                    elapsed_ms=20,
                ),
            ),
            "diagnosis": diagnosis,
            "evaluation": evaluation,
            "trace": (),
            "ledger": SimpleNamespace(state="COMPLETED"),
            "invalid_gates": (),
        }

    records = [
        record(
            "duplicate_payment_coupon_a",
            "a" * 32,
            DiagnosticStrategy.STATIC_SKILL,
            confirmed,
            "TEST_CONFIRMABLE",
        ),
        record(
            "duplicate_payment_coupon_b",
            "b" * 32,
            DiagnosticStrategy.STATIC_SKILL,
            insufficient,
            "TEST_INSUFFICIENT",
        ),
    ]
    summary = BenchmarkReporter(manifest, Path(".")).summary(
        records, SimpleNamespace(result=_doctor())
    )

    assert summary["strategies"]["STATIC_SKILL"]["paired_success"]["rate"] == 1.0
    assert summary["main_metrics"]["STATIC_SKILL"]["root_cause_accuracy"]["total"] == 1
    assert summary["main_metrics"]["STATIC_SKILL"]["affected_assets_macro_f1"]["value"] == 1.0
    assert summary["strategies"]["STATIC_SKILL"]["efficiency"]["successful_tools_median"] == 2
    assert summary["strategies"]["STATIC_SKILL"]["efficiency"]["exact_duplicate_calls"] == 0
