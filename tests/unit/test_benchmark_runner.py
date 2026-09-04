from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from data_incident_gym.artifacts import ARTIFACT_FILENAMES, ArtifactWriter
from data_incident_gym.benchmark_manifest import build_manifest
from data_incident_gym.benchmark_runner import (
    BenchmarkCellSelector,
    BenchmarkDoctorReceipt,
    BenchmarkLedgerEntry,
    BenchmarkRunner,
    BenchmarkRunnerError,
    is_receipt_acceptable,
)
from data_incident_gym.config import Settings
from data_incident_gym.diagnosis import DiagnosticStrategy
from data_incident_gym.diagnostic_config import DiagnosticSettings
from data_incident_gym.doctor import DoctorCheckCode, DoctorResult, DoctorRunner, DoctorStatus
from data_incident_gym.evaluation import (
    EvaluationApplicability,
    EvaluationCheck,
    EvaluationCheckCode,
    EvaluationResult,
    EvaluationStatus,
)
from data_incident_gym.evaluation_runner import (
    EvaluationAttemptResult,
    EvaluationRunner,
    EvaluationWorkflowError,
)

NOW = datetime(2026, 9, 1, tzinfo=UTC)
MODEL_CHECKS = frozenset(
    {
        DoctorCheckCode.MODEL_ENDPOINT,
        DoctorCheckCode.MODEL_PRESENT,
        DoctorCheckCode.MODEL_TOOL_STRUCTURED_OUTPUT,
    }
)


def _doctor_result(passed: bool = True) -> DoctorResult:
    checks = tuple(
        DoctorRunner._check(code, passed, "OK" if passed else "UNAVAILABLE")
        for code in DoctorCheckCode
    )
    return DoctorResult(
        status=DoctorStatus.PASSED if passed else DoctorStatus.FAILED,
        checks=checks,
    )


def _doctor_without_model() -> DoctorResult:
    return DoctorResult(
        status=DoctorStatus.FAILED,
        checks=tuple(
            DoctorRunner._check(
                code,
                code not in MODEL_CHECKS,
                "OK" if code not in MODEL_CHECKS else "UNAVAILABLE",
            )
            for code in DoctorCheckCode
        ),
    )


def _scoped_receipt(
    *,
    manifest_id: str = "p1-formal-v3",
    model_probe_required: bool,
    cell_selector: BenchmarkCellSelector | None = None,
) -> BenchmarkDoctorReceipt:
    return BenchmarkDoctorReceipt(
        manifest_id=manifest_id,
        manifest_sha256="a" * 64,
        implementation_revision="a" * 40,
        checkout_revision="a" * 40,
        result_inputs_sha256="b" * 64,
        cell_selector=cell_selector,
        model_probe_required=model_probe_required,
        checked_at=NOW,
        result=_doctor_without_model(),
    )


def test_fixed_rule_receipt_is_acceptable_without_model_probe() -> None:
    fixed_rule_receipt = _scoped_receipt(model_probe_required=False)

    assert fixed_rule_receipt.model_probe_required is False
    assert fixed_rule_receipt.result.status is DoctorStatus.FAILED
    assert is_receipt_acceptable(fixed_rule_receipt) is True


def test_model_backed_receipt_requires_full_doctor_pass() -> None:
    model_receipt_with_failed_probe = _scoped_receipt(model_probe_required=True)

    assert model_receipt_with_failed_probe.model_probe_required is True
    assert is_receipt_acceptable(model_receipt_with_failed_probe) is False


def test_receipt_rejects_scope_mismatch_against_selected_cells(
    tmp_path: Path,
) -> None:
    manifest = build_manifest("a" * 40, manifest_id="p1-formal-v3")
    runner_with_fixed_rule_selector = _runner(
        manifest,
        tmp_path,
        doctor_result=_doctor_without_model(),
        calls=[],
        doctor_calls=[],
        cell_selector=BenchmarkCellSelector(
            manifest_id=manifest.manifest_id,
            strategies=(DiagnosticStrategy.FIXED_RULE,),
        ),
    )
    model_backed_receipt = _scoped_receipt(model_probe_required=True)

    with pytest.raises(BenchmarkRunnerError, match="scope"):
        runner_with_fixed_rule_selector.assert_receipt_scope(model_backed_receipt)


