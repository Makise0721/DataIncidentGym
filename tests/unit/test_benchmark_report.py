from __future__ import annotations

import json
import shutil
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from data_incident_gym.artifacts import (
    ARTIFACT_FILENAMES,
    BudgetSummary,
    EvidenceArtifact,
    RunMetadata,
    trace_envelope_model,
    trace_schema_for_run,
)
from data_incident_gym.benchmark_manifest import BenchmarkManifest, build_manifest
from data_incident_gym.benchmark_report import (
    BenchmarkReporter,
    BenchmarkReportError,
    analyze_partial_suite,
)
from data_incident_gym.benchmark_runner import (
    BenchmarkCellSelector,
    BenchmarkDoctorReceipt,
    BenchmarkLedgerEntry,
)
from data_incident_gym.diagnosis import (
    AffectedAssetClaim,
    Diagnosis,
    DiagnosisMetrics,
    DiagnosisRunResult,
    DiagnosisRunResultV2,
    DiagnosisRunResultV3,
    DiagnosisStatus,
    DiagnosisTerminalTraceEvent,
    DiagnosticStrategy,
    EvidenceGateTraceEvent,
    EvidenceGateTraceEventV2,
    RootCauseClaim,
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
    ControllerCheck,
    ControllerCheckCode,
    EvaluationApplicability,
    EvaluationCheck,
    EvaluationCheckCode,
    EvaluationResult,
    EvaluationStatus,
    claim_support_verdicts,
)
from data_incident_gym.fixed_rule import fixed_rule_policy_identity
from data_incident_gym.scenarios import load_scenario_spec


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
    manifest: BenchmarkManifest | None = None,
    manifest_mismatch: bool = False,
    failing_gate: EvaluationCheckCode | None = None,
    failing_controller_gate: ControllerCheckCode | None = None,
) -> tuple[Path, Path]:
    manifest = manifest or build_manifest("a" * 40)
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
        policy_identity = (
            fixed_rule_policy_identity()
            if cell.strategy is DiagnosticStrategy.FIXED_RULE
            else policy_identity_for_strategy(cell.strategy)
        )
        kernel_state = None
        gate_event = (
            EvidenceGateTraceEventV2(
                schema_version="p1.evidence_gate.v2",
                event_type="EVIDENCE_GATE",
                reason_code="MODEL_RUNTIME_ERROR",
                accepted=True,
            )
            if policy_identity.controller_protocol_version == "p1.controller.v22"
            else EvidenceGateTraceEvent(
                event_type="EVIDENCE_GATE",
                reason_code="MODEL_RUNTIME_ERROR",
                accepted=True,
            )
        )
        trace = [gate_event]
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
        run_model = (
            DiagnosisRunResultV3
            if policy_identity.controller_protocol_version == "p1.controller.v22"
            else DiagnosisRunResultV2
            if policy_identity.controller_protocol_version == "p1.controller.v21"
            else DiagnosisRunResult
        )
        diagnosis_run = run_model(
            strategy=cell.strategy,
            policy_identity=policy_identity,
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
        if failing_gate is not None and cell.sequence == 1:
            failed_code = failing_gate
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
        if failing_controller_gate is not None and cell.sequence == 1:
            evaluation = evaluation.model_copy(
                update={
                    "controller_checks": (
                        ControllerCheck(
                            code=failing_controller_gate,
                            passed=False,
                            expected=("KERNEL_CONTRACT",),
                            actual=("VIOLATED",),
                            reason_code=f"{failing_controller_gate.value}_FAILED",
                        ),
                    )
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
        trace_schema = trace_schema_for_run(diagnosis_run)
        envelope_model = trace_envelope_model(trace_schema)
        (artifact_path / "trace.jsonl").write_text(
            "".join(
                envelope_model(
                    schema_version=trace_schema, sequence=index, event=event
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
    report = (suite_root / "report.md").read_text(encoding="utf-8")
    assert "当前固定样本尚未证明 Diagnostic Kernel 优势。" in report
    assert "## Refusal witness sources" in report
    assert "Controller precheck" in report


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
    suite_root, _ = _write_fixture(
        tmp_path, failing_gate=EvaluationCheckCode.TRACE_READ_ONLY_SAFE
    )
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
        "schema_version": envelopes[0]["schema_version"],
        "sequence": 1,
        "event": {
                "event_type": (
                    "TOOL_CALL_V2"
                    if envelopes[0]["schema_version"] in {"p1.trace.v2", "p1.trace.v3"}
                    else "TOOL_CALL"
            ),
            "tool_name": "get_dbt_run_results",
            "arguments": {"run_id": no_tool_cell.run_id},
            "fingerprint": "f" * 64,
            "evidence_ids": [],
            "error_code": None,
            "elapsed_ms": 0,
            **(
                {"outcome_origin": "EVIDENCE_BACKEND"}
                    if envelopes[0]["schema_version"] in {"p1.trace.v2", "p1.trace.v3"}
                    else {}
            ),
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
    confirmed_scenario = load_scenario_spec("duplicate_payment_coupon_a")
    confirmed = SimpleNamespace(
        status=DiagnosisStatus.CONFIRMED,
        claims=(
            RootCauseClaim(
                kind="ROOT_CAUSE",
                root_cause_code="SOURCE_EXACT_PAYMENT_DUPLICATE",
                evidence_ids=("ev_" + "1" * 64,),
            ),
            AffectedAssetClaim(
                kind="AFFECTED_ASSET",
                asset=confirmed_scenario.affected_assets[0],
                evidence_ids=("ev_" + "2" * 64,),
            ),
        ),
        evidence_ids=("ev_" + "1" * 64, "ev_" + "2" * 64),
        affected_assets=(confirmed_scenario.affected_assets[0],),
        unresolved_evidence=(),
    )
    insufficient = SimpleNamespace(
        status=DiagnosisStatus.INSUFFICIENT_EVIDENCE,
            claims=(),
            evidence_ids=(),
            affected_assets=(),
            unresolved_evidence=(),
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
        scenario = load_scenario_spec(case_id)
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
            "scenario": scenario,
            "records": (),
            "claim_verdicts": claim_support_verdicts(scenario, diagnosis, ()),
            "ledger": SimpleNamespace(state="COMPLETED"),
            "environment_gates": (),
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
    # The claim and citation blocks run on the same records; the synthetic
    # citations are unknown ids, so nothing is supported or known here.
    assert summary["main_metrics"]["STATIC_SKILL"]["claim_support"]["support_coverage"][
        "rate"
    ] == 0.0
    assert summary["main_metrics"]["STATIC_SKILL"]["citation_quality"]["existence"][
        "numerator"
    ] == 0
    assert summary["main_metrics"]["STATIC_SKILL"]["abstention"][
        "confirmable_over_abstention"
    ]["rate"] == 0.0


def test_reliability_block_follows_the_frozen_repeat_schedule(tmp_path: Path) -> None:
    manifest = build_manifest("a" * 40)
    suite_root, _ = _write_fixture(tmp_path, manifest=manifest)
    summary_path, report_path = BenchmarkReporter(manifest, suite_root).write()
    summary = json.loads(summary_path.read_text(encoding="utf-8"))

    main = summary["strategies"]["STATIC_SKILL"]["reliability"]
    assert main["protocol_version"] == "p1.reliability.v1"
    assert main["groups_total"] == 12
    assert main["groups_complete"] == 12
    assert main["groups_incomplete"] == 0
    assert {group["planned_repetitions"] for group in main["groups"]} == {3}
    # Every synthetic fixture evaluation passes, so all repetitions succeed.
    assert main["macro_pass_hat"]["1"] == {
        "value": 1.0,
        "groups": 12,
        "trials": 36,
        "zero_denominator_reason": None,
    }
    assert main["macro_pass_hat"]["3"]["value"] == 1.0

    fixed = summary["strategies"]["FIXED_RULE"]["reliability"]
    assert {group["planned_repetitions"] for group in fixed["groups"]} == {1}
    assert fixed["macro_pass_hat"]["1"]["value"] == 1.0
    # n = 1 never yields pass^2/pass^3; nothing may be borrowed from other groups.
    assert fixed["macro_pass_hat"]["2"]["value"] is None
    assert fixed["macro_pass_hat"]["2"]["zero_denominator_reason"] == (
        "no complete group with n >= 2"
    )

    assert "## Repeat reliability (p1.reliability.v1)" in report_path.read_text(encoding="utf-8")


def test_reliability_marks_an_invalid_environment_sample_incomplete(tmp_path: Path) -> None:
    manifest = build_manifest("a" * 40)
    suite_root, _ = _write_fixture(
        tmp_path, manifest=manifest, failing_gate=EvaluationCheckCode.RECOVERY_HEALTHY
    )
    summary_path, _ = BenchmarkReporter(manifest, suite_root).write()
    summary = json.loads(summary_path.read_text(encoding="utf-8"))

    main = summary["strategies"]["STATIC_SKILL"]["reliability"]
    assert main["groups_complete"] == 11
    assert main["groups_incomplete"] == 1
    invalid = next(group for group in main["groups"] if not group["complete"])

    assert invalid["invalid_repeat_indices"] == [1]
    assert invalid["invalid_gate_codes"] == ["RECOVERY_HEALTHY"]
    assert set(invalid["pass_hat"].values()) == {None}
    assert invalid["complete_subset"]["coverage"] == pytest.approx(2 / 3)
    # The macro covers the 11 complete groups only.
    assert main["macro_pass_hat"]["3"]["groups"] == 11


def test_agent_rule_violation_stays_a_failed_trial(tmp_path: Path) -> None:
    """An agent-side rule violation — a write attempt here — is never an invalid
    environment sample: the group stays complete and the trial counts as failed,
    so breaking the rules can never improve a reliability number."""

    manifest = build_manifest("a" * 40)
    suite_root, _ = _write_fixture(
        tmp_path, manifest=manifest, failing_gate=EvaluationCheckCode.TRACE_READ_ONLY_SAFE
    )
    summary_path, _ = BenchmarkReporter(manifest, suite_root).write()
    summary = json.loads(summary_path.read_text(encoding="utf-8"))

    main = summary["strategies"]["STATIC_SKILL"]["reliability"]
    assert main["groups_complete"] == 12
    assert main["groups_incomplete"] == 0
    affected = next(
        group
        for group in main["groups"]
        if group["incident_case_id"] == manifest.cells[0].incident_case_id
    )
    assert affected["invalid_repeat_indices"] == []
    assert affected["successes"] == 2
    assert affected["pass_hat"] == {
        "1": pytest.approx(2 / 3),
        "2": pytest.approx(1 / 3),
        "3": 0.0,
    }
    # The same violation is still reported as a gate failure for the conclusion.
    assert summary["conclusion"]["status"] == "INVALID"
    assert summary["invalid_gates"][0]["gate"] == "TRACE_READ_ONLY_SAFE"


def test_environment_gate_isolation_shows_in_the_abstention_sets(tmp_path: Path) -> None:
    """The T07 metric sets use the same environment-only validity rule."""

    manifest = build_manifest("a" * 40)
    suite_root, _ = _write_fixture(
        tmp_path, manifest=manifest, failing_gate=EvaluationCheckCode.RECOVERY_HEALTHY
    )
    summary_path, _ = BenchmarkReporter(manifest, suite_root).write()
    summary = json.loads(summary_path.read_text(encoding="utf-8"))

    assert summary["main_metrics"]["STATIC_SKILL"]["abstention"]["cells"]["invalid_excluded"] == 1


def test_reliability_rejects_a_foreign_strategy_cell() -> None:
    """A caller that mixes strategies must not leak a foreign group into this
    strategy's reliability block."""

    manifest = build_manifest("a" * 40)
    reporter = BenchmarkReporter(manifest, Path("."))
    foreign = manifest.cells[0]
    other = next(cell for cell in manifest.cells if cell.strategy is not foreign.strategy)

    with pytest.raises(BenchmarkReportError, match="foreign-strategy cell"):
        reporter._reliability(  # noqa: SLF001
            other.strategy,
            [
                {
                    "cell": foreign,
                    "diagnosis": SimpleNamespace(status=DiagnosisStatus.MODEL_ERROR),
                    "evaluation": SimpleNamespace(
                        status=EvaluationStatus.FAILED, expected_status="CONFIRMED"
                    ),
                    "environment_gates": (),
                }
            ],
        )


def test_kernel_gate_failure_is_a_failed_trial_not_a_success(tmp_path: Path) -> None:
    """Audit regression: EvaluationResult.status only covers the evidence checks,
    so a run that violated the kernel contract can still read PASSED. The
    reliability success judgement must consult the controller gates too."""

    manifest = build_manifest("a" * 40)
    suite_root, _ = _write_fixture(
        tmp_path,
        manifest=manifest,
        failing_controller_gate=ControllerCheckCode.KERNEL_HYPOTHESIS_GATE,
    )
    summary_path, _ = BenchmarkReporter(manifest, suite_root).write()
    summary = json.loads(summary_path.read_text(encoding="utf-8"))

    # The gate failure is listed for the conclusion...
    assert summary["conclusion"]["status"] == "INVALID"
    assert summary["invalid_gates"][0]["gate"] == "KERNEL_HYPOTHESIS_GATE"

    # ...and it is a failed trial in every success-based metric.
    main = summary["strategies"]["STATIC_SKILL"]["reliability"]
    affected = next(
        group
        for group in main["groups"]
        if group["incident_case_id"] == manifest.cells[0].incident_case_id
    )
    assert affected["complete"] is True
    assert affected["invalid_repeat_indices"] == []
    assert affected["successes"] == 2
    assert affected["pass_hat"]["1"] == pytest.approx(2 / 3)
    assert affected["pass_hat"]["3"] == 0.0
    assert summary["strategies"]["STATIC_SKILL"]["completed"] == 36
    assert summary["strategies"]["STATIC_SKILL"]["efficiency"]["passed_cells"] == 35


def test_partial_analysis_reports_a_missing_repeat(tmp_path: Path) -> None:
    """The protocol's incomplete-group rules must be reachable for a suite that
    is still running or was interrupted, without relaxing the formal report."""

    manifest = build_manifest("a" * 40)
    suite_root, artifact_root = _write_fixture(tmp_path, manifest=manifest)
    ledger_lines = (suite_root / "ledger.jsonl").read_text(encoding="utf-8").splitlines()
    # Drop the last repetition of the first cell: its ledger entries and bundle.
    target = manifest.cells[2]
    assert target.incident_case_id != manifest.cells[0].incident_case_id or True
    kept = [
        line
        for line in ledger_lines
        if json.loads(line)["run_id"] != target.run_id
    ]
    (suite_root / "ledger.jsonl").write_text("\n".join(kept) + "\n", encoding="utf-8")
    shutil.rmtree(artifact_root / target.run_id)

    # The formal report still refuses the incomplete suite...
    with pytest.raises(BenchmarkReportError, match="exactly two entries per cell"):
        BenchmarkReporter(manifest, suite_root).write()

    # ...while the partial entry point reports the incomplete group honestly.
    analysis = analyze_partial_suite(manifest, suite_root)
    reliability = analysis["reliability"]
    group = next(
        item for item in reliability["groups"] if item["group_id"] == (
            f"{target.incident_case_id}/{target.strategy.value}"
        )
    )

    assert group["complete"] is False
    assert group["missing_repeat_indices"] == (target.repeat_index,)
    assert set(group["pass_hat"].values()) == {None}
    assert group["complete_subset"]["coverage"] == pytest.approx(2 / 3)
    assert reliability["groups_incomplete"] == 1
    missing = [cell for cell in analysis["cells"] if cell["state"] == "MISSING"]
    assert [cell["reason_code"] for cell in missing] == ["NO_TERMINAL_LEDGER_ENTRY"]
    assert analysis["ledger_terminal_conflicts"] == []


def test_partial_analysis_flags_unreadable_and_unbound_cells(tmp_path: Path) -> None:
    manifest = build_manifest("a" * 40)
    suite_root, artifact_root = _write_fixture(tmp_path, manifest=manifest)
    target = manifest.cells[0]
    shutil.rmtree(artifact_root / target.run_id)

    analysis = analyze_partial_suite(manifest, suite_root)
    unreadable = next(cell for cell in analysis["cells"] if cell["run_id"] == target.run_id)
    assert unreadable["state"] == "MISSING"
    assert unreadable["reason_code"] == "ARTIFACTS_UNREADABLE"

    # A different manifest identity may never borrow these samples.
    other = build_manifest("b" * 40, manifest_id=manifest.manifest_id)
    drifted = analyze_partial_suite(other, suite_root)
    reasons = Counter(cell["reason_code"] for cell in drifted["cells"])
    assert reasons["MANIFEST_IDENTITY_MISMATCH"] == len(manifest.cells) - 1
    assert reasons["ARTIFACTS_UNREADABLE"] == 1  # the bundle deleted above
    assert drifted["reliability"]["groups_complete"] == 0
    assert drifted["reliability"]["macro_pass_hat"]["1"]["value"] is None


def test_partial_analysis_flags_a_conflicting_ledger_terminal(tmp_path: Path) -> None:
    manifest = build_manifest("a" * 40)
    suite_root, _ = _write_fixture(tmp_path, manifest=manifest)
    ledger = suite_root / "ledger.jsonl"
    lines = ledger.read_text(encoding="utf-8").splitlines()
    conflicting = json.loads(lines[1])
    conflicting["state"] = "FAILED"
    conflicting["reason_code"] = "EVALUATION_FAILED"
    ledger.write_text(
        "\n".join((*lines, json.dumps(conflicting))) + "\n", encoding="utf-8"
    )

    analysis = analyze_partial_suite(manifest, suite_root)

    assert analysis["ledger_terminal_conflicts"] == [manifest.cells[0].run_id]
    conflicted = next(
        cell for cell in analysis["cells"] if cell["run_id"] == manifest.cells[0].run_id
    )
    assert conflicted["reason_code"] == "LEDGER_TERMINAL_CONFLICT"


def test_partial_analysis_rejects_a_substituted_bundle(tmp_path: Path) -> None:
    """Audit regression: a file that belongs to another cell must never be
    scored as this repeat — only the manifest digest was checked before, so
    swapping in another scenario's evaluation produced a SCORED trial."""

    manifest = build_manifest("a" * 40)
    suite_root, artifact_root = _write_fixture(tmp_path, manifest=manifest)
    target = manifest.cells[3]
    other_case = next(
        cell for cell in manifest.cells if cell.incident_case_id != target.incident_case_id
    )
    sibling = next(
        cell
        for cell in manifest.cells
        if cell.incident_case_id == target.incident_case_id
        and cell.strategy is target.strategy
        and cell.repeat_index != target.repeat_index
    )
    target_dir = artifact_root / target.run_id
    foreign_evaluation = (artifact_root / other_case.run_id / "evaluation.json").read_text(
        encoding="utf-8"
    )
    foreign_diagnosis = (artifact_root / other_case.run_id / "diagnosis.json").read_text(
        encoding="utf-8"
    )
    sibling_evaluation = (artifact_root / sibling.run_id / "evaluation.json").read_text(
        encoding="utf-8"
    )

    substitutions = (
        ("evaluation.json", foreign_evaluation),
        ("evaluation.json", sibling_evaluation),
        ("diagnosis.json", foreign_diagnosis),
    )
    for name, payload in substitutions:
        path = target_dir / name
        original = path.read_text(encoding="utf-8")
        path.write_text(payload, encoding="utf-8")

        analysis = analyze_partial_suite(manifest, suite_root)

        record = next(cell for cell in analysis["cells"] if cell["run_id"] == target.run_id)
        assert record["state"] == "MISSING", name
        assert record["reason_code"] == "IDENTITY_MISMATCH", name
        assert record.get("passed") is None
        group = next(
            item
            for item in analysis["reliability"]["groups"]
            if item["group_id"] == f"{target.incident_case_id}/{target.strategy.value}"
        )
        assert group["complete"] is False
        assert target.repeat_index in group["missing_repeat_indices"]
        path.write_text(original, encoding="utf-8")


def test_partial_analysis_rejects_a_ledger_identity_mismatch(tmp_path: Path) -> None:
    manifest = build_manifest("a" * 40)
    suite_root, _ = _write_fixture(tmp_path, manifest=manifest)
    target = manifest.cells[0]
    other_case = next(
        cell for cell in manifest.cells if cell.incident_case_id != target.incident_case_id
    )
    ledger = suite_root / "ledger.jsonl"
    lines = ledger.read_text(encoding="utf-8").splitlines()
    rewritten = []
    for line in lines:
        entry = json.loads(line)
        if entry["run_id"] == target.run_id and entry["state"] in {"COMPLETED", "FAILED"}:
            entry["incident_case_id"] = other_case.incident_case_id
        rewritten.append(json.dumps(entry))
    ledger.write_text("\n".join(rewritten) + "\n", encoding="utf-8")

    analysis = analyze_partial_suite(manifest, suite_root)

    record = next(cell for cell in analysis["cells"] if cell["run_id"] == target.run_id)
    assert record["state"] == "MISSING"
    assert record["reason_code"] == "IDENTITY_MISMATCH"


def test_partial_analysis_survives_a_structurally_broken_file(tmp_path: Path) -> None:
    """Audit regression: a cell whose file parses to the wrong JSON shape must
    leave that one cell unscored instead of aborting the whole analysis."""

    manifest = build_manifest("a" * 40)
    suite_root, artifact_root = _write_fixture(tmp_path, manifest=manifest)
    target = manifest.cells[0]
    (artifact_root / target.run_id / "metadata.json").write_text("[]\n", encoding="utf-8")

    analysis = analyze_partial_suite(manifest, suite_root)

    record = next(cell for cell in analysis["cells"] if cell["run_id"] == target.run_id)
    assert record["state"] == "MISSING"
    assert record["reason_code"] == "ARTIFACTS_INVALID"
    # The rest of the suite is still analysed: only this repeat is affected.
    # The partial entry point covers every strategy, so all 58 scheduled groups
    # are reported and exactly one of them lost a trial.
    assert analysis["reliability"]["groups_total"] == 58
    assert analysis["reliability"]["groups_complete"] == 57
    assert analysis["reliability"]["groups_incomplete"] == 1


def test_reporter_fails_closed_when_result_inputs_drift(tmp_path: Path) -> None:
    """Claim metrics re-derive evaluator verdicts, so a report built with an
    evaluator (or schema, or profile spec) other than the frozen one must fail
    closed instead of mixing identities."""

    manifest = build_manifest("a" * 40)
    suite_root, _ = _write_fixture(tmp_path, manifest=manifest)
    drifted = manifest.model_copy(
        update={
            "result_inputs": manifest.result_inputs.model_copy(
                update={"evaluator_sha256": "d" * 64}
            )
        }
    )

    assert drifted.result_inputs != manifest.result_inputs
    with pytest.raises(BenchmarkReportError, match="result inputs"):
        BenchmarkReporter(drifted, suite_root).write()


def test_reporter_fails_closed_when_a_scenario_contract_drifted(tmp_path: Path) -> None:
    """A cell whose contract no longer matches the frozen catalog digest cannot
    be scored, because the claim verdicts would be computed against a different
    contract than the run answered."""

    manifest = build_manifest("a" * 40)
    target = manifest.cells[0].incident_case_id
    catalog = tuple(
        entry.model_copy(update={"scenario_spec_sha256": "e" * 64})
        if entry.incident_case_id == target
        else entry
        for entry in manifest.scenario_catalog
    )
    drifted = manifest.model_copy(update={"scenario_catalog": catalog})
    suite_root, _ = _write_fixture(tmp_path, manifest=drifted)

    with pytest.raises(BenchmarkReportError, match="scenario contract drifted"):
        BenchmarkReporter(drifted, suite_root).write()