def test_receipt_rejects_different_fixed_only_selector_even_without_model_probe(
    tmp_path: Path,
) -> None:
    manifest = build_manifest("a" * 40, manifest_id="p1-formal-v3")
    first_selector = BenchmarkCellSelector(
        manifest_id=manifest.manifest_id,
        sequences=(manifest.cells[-1].sequence,),
    )
    second_selector = BenchmarkCellSelector(
        manifest_id=manifest.manifest_id,
        sequences=(manifest.cells[-2].sequence,),
    )
    runner = _runner(
        manifest,
        tmp_path,
        doctor_result=_doctor_without_model(),
        calls=[],
        doctor_calls=[],
        cell_selector=first_selector,
    )

    with pytest.raises(BenchmarkRunnerError, match="scope"):
        runner.assert_receipt_scope(
            _scoped_receipt(
                manifest_id=manifest.manifest_id,
                model_probe_required=False,
                cell_selector=second_selector,
            )
        )


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


class _FakeDoctor:
    def __init__(self, result: DoctorResult, calls: list[str]) -> None:
        self._result = result
        self._calls = calls

    async def run(self) -> DoctorResult:
        self._calls.append("doctor")
        return self._result


class _FakeEvaluationRunner:
    def __init__(self, calls: list[tuple[str, DiagnosticStrategy, str]]) -> None:
        self._calls = calls

    async def run(
        self,
        case_id: str,
        strategy: DiagnosticStrategy,
        *,
        run_id: str,
    ) -> EvaluationAttemptResult:
        self._calls.append((case_id, strategy, run_id))
        return EvaluationAttemptResult(
            incident_case_id=case_id,
            run_id=run_id,
            status=EvaluationStatus.PASSED,
            evaluation=_evaluation(case_id, run_id),
            artifact_dir=Path("artifacts") / run_id,
        )


class _FakeWriter:
    def __init__(self) -> None:
        self.runs = []

    def write(self, run: object) -> Path:
        self.runs.append(run)
        return Path("artifacts") / run.run_id


def _evaluation_with_gate_failure(
    case_id: str,
    run_id: str,
    failed_code: EvaluationCheckCode,
) -> EvaluationResult:
    base = _evaluation(case_id, run_id)
    checks = []
    for check in base.checks:
        if check.code is EvaluationCheckCode.ENVIRONMENT_VERIFIED:
            passed = failed_code is not check.code
            update = {
                "applicability": EvaluationApplicability.APPLICABLE,
                "passed": passed,
                "expected": ("PRIVATE_SCENARIO_VERIFIED",),
                "actual": ("VERIFIED",) if passed else ("INVALID",),
                "reason_code": f"{check.code.value}_{'PASSED' if passed else 'FAILED'}",
            }
        elif check.code is EvaluationCheckCode.RECOVERY_HEALTHY:
            passed = failed_code is not check.code
            update = {
                "applicability": EvaluationApplicability.APPLICABLE,
                "passed": passed,
                "expected": ("RECOVERY_HEALTHY",),
                "actual": ("HEALTHY",) if passed else ("FAILED",),
                "reason_code": f"{check.code.value}_{'PASSED' if passed else 'FAILED'}",
            }
        elif check.code is failed_code:
            update = {
                "applicability": EvaluationApplicability.APPLICABLE,
                "passed": False,
                "expected": ("EXPECTED",),
                "actual": ("ACTUAL",),
                "reason_code": f"{check.code.value}_FAILED",
            }
        else:
            checks.append(check)
            continue
        checks.append(check.model_copy(update=update))
    checks_tuple = tuple(checks)
    failed = tuple(check.code for check in checks_tuple if not check.passed)
    return EvaluationResult(
        incident_case_id=case_id,
        run_id=run_id,
        status=EvaluationStatus.FAILED if failed else EvaluationStatus.PASSED,
        checks=checks_tuple,
        failed_check_codes=failed,
        answerability="UNAVAILABLE",
        expected_status="UNAVAILABLE",
    )


class _ScriptedEvaluationRunner:
    def __init__(self, actions: list[object]) -> None:
        self.actions = actions
        self.calls: list[tuple[str, DiagnosticStrategy, str]] = []

    async def run(
        self,
        case_id: str,
        strategy: DiagnosticStrategy,
        *,
        run_id: str,
    ) -> EvaluationAttemptResult:
        self.calls.append((case_id, strategy, run_id))
        action = self.actions.pop(0)
        if isinstance(action, BaseException):
            raise action
        return EvaluationAttemptResult(
            incident_case_id=case_id,
            run_id=run_id,
            status=action.status,
            evaluation=action,
            artifact_dir=Path("artifacts") / run_id,
        )


def _runner(
    manifest,
    tmp_path: Path,
    *,
    doctor_result: DoctorResult,
    calls: list[tuple[str, DiagnosticStrategy, str]],
    doctor_calls: list[str],
    writer: object | None = None,
    evaluation_runner_factory: object | None = None,
    cell_selector: BenchmarkCellSelector | None = None,
) -> BenchmarkRunner:
    return BenchmarkRunner(
        manifest,
        project_root=tmp_path,
        doctor_factory=lambda: _FakeDoctor(doctor_result, doctor_calls),
        evaluation_runner_factory=evaluation_runner_factory
        or (lambda: _FakeEvaluationRunner(calls)),
        artifact_writer=writer or _FakeWriter(),
        clock=lambda: NOW,
        checkout_verifier=lambda _manifest: None,
        checkout_revision_reader=lambda: "a" * 40,
        cell_selector=cell_selector,
    )


def test_cell_selector_filters_by_strategy() -> None:
    manifest = build_manifest("a" * 40)
    selector = BenchmarkCellSelector(
        manifest_id=manifest.manifest_id,
        strategies=(DiagnosticStrategy.FIXED_RULE,),
    )

    selected = selector.select(manifest.cells)

    assert len(selected) == 12
    assert all(cell.strategy is DiagnosticStrategy.FIXED_RULE for cell in selected)
    assert all(cell.model_backed is False for cell in selected)


def test_cell_selector_filters_by_sequence() -> None:
    manifest = build_manifest("a" * 40)
    selector = BenchmarkCellSelector(
        manifest_id=manifest.manifest_id, sequences=(1, 2, 3)
    )

    selected = selector.select(manifest.cells)

    assert [cell.sequence for cell in selected] == [1, 2, 3]


def test_cell_selector_rejects_empty_duplicate_and_bad_sequence() -> None:
    with pytest.raises(ValidationError):
        BenchmarkCellSelector(manifest_id="p1-formal-v2")
    with pytest.raises(ValidationError):
        BenchmarkCellSelector(
            manifest_id="p1-formal-v2",
            strategies=(
                DiagnosticStrategy.FIXED_RULE,
                DiagnosticStrategy.FIXED_RULE,
            ),
        )
    with pytest.raises(ValidationError):
        BenchmarkCellSelector(manifest_id="p1-formal-v2", sequences=(0,))


def test_runner_rejects_selector_bound_to_another_manifest(
    tmp_path: Path,
) -> None:
    manifest = build_manifest("a" * 40)
    selector = BenchmarkCellSelector(
        manifest_id=(
            "p1-formal-v4"
            if manifest.manifest_id != "p1-formal-v4"
            else "p1-formal-v3"
        ),
        strategies=(DiagnosticStrategy.FIXED_RULE,),
    )

    with pytest.raises(BenchmarkRunnerError):
        _runner(
            manifest,
            tmp_path,
            doctor_result=_doctor_result(),
            calls=[],
            doctor_calls=[],
            cell_selector=selector,
        )


def test_runner_rejects_unapproved_manifest_identity_before_setup(tmp_path: Path) -> None:
    manifest = build_manifest("a" * 40).model_copy(update={"manifest_id": "p1-formal-v999"})

    with pytest.raises(BenchmarkRunnerError, match="approved formal identity"):
        _runner(
            manifest,
            tmp_path,
            doctor_result=_doctor_result(),
            calls=[],
            doctor_calls=[],
        )


def test_full_runner_rejects_existing_subset_marker(tmp_path: Path) -> None:
    manifest = build_manifest("a" * 40)
    runner = _runner(
        manifest,
        tmp_path,
        doctor_result=_doctor_result(),
        calls=[],
        doctor_calls=[],
    )
    asyncio.run(runner.preflight())
    (runner._suite_root() / "subset.json").write_text(
        "{}\n",
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(BenchmarkRunnerError, match="marked as a subset"):
        asyncio.run(runner.run())


def test_subset_runner_executes_selected_cells_and_records_marker(tmp_path: Path) -> None:
    manifest = build_manifest("a" * 40)
    selector = BenchmarkCellSelector(
        manifest_id=manifest.manifest_id,
        strategies=(DiagnosticStrategy.FIXED_RULE,),
    )
    calls: list[tuple[str, DiagnosticStrategy, str]] = []
    runner = _runner(
        manifest,
        tmp_path,
        doctor_result=_doctor_result(),
        calls=calls,
        doctor_calls=[],
        cell_selector=selector,
    )

    asyncio.run(runner.preflight())
    result = asyncio.run(runner.run())

    selected = tuple(
        cell for cell in manifest.cells if cell.strategy is DiagnosticStrategy.FIXED_RULE
    )
    marker = runner._suite_root() / "subset.json"
    assert result.status == "COMPLETED"
    assert result.subset is True
    assert result.model_probe_required is False
    assert len(calls) == len(selected) == 12
    assert [item[2] for item in calls] == [cell.run_id for cell in selected]
    assert marker.read_text(encoding="utf-8") == selector.model_dump_json(indent=2) + "\n"


def test_subset_scope_failure_does_not_leave_marker(tmp_path: Path) -> None:
    manifest = build_manifest("a" * 40, manifest_id="p1-formal-v3")
    selector = BenchmarkCellSelector(
        manifest_id=manifest.manifest_id,
        strategies=(DiagnosticStrategy.FIXED_RULE,),
    )
    runner = _runner(
        manifest,
        tmp_path,
        doctor_result=_doctor_without_model(),
        calls=[],
        doctor_calls=[],
        cell_selector=selector,
    )
    asyncio.run(runner.preflight())
    outside = manifest.cells[0]
    entries = (
        BenchmarkLedgerEntry.create(
            manifest_id=manifest.manifest_id,
            sequence=outside.sequence,
            run_id=outside.run_id,
            incident_case_id=outside.incident_case_id,
            strategy=outside.strategy,
            state="STARTED",
            now=NOW,
            started_at=NOW,
        ),
        BenchmarkLedgerEntry.create(
            manifest_id=manifest.manifest_id,
            sequence=outside.sequence,
            run_id=outside.run_id,
            incident_case_id=outside.incident_case_id,
            strategy=outside.strategy,
            state="COMPLETED",
            now=NOW,
            started_at=NOW,
        ),
    )
    (runner._suite_root() / "ledger.jsonl").write_text(
        "\n".join(entry.model_dump_json() for entry in entries) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(BenchmarkRunnerError, match="outside the selected scope"):
        asyncio.run(runner.run())
    assert not (runner._suite_root() / "subset.json").exists()


def test_unstarted_artifact_failure_does_not_leave_marker(tmp_path: Path) -> None:
    manifest = build_manifest("a" * 40, manifest_id="p1-formal-v3")
    selector = BenchmarkCellSelector(
        manifest_id=manifest.manifest_id,
        strategies=(DiagnosticStrategy.FIXED_RULE,),
    )
    runner = _runner(
        manifest,
        tmp_path,
        doctor_result=_doctor_without_model(),
        calls=[],
        doctor_calls=[],
        cell_selector=selector,
    )
    asyncio.run(runner.preflight())
    (tmp_path / "artifacts" / manifest.cells[0].run_id).mkdir(parents=True)

    with pytest.raises(BenchmarkRunnerError, match="unstarted cell"):
        asyncio.run(runner.run())
    assert not (runner._suite_root() / "subset.json").exists()


def test_runner_verifies_before_doctor_and_executes_each_cell_once(tmp_path: Path) -> None:
    manifest = build_manifest("a" * 40)
    calls: list[tuple[str, DiagnosticStrategy, str]] = []
    doctor_calls: list[str] = []
    runner = _runner(
        manifest,
        tmp_path,
        doctor_result=_doctor_result(),
        calls=calls,
        doctor_calls=doctor_calls,
    )

    __import__("asyncio").run(runner.preflight())
    result = __import__("asyncio").run(runner.run())

    assert result.status == "COMPLETED"
    assert result.terminal_cells == 106
    assert result.completed_cells == 106
    assert result.failed_cells == 0
    assert len(calls) == 106
    assert [item[2] for item in calls] == [cell.run_id for cell in manifest.cells]
    assert sum(strategy is DiagnosticStrategy.FIXED_RULE for _, strategy, _ in calls) == 12
    ledger_path = tmp_path / "artifacts" / "benchmarks" / "p1-formal-v1" / "ledger.jsonl"
    ledger_lines = ledger_path.read_text(encoding="utf-8").splitlines()
    assert len(ledger_lines) == 212
    assert doctor_calls == ["doctor"]


def test_runner_stops_after_setup_error_and_records_stage_and_recovery(tmp_path: Path) -> None:
    manifest = build_manifest("a" * 40)
    scripted = _ScriptedEvaluationRunner(
        [EvaluationWorkflowError("BUILD_FAILED", recovery_succeeded=True)]
    )
    writer = _FakeWriter()
    runner = _runner(
        manifest,
        tmp_path,
        doctor_result=_doctor_result(),
        calls=[],
        doctor_calls=[],
        writer=writer,
        evaluation_runner_factory=lambda: scripted,
    )

    asyncio.run(runner.preflight())
    result = asyncio.run(runner.run())

    assert result.status == "FAILED"
    assert result.terminal_cells == 1
    assert result.completed_cells == 0
    assert result.failed_cells == 1
    assert len(scripted.calls) == 1
    assert len(writer.runs) == 1
    setup_run = writer.runs[0]
    environment = next(
        check
        for check in setup_run.evaluation.checks
        if check.code is EvaluationCheckCode.ENVIRONMENT_VERIFIED
    )
    recovery = next(
        check
        for check in setup_run.evaluation.checks
        if check.code is EvaluationCheckCode.RECOVERY_HEALTHY
    )
    assert environment.actual == ("BUILD_FAILED",)
    assert recovery.passed is True
    assert all(
        check.applicability is EvaluationApplicability.NOT_APPLICABLE
        for check in setup_run.evaluation.checks
        if check.code
        not in {
            EvaluationCheckCode.ENVIRONMENT_VERIFIED,
            EvaluationCheckCode.RECOVERY_HEALTHY,
        }
    )
    assert setup_run.diagnosis_run.trace[0].reason_code == "BUILD_FAILED"
    ledger = runner._suite_root() / "ledger.jsonl"
    assert len(ledger.read_text(encoding="utf-8").splitlines()) == 2


def test_runner_continues_after_quality_failure_with_healthy_gates(tmp_path: Path) -> None:
    manifest = build_manifest("b" * 40)
    scripted = _ScriptedEvaluationRunner(
        [
            _evaluation_with_gate_failure(
                manifest.cells[0].incident_case_id,
                manifest.cells[0].run_id,
                EvaluationCheckCode.STATUS_EXACT,
            ),
            *(
                _evaluation(cell.incident_case_id, cell.run_id)
                for cell in manifest.cells[1:]
            ),
        ]
    )
    runner = _runner(
        manifest,
        tmp_path,
        doctor_result=_doctor_result(),
        calls=[],
        doctor_calls=[],
        evaluation_runner_factory=lambda: scripted,
    )

    asyncio.run(runner.preflight())
    result = asyncio.run(runner.run())

    assert result.status == "FAILED"
    assert result.terminal_cells == 106
    assert result.completed_cells == 105
    assert result.failed_cells == 1
    assert len(scripted.calls) == 106


def test_runner_stops_after_applicable_environment_failure(tmp_path: Path) -> None:
    manifest = build_manifest("c" * 40)
    scripted = _ScriptedEvaluationRunner(
        [
            _evaluation_with_gate_failure(
                manifest.cells[0].incident_case_id,
                manifest.cells[0].run_id,
                EvaluationCheckCode.ENVIRONMENT_VERIFIED,
            )
        ]
    )
    runner = _runner(
        manifest,
        tmp_path,
        doctor_result=_doctor_result(),
        calls=[],
        doctor_calls=[],
        evaluation_runner_factory=lambda: scripted,
    )

    asyncio.run(runner.preflight())
    result = asyncio.run(runner.run())

    assert result.status == "FAILED"
    assert result.terminal_cells == 1
    assert result.failed_cells == 1
    assert len(scripted.calls) == 1


def test_preflight_runs_doctor_and_writes_receipt_before_execution(tmp_path: Path) -> None:
    manifest = build_manifest("a" * 40)
    calls: list[tuple[str, DiagnosticStrategy, str]] = []
    doctor_calls: list[str] = []
    runner = _runner(
        manifest,
        tmp_path,
        doctor_result=_doctor_result(),
        calls=calls,
        doctor_calls=doctor_calls,
    )

    receipt = __import__("asyncio").run(runner.preflight())

    assert receipt.result.status is DoctorStatus.PASSED
    assert receipt.model_probe_required is True
    assert doctor_calls == ["doctor"]
    suite_root = tmp_path / "artifacts" / "benchmarks" / manifest.manifest_id
    receipt = suite_root / "doctor.json"
    assert receipt.is_file()
    assert not tuple(suite_root.glob(".doctor.json.*.tmp"))
    assert not (suite_root / "ledger.jsonl").exists()
    assert not calls


def test_runner_requires_a_passed_preflight_receipt(tmp_path: Path) -> None:
    manifest = build_manifest("b" * 40)
    calls: list[tuple[str, DiagnosticStrategy, str]] = []
    doctor_calls: list[str] = []
    runner = _runner(
        manifest,
        tmp_path,
        doctor_result=_doctor_result(),
        calls=calls,
        doctor_calls=doctor_calls,
    )

    with pytest.raises(BenchmarkRunnerError, match="preflight receipt"):
        __import__("asyncio").run(runner.run())

    assert doctor_calls == []
    assert calls == []


def test_preflight_rejects_existing_target_artifact(tmp_path: Path) -> None:
    manifest = build_manifest("c" * 40)
    calls: list[tuple[str, DiagnosticStrategy, str]] = []
    doctor_calls: list[str] = []
    artifact = tmp_path / "artifacts" / manifest.cells[0].run_id
    artifact.mkdir(parents=True)
    runner = _runner(
        manifest,
        tmp_path,
        doctor_result=_doctor_result(),
        calls=calls,
        doctor_calls=doctor_calls,
    )

    with pytest.raises(BenchmarkRunnerError, match="artifact already exists"):
        __import__("asyncio").run(runner.preflight())

    assert doctor_calls == []


def test_preflight_binds_and_run_rechecks_checkout_revision(tmp_path: Path) -> None:
    manifest = build_manifest("a" * 40)
    calls: list[tuple[str, DiagnosticStrategy, str]] = []
    doctor_calls: list[str] = []
    checkout_revision = {"value": "b" * 40}
    runner = BenchmarkRunner(
        manifest,
        project_root=tmp_path,
        doctor_factory=lambda: _FakeDoctor(_doctor_result(), doctor_calls),
        evaluation_runner_factory=lambda: _FakeEvaluationRunner(calls),
        artifact_writer=_FakeWriter(),
        clock=lambda: NOW,
        checkout_verifier=lambda _manifest: None,
        checkout_revision_reader=lambda: checkout_revision["value"],
    )

    __import__("asyncio").run(runner.preflight())
    receipt_path = runner._suite_root() / "doctor.json"
    receipt = __import__("json").loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["checkout_revision"] == "b" * 40

    checkout_revision["value"] = "c" * 40
    with pytest.raises(BenchmarkRunnerError, match="checkout revision"):
        __import__("asyncio").run(runner.run())

    assert calls == []


def test_preflight_rejects_residual_suite_files(tmp_path: Path) -> None:
    manifest = build_manifest("6" * 40)
    calls: list[tuple[str, DiagnosticStrategy, str]] = []
    doctor_calls: list[str] = []
    runner = _runner(
        manifest,
        tmp_path,
        doctor_result=_doctor_result(),
        calls=calls,
        doctor_calls=doctor_calls,
    )
    suite_root = runner._suite_root()
    (suite_root / "summary.json").write_text("{}", encoding="utf-8")

    with pytest.raises(BenchmarkRunnerError, match="untouched benchmark suite"):
        __import__("asyncio").run(runner.preflight())

    assert doctor_calls == []


def test_run_rejects_receipt_with_drifted_result_inputs(tmp_path: Path) -> None:
    manifest = build_manifest("4" * 40)
    calls: list[tuple[str, DiagnosticStrategy, str]] = []
    doctor_calls: list[str] = []
    runner = _runner(
        manifest,
        tmp_path,
        doctor_result=_doctor_result(),
        calls=calls,
        doctor_calls=doctor_calls,
    )
    __import__("asyncio").run(runner.preflight())
    receipt_path = runner._suite_root() / "doctor.json"
    payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    payload["result_inputs_sha256"] = "0" * 64
    receipt_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(BenchmarkRunnerError, match="result inputs do not match manifest"):
        __import__("asyncio").run(runner.run())

    assert doctor_calls == ["doctor"]
    assert calls == []


def test_run_rejects_artifact_for_cell_that_never_started(tmp_path: Path) -> None:
    manifest = build_manifest("5" * 40)
    calls: list[tuple[str, DiagnosticStrategy, str]] = []
    doctor_calls: list[str] = []
    runner = _runner(
        manifest,
        tmp_path,
        doctor_result=_doctor_result(),
        calls=calls,
        doctor_calls=doctor_calls,
    )
    __import__("asyncio").run(runner.preflight())
    artifact = tmp_path / "artifacts" / manifest.cells[1].run_id
    artifact.mkdir(parents=True)

    with pytest.raises(BenchmarkRunnerError, match="artifact already exists"):
        __import__("asyncio").run(runner.run())

    assert calls == []


def test_runner_reuses_terminal_ledger_without_retry(tmp_path: Path) -> None:
    manifest = build_manifest("b" * 40)
    calls: list[tuple[str, DiagnosticStrategy, str]] = []
    doctor_calls: list[str] = []
    runner = _runner(
        manifest,
        tmp_path,
        doctor_result=_doctor_result(),
        calls=calls,
        doctor_calls=doctor_calls,
    )

    __import__("asyncio").run(runner.preflight())
    first = __import__("asyncio").run(runner.run())
    second = __import__("asyncio").run(runner.run())

    assert first.status == second.status == "COMPLETED"
    assert len(calls) == 106
    assert doctor_calls == ["doctor"]


def test_runner_doctor_failure_creates_no_started_cell(tmp_path: Path) -> None:
    manifest = build_manifest("c" * 40)
    calls: list[tuple[str, DiagnosticStrategy, str]] = []
    doctor_calls: list[str] = []
    runner = _runner(
        manifest,
        tmp_path,
        doctor_result=_doctor_result(False),
        calls=calls,
        doctor_calls=doctor_calls,
    )

    __import__("asyncio").run(runner.preflight())
    with pytest.raises(BenchmarkRunnerError, match="receipt is not acceptable"):
        __import__("asyncio").run(runner.run())

    assert calls == []
    suite_root = tmp_path / "artifacts" / "benchmarks" / "p1-formal-v1"
    assert (suite_root / "doctor.json").is_file()
    assert not (suite_root / "ledger.jsonl").exists()


def test_runner_materializes_an_interrupted_cell_once(tmp_path: Path) -> None:
    manifest = build_manifest("d" * 40)
    calls: list[tuple[str, DiagnosticStrategy, str]] = []
    doctor_calls: list[str] = []
    writer = _FakeWriter()
    runner = _runner(
        manifest,
        tmp_path,
        doctor_result=_doctor_result(),
        calls=calls,
        doctor_calls=doctor_calls,
        writer=writer,
    )
    __import__("asyncio").run(runner.preflight())
    raw_artifact = tmp_path / "artifacts" / manifest.cells[0].run_id
    raw_artifact.mkdir(parents=True)
    (raw_artifact / "partial-output.txt").write_text("incomplete", encoding="utf-8")
    suite_root = runner._suite_root()
    started = BenchmarkLedgerEntry.create(
        manifest_id=manifest.manifest_id,
        sequence=1,
        run_id=manifest.cells[0].run_id,
        incident_case_id=manifest.cells[0].incident_case_id,
        strategy=manifest.cells[0].strategy,
        state="STARTED",
        now=NOW,
        started_at=NOW,
    )
    runner._append_ledger(suite_root / "ledger.jsonl", started)

    result = __import__("asyncio").run(runner.run())

    assert result.status == "FAILED"
    assert result.failed_cells == 1
    assert len(calls) == 105
    assert len(writer.runs) == 1
    assert writer.runs[0].run_id == manifest.cells[0].run_id
    preserved = suite_root / "interrupted-artifacts" / manifest.cells[0].run_id
    assert (preserved / "partial-output.txt").read_text(encoding="utf-8") == "incomplete"


def test_runner_rejects_terminal_ledger_entry_without_started_predecessor(tmp_path: Path) -> None:
    manifest = build_manifest("0" * 40)
    runner = _runner(
        manifest,
        tmp_path,
        doctor_result=_doctor_result(),
        calls=[],
        doctor_calls=[],
    )
    suite_root = runner._suite_root()
    terminal = BenchmarkLedgerEntry.create(
        manifest_id=manifest.manifest_id,
        sequence=1,
        run_id=manifest.cells[0].run_id,
        incident_case_id=manifest.cells[0].incident_case_id,
        strategy=manifest.cells[0].strategy,
        state="FAILED",
        now=NOW,
        started_at=NOW,
        reason_code="RUN_SETUP_ERROR",
    )
    runner._append_ledger(suite_root / "ledger.jsonl", terminal)

    with pytest.raises(BenchmarkRunnerError, match="STARTED predecessor"):
        runner._read_ledger(suite_root / "ledger.jsonl")


def test_runner_rejects_duplicate_ledger_json_key(tmp_path: Path) -> None:
    manifest = build_manifest("e" * 40)
    runner = _runner(
        manifest,
        tmp_path,
        doctor_result=_doctor_result(),
        calls=[],
        doctor_calls=[],
    )
    suite_root = runner._suite_root()
    started = BenchmarkLedgerEntry.create(
        manifest_id=manifest.manifest_id,
        sequence=1,
        run_id=manifest.cells[0].run_id,
        incident_case_id=manifest.cells[0].incident_case_id,
        strategy=manifest.cells[0].strategy,
        state="STARTED",
        now=NOW,
        started_at=NOW,
    )
    ledger_path = suite_root / "ledger.jsonl"
    ledger_path.write_text(
        started.model_dump_json().replace(
            '"state":"STARTED"', '"state":"STARTED","state":"STARTED"', 1
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(BenchmarkRunnerError, match="ledger is invalid"):
        runner._read_ledger(ledger_path)


def test_runner_materializes_stale_cell_with_passed_doctor_receipt(tmp_path: Path) -> None:
    manifest = build_manifest("1" * 40)
    calls: list[tuple[str, DiagnosticStrategy, str]] = []
    doctor_calls: list[str] = []
    writer = _FakeWriter()
    runner = _runner(
        manifest,
        tmp_path,
        doctor_result=_doctor_result(),
        calls=calls,
        doctor_calls=doctor_calls,
        writer=writer,
    )
    __import__("asyncio").run(runner.preflight())
    suite_root = runner._suite_root()
    started = BenchmarkLedgerEntry.create(
        manifest_id=manifest.manifest_id,
        sequence=1,
        run_id=manifest.cells[0].run_id,
        incident_case_id=manifest.cells[0].incident_case_id,
        strategy=manifest.cells[0].strategy,
        state="STARTED",
        now=NOW,
        started_at=NOW,
    )
    runner._append_ledger(suite_root / "ledger.jsonl", started)

    result = __import__("asyncio").run(runner.run())

    assert result.status == "FAILED"
    assert result.terminal_cells == 106
    assert result.completed_cells == 105
    assert result.failed_cells == 1
    assert len(calls) == 105
    assert len(writer.runs) == 1
    assert len((suite_root / "ledger.jsonl").read_text(encoding="utf-8").splitlines()) == 212


def test_runner_binds_manifest_model_configuration_to_runtime_settings(tmp_path: Path) -> None:
    manifest = build_manifest(
        "2" * 40,
        model_base_url="https://manifest.example/v1",
    )
    diagnostic_settings = DiagnosticSettings(
        _env_file=None,
        model_base_url="https://runtime.example/v1",
        model_name="runtime-model",
    )

    runner = BenchmarkRunner.for_project(
        manifest,
        project_root=tmp_path,
        settings=Settings(_env_file=None),
        diagnostic_settings=diagnostic_settings,
    )

    evaluation_runner = runner._evaluation_runner_factory()

    assert isinstance(evaluation_runner, EvaluationRunner)
    assert evaluation_runner._diagnostic_settings.model_base_url == "https://manifest.example/v1"
    assert evaluation_runner._diagnostic_settings.model_name == "mimo-v2.5"


def test_setup_error_materialization_writes_the_canonical_six_files(tmp_path: Path) -> None:
    manifest = build_manifest("e" * 40)

    def git_command(argv: list[str], **_: object):
        import subprocess

        stdout = "1" * 40 + "\n" if argv[-2:] == ["rev-parse", "HEAD"] else ""
        return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr="")

    writer = ArtifactWriter(tmp_path, run_command=git_command)
    runner = _runner(
        manifest,
        tmp_path,
        doctor_result=_doctor_result(),
        calls=[],
        doctor_calls=[],
        writer=writer,
    )

    output = runner._materialize_setup_error(
        manifest.cells[0],
        started_at=NOW,
    )

    assert tuple(sorted(path.name for path in output.iterdir())) == tuple(
        sorted(ARTIFACT_FILENAMES)
    )
    assert output.name == manifest.cells[0].run_id


def test_runner_rejects_checkout_before_doctor(tmp_path: Path) -> None:
    manifest = build_manifest("f" * 40)
    calls: list[tuple[str, DiagnosticStrategy, str]] = []
    doctor_calls: list[str] = []

    def reject(_manifest) -> None:
        raise BenchmarkRunnerError("input drift")

    runner = BenchmarkRunner(
        manifest,
        project_root=tmp_path,
        doctor_factory=lambda: _FakeDoctor(_doctor_result(), doctor_calls),
        evaluation_runner_factory=lambda: _FakeEvaluationRunner(calls),
        artifact_writer=_FakeWriter(),
        clock=lambda: NOW,
        checkout_verifier=reject,
    )

    import asyncio

    try:
        asyncio.run(runner.run())
    except BenchmarkRunnerError as error:
        assert str(error) == "input drift"
    else:
        raise AssertionError("runner should reject a drifted checkout")
    assert doctor_calls == []
    assert calls == []
